"""
Wedge Agent — reads raw candidate source text and uses an LLM to write
Likert statements with a per-candidate agreement score.

Two steps, with guardrails (all at generation time, nothing per user):
  1. Positions. One call per candidate lists the positions their sources
     state explicitly, each with a verbatim quote. Positions whose quote
     isn't found word for word in the sources are dropped. A quote that
     joins sentences from different parts of one page is kept only if each
     sentence is word for word on that page, and is shown with "…".
  2. Statements. One call writes up to 9 statements, one per topic, and
     must cite a checked position for every score. Quotes are attached by
     code from the cited position, never copied by the model.
  3. Gap filling. A second call checks every blank score against that
     candidate's positions, so a stated position isn't missed just because
     the statement was written around another candidate.

Then:
  - Unknown is not neutral: no cited position → null, "Not enough public
    information", never a 3.
  - A second-pass check (skip with --no-verify) drops scores their quote
    doesn't support, and flags summaries that add claims and loaded wording.
  - Statements may score one candidate (their stated position) or several.
    Silence is never scored. Statements with no scored candidate are dropped.
  - Equal coverage: no candidate is scored on more than one statement more
    than any other, so whoever has more web pages doesn't dominate.
  - Direction and thin-data checks.

Anything flagged or dropped is listed under "flags" in the output file.

Outputs JSON to data/processed/statements_ballot_<STATE>.json: one question
set per state's ballot, so shared topics are asked once across offices.

Usage:
    export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY
    export ZIPVOTE_MODEL=...       # optional: override the default model

    python -m agents.wedge
    python -m agents.wedge --no-verify
    python -m agents.wedge --positions ca_gov_primary   # step 1 + overlap only
    python -m agents.wedge --race ca_gov_primary        # one race: full pool + core
"""

import argparse
import copy
import hashlib
import json
import subprocess
import os
import sys
import threading
import time
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from statistics import median
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agents.config import RACES, CANDIDATES

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RACE_TITLES = {r["id"]: r["title"] for r in RACES}
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"


def cache_key(*parts: str) -> str:
    """Fingerprint of everything that affects a result: source text, prompt, model."""
    import hashlib
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def cache_get(name: str, key: str):
    path = CACHE_DIR / f"{name}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            return saved["value"]
    return None


def cache_put(name: str, key: str, value) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{name}.json").write_text(
        json.dumps({"key": key, "value": value}, indent=2, ensure_ascii=False), encoding="utf-8")
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

EXCERPT_BUDGET_CHARS = 36_000   # per candidate, roughly 9k tokens (about 6,000 words)
MIN_QUOTE_CHARS = 25
UNKNOWN_POSITION = "Not enough public information."

SYSTEM_PROMPT = (
    "You are a nonpartisan civic researcher. You only state what the provided "
    "source text supports, and you say plainly when it does not. You never "
    "infer a position from party membership."
)


# ---------------------------------------------------------------------------
# LLM client — picks a provider from whichever API key is set
# ---------------------------------------------------------------------------

# Two tiers: a strong model for judgment (writing statements, checking them)
# and a fast, cheaper model for extraction (listing positions, filling gaps,
# bios), where code already verifies every quote word for word.
DEFAULT_MODELS = {
    "ANTHROPIC_API_KEY": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "OPENAI_API_KEY": ("gpt-4o", "gpt-4o-mini"),
    "GEMINI_API_KEY": ("gemini-2.5-pro", "gemini-2.5-flash"),
}


def active_model(fast: bool = False) -> str:
    for key, (strong, cheap) in DEFAULT_MODELS.items():
        if os.environ.get(key):
            if fast:
                return os.environ.get("ZIPVOTE_MODEL_FAST", cheap)
            return os.environ.get("ZIPVOTE_MODEL", strong)
    raise EnvironmentError(
        "No LLM API key found. Put ANTHROPIC_API_KEY in ZipVote/.env (see .env.example), "
        "or set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY in your shell."
    )


# Each step's model tier and effort (how hard Claude 5+ thinks before
# answering; thinking is billed as output). Every step runs on the fast,
# cheap model so a whole state costs a few dollars. The guardrails that
# cost nothing stay in code: quotes are matched word for word, evidence is
# attached by code, a candidate who disagrees makes a statement contested.
# The verify step runs at high effort, since it's the model's one check.
# Override one step's effort with ZIPVOTE_EFFORT_<STEP> (low, medium, high).
STEPS = {
    "positions":  ("fast", "low"),      # quotes are checked word for word by code
    "bios":       ("fast", "low"),
    "write":      ("fast", "medium"),   # drafts; verify checks every one
    "rollup":     ("fast", "medium"),
    "fill":       ("fast", "medium"),
    "flip":       ("fast", "medium"),
    "rewrite":    ("fast", "medium"),
    "topup":      ("fast", "medium"),
    "statements": ("fast", "medium"),   # ballot mode's single writing call
    "group":      ("fast", "medium"),   # one call that sorts the whole field
    "verify":     ("fast", "high"),     # the model's check on every score and statement
}


def step_settings(step: str = None, fast: bool = False) -> tuple:
    """(model, effort) for a step. Unnamed calls fall back to fast or strong."""
    tier, effort = STEPS.get(step, ("fast" if fast else "strong", "low" if fast else "high"))
    if step:
        effort = os.environ.get(f"ZIPVOTE_EFFORT_{step.upper()}", effort)
    return active_model(tier == "fast"), effort


# What each model costs, in USD per million tokens (input, output), from
# platform.claude.com/docs/en/about-claude/pricing. Thinking is output.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),        # prompts over 100K tokens: 0.50 / 2.50
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def price(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = PRICES.get(model, (0.0, 0.0))
    if model == "claude-haiku-5-5" and tokens_in > 100_000:
        rate_in, rate_out = 0.50, 2.50
    return (tokens_in * rate_in + tokens_out * rate_out) / 1e6


class Meter:
    """Tokens and cost per step, across threads. Saved answers reused from the cache cost nothing."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}

    def add(self, step, model, tokens_in, tokens_out):
        with self.lock:
            r = self.rows.setdefault((step or "other", model), [0, 0, 0, 0.0])
            r[0] += 1
            r[1] += tokens_in
            r[2] += tokens_out
            r[3] += price(model, tokens_in, tokens_out)

    def summary(self) -> dict:
        rows = [{"step": st, "model": m, "calls": c, "input_tokens": i, "output_tokens": o, "usd": round(u, 4)}
                for (st, m), (c, i, o, u) in sorted(self.rows.items(), key=lambda kv: -kv[1][3])]
        return {"steps": rows, "total_usd": round(sum(r["usd"] for r in rows), 4)}

    def print(self):
        sm = self.summary()
        if not sm["steps"]:
            print("   💵 No model calls (everything reused from saved answers)")
            return
        print("   💵 Cost by step:")
        for r in sm["steps"]:
            print(f"      {r['step']:<11} {r['model']:<18} {r['calls']:>3} calls  "
                  f"{r['input_tokens'] / 1000:>7.1f}k in  {r['output_tokens'] / 1000:>7.1f}k out  ${r['usd']:.2f}")
        print(f"      {'total':<11} {'':<18} {'':>9}  {'':>10}  {'':>11}  ${sm['total_usd']:.2f}")


METER = Meter()

TIMES = []   # (stage, seconds), in the order the stages ran


class timed:
    """with timed("writing"): ... records how long a stage took, for the summary."""

    def __init__(self, stage):
        self.stage = stage

    def __enter__(self):
        self.t = time.monotonic()

    def __exit__(self, *exc):
        TIMES.append((self.stage, round(time.monotonic() - self.t, 1)))
        return False


def print_times():
    if TIMES:
        print("   ⏱  Time by stage: " + ", ".join(f"{st} {sec:.0f}s" for st, sec in TIMES))


class ModelStopped(RuntimeError):
    """The model declined or ran out of room before answering."""


def call_llm(prompt: str, fast: bool = False, step: str = None) -> str:
    """Send a prompt to the available LLM and return the text response."""
    model, effort = step_settings(step, fast)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_anthropic(prompt, model, effort, step)
    if os.environ.get("OPENAI_API_KEY"):
        return _call_openai(prompt, model)
    return _call_gemini(prompt, model)


MAX_TOKENS = 32000   # thinking counts toward this


def _call_anthropic(prompt: str, model: str, effort: str = "high", step: str = None) -> str:
    import anthropic
    client = anthropic.Anthropic()
    extra = {}
    # Claude 5 and later think by default, and effort sets how much. Sent
    # through extra_body so it works with any SDK version.
    if not re.search(r"-[34]-", model):
        extra["extra_body"] = {"output_config": {"effort": effort}}
    # Streamed, so a long reply (thinking plus answer) can use the full
    # MAX_TOKENS without the request timing out
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT + " Respond ONLY with valid JSON, no markdown fences.",
        messages=[{"role": "user", "content": prompt}],
        **extra,
    ) as stream:
        response = stream.get_final_message()
    usage = getattr(response, "usage", None)
    if usage is not None:
        METER.add(step, model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
    if response.stop_reason == "refusal":
        raise ModelStopped(f"{model} declined the request")
    if response.stop_reason == "max_tokens":
        raise ModelStopped(f"{model} ran out of room before finishing (max_tokens)")
    # A response can open with thinking blocks; only text blocks are the answer
    return "".join(b.text for b in response.content if b.type == "text")


def _call_openai(prompt: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def _call_gemini(prompt: str, model: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    client = genai.GenerativeModel(model, system_instruction=SYSTEM_PROMPT)
    response = client.generate_content(
        f"Respond ONLY with valid JSON, no markdown fences.\n\n{prompt}"
    )
    return response.text


def parse_json(raw: str) -> dict:
    """Parse model output, tolerating markdown fences or stray text around the JSON."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        return json.loads(text[start:end + 1])


def call_json(prompt: str, retries: int = 2, fast: bool = False, step: str = None) -> dict:
    """Call the model and parse JSON, asking again if the reply isn't valid JSON."""
    last_error = None
    for attempt in range(retries + 1):
        ask = prompt if attempt == 0 else (
            prompt + "\n\nYour previous reply was not valid JSON "
            f"({last_error}). Reply again with valid JSON only. Escape any double "
            "quotes inside strings as \\\"."
        )
        try:
            return parse_json(call_llm(ask, fast=fast, step=step))
        except json.JSONDecodeError as e:
            last_error = e
            if attempt < retries:
                print(f"     (reply wasn't valid JSON, retrying {attempt + 1}/{retries})")
    raise last_error


# ---------------------------------------------------------------------------
# Source text: split by source, pick the most relevant lines
# ---------------------------------------------------------------------------

SOURCE_HEADER = re.compile(
    r"^#{1,3}\s*(Source|Voter guide|Wikipedia|Campaign site|Office site|Bio page)\s*:\s*(https?://\S+)", re.I
)

POSITION_WORDS = re.compile(
    r"\b(support\w*|oppos\w*|vot(?:e|ed|es|ing)|sponsor\w*|cosponsor\w*|co-sponsor\w*|"
    r"introduc\w*|legislation|bill|act|amendment|policy|policies|position\w*|"
    r"call(?:s|ed)? for|advocat\w*|propos\w*|plan|pledg\w*|ban\w*|fund\w*|"
    r"priorit\w*|endors\w*|criticiz\w*|favor\w*|against|reform\w*|urg\w*)\b",
    re.I,
)
ISSUE_WORDS = re.compile(
    r"\b(climate|energy|health\w*|medicare|medicaid|insurance|immigra\w*|border|"
    r"gun\w*|firearm\w*|abortion|reproductive|israel|gaza|ukraine|china|defense|"
    r"military|veteran\w*|housing|rent|wage\w*|labor|union\w*|trade|tariff\w*|"
    r"tax\w*|crypto\w*|education|student|school\w*|police|crime|social security|"
    r"filibuster|court|democracy|voting rights|child care|drug\w*|price\w*)\b",
    re.I,
)


@dataclass
class Segment:
    source: str                     # URL of a public page
    lines: list = field(default_factory=list)
    kind: str = "source"            # "voter guide", "campaign site", "wikipedia", ...



def parse_sources(text: str) -> list:
    """
    Split a raw candidate file into segments, one per source header. Text
    that isn't under a source URL is ignored: every quote must come from a
    public page anyone can check, so there are no hand-written notes.
    """
    segments = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        url_match = SOURCE_HEADER.match(stripped)
        if url_match:
            if current and current.lines:
                segments.append(current)
            current = Segment(url_match.group(2), kind=url_match.group(1).lower())
            continue
        if stripped and current is not None:
            current.lines.append(stripped)
    if current and current.lines:
        segments.append(current)
    return segments


def line_score(line: str) -> int:
    return 2 * len(POSITION_WORDS.findall(line)) + len(ISSUE_WORDS.findall(line))


def select_excerpt(segments: list, budget: int = EXCERPT_BUDGET_CHARS) -> str:
    """
    Fill the budget with the highest-scoring lines from the sources. Lines
    stay in their original order under their source header so the model can
    cite them.
    """
    keep = set()   # (segment index, line index)
    used = 0

    # Campaign sites and voter guide statements state current positions in the
    # candidate's own words, so their lines outrank Wikipedia's (which skews
    # toward older, notable moments).
    ranked = [
        (line_score(line) + (3 if seg.kind in ("campaign site", "voter guide") else 0), si, li)
        for si, seg in enumerate(segments)
        for li, line in enumerate(seg.lines)
    ]
    ranked = [r for r in ranked if r[0] > 0]
    ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
    for _, si, li in ranked:
        line = segments[si].lines[li]
        if used + len(line) > budget:
            continue
        keep.add((si, li))
        used += len(line) + 1

    out = []
    for si, seg in enumerate(segments):
        chosen = [seg.lines[li] for li in range(len(seg.lines)) if (si, li) in keep]
        if chosen:
            out.append(f"### Source: {seg.source}")
            out.extend(chosen)
            out.append("")
    return "\n".join(out).strip()


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("—", "-").replace("–", "-"))
    return re.sub(r"\s+", " ", text).strip().lower()


# Where one sentence ends and the next begins, or where the model already
# marked a gap with an ellipsis
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(\u201c]?[A-Z0-9])|\s*(?:\.\.\.|\u2026)\s*")
MIN_PIECE_CHARS = 15     # each sentence of a joined quote, so "Yes." can't pass alone


def match_quote(quote: str, segments: list):
    """
    Check a quote against the sources. Returns (source, quote_to_show,
    pieces), or None if it isn't word for word in any one source.

    A quote found as written is shown as written (pieces = 1). Models
    sometimes join two real sentences from different parts of a page into
    one quote. Then each sentence is checked on its own, all of them must
    be word for word on the same page, and the quote is shown with "…"
    between them so a reader sees the gap. Any sentence that isn't found
    drops the whole quote.
    """
    if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
        return None
    pages = [(seg.source, _norm(" ".join(seg.lines))) for seg in segments]
    needle = _norm(quote).strip(" .\"'")
    for source, text in pages:
        if needle in text:
            return source, quote.strip(), 1
    parts = [x.strip() for x in SENTENCE_BREAK.split(quote.strip()) if x and x.strip()]
    if len(parts) < 2 or any(len(x) < MIN_PIECE_CHARS for x in parts):
        return None
    for source, text in pages:
        if all(_norm(x).strip(" .\"'") in text for x in parts):
            return source, " … ".join(parts), len(parts)
    return None


def locate_quote(quote: str, segments: list):
    """The source a quote came from, or None if it isn't word for word in any source."""
    found = match_quote(quote, segments)
    return found[0] if found else None


MAX_STATEMENTS = 12      # for the whole ballot: one statement per topic
PER_CANDIDATE = 3        # target statements each candidate is scored on
MAX_POSITIONS = 20       # per candidate, from step 1
MIN_SCORED = 1           # a statement needs at least one candidate's stated position


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACT_PROMPT_TEMPLATE = """
Below are source excerpts about {name} ({party}), a candidate in the
{race_title}. Each excerpt is grouped under a "### Source: <url>" header.

{excerpt}

---

List up to {max_positions} policy positions this text states explicitly for
{name}: things they support, oppose, voted for, signed, or plan to do.
- Cover as many **different issue areas** as the text addresses (for example
  abortion, health care, climate and energy, guns, immigration, economy,
  housing, education) before listing a second position on any one area.
- When both the candidate's own words (a campaign page or voter guide
  statement) and older history cover an issue, quote the candidate's own
  words: they are the current position.

For each position give:
- "issue": a 1–3 word label (e.g. "Drug Prices", "Nuclear Power")
- "stance": one plain sentence restating only what the quote says
- "quote": one or two consecutive sentences copied **word for word** from
  the excerpt, at least 25 characters, no edits, no ellipses

Only list positions that belong to {name}: their own words, or their own
votes, bills, and plans. Skip quotes from other people, positions of
opponents or critics, and descriptions of what others say {name} believes.

Skip biography, endorsements, fundraising, and vague slogans with no policy.

Respond with this exact JSON structure:
{{"positions": [{{"issue": "...", "stance": "...", "quote": "..."}}]}}
"""

WEDGE_PROMPT_TEMPLATE = """
You are helping first-time voters understand where candidates in the
November 3, 2026 general election actually differ: the **{race_title}**.

Candidates are running for different offices. One question can score
candidates from any office whose positions answer it directly, so a topic
several candidates share (like health care) is asked **once**. Phrase such
questions so they apply at any level of government ("Everyone should have
health coverage through a single public plan"), not "the federal
government" or "the state".

Below are each candidate's documented positions. Every position has an id,
and every one has already been checked against its source.

{position_blocks}

---

## Your Task

Write up to **{max_statements} statements**, each on a **different topic**.
The user will rate each from 1 (strongly disagree) to 5 (strongly agree).
Their agreement with a candidate is measured only on positions that
candidate has actually stated. Silence is never counted for or against.

### Choosing topics
1. **Shared topics first.** Where two or more candidates have positions on
   the same specific question, write a statement that scores all of them.
2. **Then each candidate's own positions.** Fill the rest with statements
   drawn from one candidate's position; other candidates are null unless
   they also have a position that answers it directly.
3. **Equal coverage.** Aim for each candidate to be scored on about
   {per_candidate} statements, so no candidate dominates the quiz.
4. Cover a range of issues. Never write two statements on the same topic.
   Prefer each candidate's most concrete positions (a bill, vote, or plan).

### Writing statements
- Write each statement at the level the cited positions actually address.
  If one position is about a specific program and another about the
  general goal, write about the general goal.
- Plain language for someone new to politics: one short sentence, no
  jargon, no acronyms without explanation.
- **One idea per statement.** Never combine several policies.
- **No reasons inside the statement.** State the policy, not why.
- Neutral wording: no loaded or emotional words, no names of candidates or
  bills that make one answer sound right.
- **Vary direction.** For statements drawn from one candidate's position,
  phrase some so that candidate agrees and some so they disagree, so that
  agreeing never always means siding with the same person. When phrasing
  the other side, write a **real policy that real people advocate**, the
  way its supporters would say it ("Private gun sales should not require a
  background check"), never a negation or strawman ("Background checks
  should be weakened", "X should remain unchanged").

### Scoring
For each candidate, either cite the id of the position that answers this
statement and give a score, or give null:
- 5 = strongly agrees, 4 = mostly agrees, 3 = documented mixed or middle
  position, 2 = mostly disagrees, 1 = strongly disagrees.
- A position answers a statement when it takes a side on it. A specific
  proposal answers a broader statement it clearly takes a side on: capping
  developer fees answers "California should make it cheaper to build
  housing". A broad goal never answers a specific policy: supporting
  affordable housing does not answer a statement about rent control. A
  position on a related but different policy does not count. Never guess from party, and never use 3 for unknown.

### Also provide for each statement
- **candidatePositions**: one plain sentence per scored candidate restating
  only their cited position. Add nothing that is not in it.
- **raceInsight**: 1–2 sentences on why this issue matters to voters in
  {state_name}. Talk about the issue only: never describe or compare the
  candidates, and never mention how much information exists about them.
- **policyBackground**: 2–3 neutral sentences explaining the policy in the
  statement. Only explain terms that appear in the statement itself; do
  not introduce related policies or jargon the statement doesn't use.

Respond with this exact JSON structure:
{{
  "statements": [
    {{
      "topic": "Nuclear Power",
      "text": "Statement text here.",
      "basis": {{"candidate_a": "candidate_a.3", "candidate_b": null}},
      "candidateAgreement": {{"candidate_a": 4, "candidate_b": null}},
      "candidatePositions": {{"candidate_a": "Their cited stance."}},
      "raceInsight": "Why this issue matters here.",
      "policyBackground": "Neutral background."
    }}
  ]
}}

Use candidate ids exactly as given: {candidate_ids}.
"""

FILL_PROMPT_TEMPLATE = """
Some statements in a voter guide have candidates with no score yet. For
each item below, read that candidate's documented positions and decide
whether one of them **directly** answers the statement.

- A position answers a statement when it takes a side on it. A specific
  proposal answers a broader statement it clearly takes a side on: capping
  developer fees answers "California should make it cheaper to build
  housing". A broad goal never answers a specific policy: supporting
  affordable housing does not answer a statement about rent control. A
  position on a related but different policy does not count.
- If one does, cite its id, give a score (5 strongly agrees, 4 mostly
  agrees, 3 documented mixed position, 2 mostly disagrees, 1 strongly
  disagrees), and write one plain sentence restating only that position.
- If none does, return null for all three. Never guess from party.

{items}

Respond with this exact JSON structure:
{{
  "fills": [
    {{"statement_id": "senate_s1", "candidate_id": "x", "position_id": "x.4", "score": 5, "summary": "..."}},
    {{"statement_id": "senate_s2", "candidate_id": "x", "position_id": null, "score": null, "summary": null}}
  ]
}}
"""

BIO_PROMPT_TEMPLATE = """
Write a short, neutral background for {name} ({party}), a candidate for
{office}, from the text below only.

{excerpt}

---

Rules:
- 3 to 5 sentences of **background facts only**: offices held and when,
  career before politics, education, where they're from, notable roles.
- **No policy positions**, no campaign promises, no accomplishments framed
  as praise, no adjectives like "tireless", "fierce", "champion". If the
  text describes something with praise, restate only the plain fact.
- Every sentence must be backed by a quote copied **word for word** from
  the text above (one or two consecutive sentences, no edits).

Respond with this exact JSON structure:
{{"sentences": [{{"text": "Plain factual sentence.", "quote": "Exact words from the text."}}]}}
"""

TOPUP_PROMPT_TEMPLATE = """
A voter guide needs more questions about {name} ({party}, {office}).
They are currently scored on {have} question(s); write {need} more.

Their documented positions:
{positions}

Topics already asked (do not repeat these):
{used}

Rules:
- Each statement comes from one of the positions above, cited by id, on a
  topic not already asked. One short, plain, neutral sentence, one idea,
  no reasons inside it, phrased so it applies at any level of government.
- Score {name} 1–5 on it from the cited position (5 strongly agrees,
  1 strongly disagrees).

Respond with this exact JSON structure:
{{
  "statements": [
    {{"topic": "...", "text": "...", "position_id": "{cid}.3", "score": 5,
      "summary": "One sentence restating only the cited position.",
      "raceInsight": "1-2 sentences on why this issue matters to {state_name} voters, about the issue only.",
      "policyBackground": "2-3 neutral sentences explaining only the terms in the statement."}}
  ]
}}
"""

FLIP_PROMPT_TEMPLATE = """
Rewrite each statement below as the **opposite policy**, stated the way
its real supporters would put it. Keep the same topic. It must be a policy
real people advocate, not a negation or strawman: "Private gun sales should
not require a background check", never "Background checks should be
weakened". One short, plain, neutral sentence, one idea, no reasons.

Also rewrite the background: 2-3 neutral sentences explaining only the
terms in the new statement.

{items}

Respond with this exact JSON structure:
{{"rewrites": [{{"statement_id": "ballot_s1", "text": "...", "policyBackground": "..."}}]}}
"""

VERIFY_PROMPT_TEMPLATE = """
You are checking a voter guide for accuracy and neutrality. Be strict.

For each item below, a candidate was given a 1–5 agreement score on a
statement, backed by a quote from a source, plus a one-sentence summary of
their position. Decide:
  - supported: does the quote, on its own, clearly show which side of this
    statement the candidate is on (agree or disagree)? A specific proposal
    shows a side on a broader statement it clearly implies (capping
    developer fees shows support for making it cheaper to build housing);
    a broad goal never shows a side on a specific policy. Mark it
    unsupported if the quote is about a different policy or the side is
    unclear.
  - best_score: how strongly the candidate holds this position, 1–5, based
    on what the quote says (a firm commitment is 5 or 1; a qualified or
    partial one is 4 or 2). Judge the candidate's stance, **not** how
    directly the quote is worded: an indirect quote showing firm support is
    still a 5.
  - direct: true if the quote states this exact policy; false if it shows
    the candidate's side through a closely related action or statement.
  - summary_faithful: does the summary say only what the quote says? Mark it
    false if it adds any claim, detail, or comparison not in the quote.
  - own_position: true if the quote is the candidate's own words, or describes
    the candidate's own vote, bill, or plan. False if it is someone else
    speaking (an opponent, critic, or journalist characterizing them) or
    describes another person's position.

Also review each statement's wording. Mark it loaded if it uses emotional or
one-sided language, makes one answer sound obviously correct, combines more
than one policy, includes a reason for the policy, or describes a position
no real candidate or advocate would put that way (for example "X should be
weakened" or "loopholes should remain open"). For a loaded statement, also
give fixed_text: a rewrite that fixes the problem and keeps the same policy
and the same side, so every scored candidate's score still holds. If it
combined several policies, keep the main one. If it can't be fixed that
way, give null and it is removed.

Also mark contested false only if nobody argues the other side: no
candidate, party or advocacy group anywhere takes the opposite position,
for example "State spending should be audited" or "Government should spend
money wisely". Such a statement tells a voter nothing. A policy that any
real group opposes is contested, even if most voters in this state agree
with it: abortion access, single-payer health care, school vaccine
requirements and gun restrictions are all contested.

## Scores to check
{score_items}

## Statements to review for wording
{statement_items}

Respond with this exact JSON structure:
{{
  "scores": [
    {{"statement_id": "s1", "candidate_id": "x", "supported": true, "best_score": 5, "direct": false, "summary_faithful": true, "own_position": true, "note": "short reason"}}
  ],
  "wording": [
    {{"statement_id": "s1", "loaded": false, "fixed_text": null, "contested": true, "note": "short reason"}}
  ]
}}
"""

REWRITE_PROMPT_TEMPLATE = """
Each statement below was rejected for its wording, for the reason given.
Rewrite it so it passes: one short, plain, neutral sentence, one policy, no
reason inside it, no emotional or one-sided words, and a policy real people
disagree about. Keep the same policy and the same side, so anyone who agreed
with the original agrees with the rewrite. If it combined several policies,
keep only the main one. If it can't be fixed that way, return null text.

Also rewrite the background: 2-3 neutral sentences explaining only the
terms in the new statement.

{items}

Respond with this exact JSON structure:
{{"rewrites": [{{"statement_id": "x_s1", "text": "... or null", "policyBackground": "..."}}]}}
"""


def build_party_lines(race: dict) -> str:
    from collections import defaultdict
    by_party = defaultdict(list)
    for cid in race["candidates"]:
        info = CANDIDATES[cid]
        by_party[info["party"]].append(f"{info['name']} (id: {cid})")
    return "\n".join(f"- {party}: {', '.join(names)}" for party, names in by_party.items())


def load_sources(race: dict) -> dict:
    """candidate id → list of Segments (empty if no raw file)."""
    sources = {}
    for cid in race["candidates"]:
        raw_path = RAW_DIR / f"{cid}.txt"
        if raw_path.exists():
            segments = parse_sources(raw_path.read_text(encoding="utf-8"))
            # Human corrections from config: drop source lines known to be wrong
            excludes = [e.lower() for e in CANDIDATES[cid].get("exclude_lines", [])]
            for seg in segments:
                kept = [l for l in seg.lines if not any(e in l.lower() for e in excludes)]
                if len(kept) != len(seg.lines):
                    print(f"   ✂ Removed {len(seg.lines) - len(kept)} corrected line(s) from {cid}'s sources")
                seg.lines = kept
            sources[cid] = segments
        else:
            print(f"   ⚠ No raw data for {cid} — run scout first")
            sources[cid] = []
    return sources


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

# Every flag names the heuristic in HEURISTICS.md that raised it, so the
# glass box can say which rule shaped a question and the scorecard can count
# how often each rule fired. A failed model call is not a rule firing: it is
# tagged "system" so pipeline breakdowns are counted separately.
HEURISTICS = {
    1: "Say only what you can point to",
    2: "Don't fill the silence",
    3: "Don't outrun the evidence",
    4: "Let people speak for themselves",   # speaker check in verify_with_llm
    5: "Ask without leaning",
    6: "Give everyone equal footing",
    8: "Spend attention where it tells you the most",
}


def flag(flags: list, heuristic, detail: str, statement: str = None, candidate: str = None,
         item=None) -> None:
    """
    Record a flag. heuristic is a number from HEURISTICS, or None for a system
    failure. item keeps a copy of whatever was dropped or changed, as it was
    before the change, so the evaluation can check whether each rule fired
    correctly and the glass box can show what the rule removed.
    """
    flags.append({
        "heuristic": heuristic,
        "name": HEURISTICS.get(heuristic, "System"),
        "statement": statement,
        "candidate": candidate,
        "detail": detail,
        "item": copy.deepcopy(item),
    })


def run_record(race: dict) -> dict:
    """
    Everything needed to reproduce or explain this run: the code version,
    models, a fingerprint of every prompt, and of every candidate's sources.
    """
    def git(*args):
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True,
                                  cwd=Path(__file__).parent.parent).stdout.strip()
        except OSError:
            return ""

    def fp(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    prompts = {"SYSTEM_PROMPT": SYSTEM_PROMPT}
    prompts.update({k: v for k, v in globals().items() if k.endswith("_PROMPT_TEMPLATE")})
    sources = {}
    for cid in race["candidates"]:
        path = RAW_DIR / f"{cid}.txt"
        sources[cid] = fp(path.read_text(encoding="utf-8")) if path.exists() else None
    return {
        "git_commit": git("rev-parse", "--short", "HEAD"),
        # Uncommitted changes mean the commit alone doesn't reproduce this run
        "uncommitted_changes": bool(git("status", "--porcelain", "agents")),
        "model": active_model(),
        "fast_model": active_model(fast=True),
        "steps": {st: dict(zip(("model", "effort"), step_settings(st))) for st in STEPS},
        "prompts": {k: fp(v) for k, v in sorted(prompts.items())},
        "sources": sources,
        # How Scout read each candidate's sources: live or as of a date
        "scout": {cid: scout_mode(cid) for cid in race["candidates"]},
    }


def scout_mode(cid: str):
    path = RAW_DIR / f"{cid}.sources.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    return {k: rec.get(k) for k in ("mode", "as_of", "retrieved_at")}


def flag_text(f: dict) -> str:
    where = " / ".join(x for x in (f["statement"], f["candidate"]) if x)
    tag = f"H{f['heuristic']}" if f["heuristic"] else "SYSTEM"
    return f"[{tag}] {where + ': ' if where else ''}{f['detail']}"


def score_snapshot(statement: dict, cid: str) -> dict:
    """One candidate's score on a statement, as it stood before a check changed it."""
    return {
        "text": statement.get("text"),
        "score": statement["candidateAgreement"].get(cid),
        "evidence": (statement.get("evidence") or {}).get(cid),
        "summary": (statement.get("candidatePositions") or {}).get(cid),
    }


def set_unknown(statement: dict, cid: str) -> None:
    statement["candidateAgreement"][cid] = None
    statement.setdefault("evidence", {})[cid] = None
    statement.setdefault("candidatePositions", {})[cid] = UNKNOWN_POSITION


def check_schema(data: dict, race: dict) -> None:
    assert isinstance(data.get("statements"), list), "Missing 'statements' list"
    for s in data["statements"]:
        for key in ("topic", "text", "candidateAgreement"):
            assert key in s, f"Statement \"{s.get('text', '?')[:40]}\" missing '{key}'"
        s.setdefault("evidence", {})
        s.setdefault("candidatePositions", {})
        for cid in race["candidates"]:
            s["candidateAgreement"].setdefault(cid, None)
        # Drop any candidate ids the model invented
        for key in ("candidateAgreement", "evidence", "candidatePositions"):
            for cid in list(s[key]):
                if cid not in race["candidates"]:
                    del s[key][cid]


def verify_with_llm(data: dict, flags: list, strict: bool = False) -> None:
    """
    Guardrail 4: a second model call checks each score against its quote.
    strict: a reply cut off by length raises ModelStopped, so the caller can
    split the batch, instead of leaving the scores unchecked.
    """
    score_items, statement_items = [], []
    for s in data["statements"]:
        statement_items.append(f'- {s["id"]}: "{s["text"]}"')
        for cid, score in s["candidateAgreement"].items():
            if score is None:
                continue
            quote = s["evidence"][cid]["quote"]
            score_items.append(
                f'- statement_id: {s["id"]}\n  statement: "{s["text"]}"\n'
                f'  candidate_id: {cid}\n  score: {score}\n  quote: "{quote}"\n'
                f'  summary: "{s["candidatePositions"].get(cid, "")}"'
            )
    if not score_items:
        return

    prompt = VERIFY_PROMPT_TEMPLATE.format(
        score_items="\n".join(score_items),
        statement_items="\n".join(statement_items),
    )
    key = cache_key(prompt, *step_settings("verify"))
    result = cache_get(f"verify_{key}", key)
    try:
        if result is None:
            print("   Checking scores against their quotes...")
            result = call_json(prompt, step="verify")
            cache_put(f"verify_{key}", key, result)
        else:
            print("   Checking scores against their quotes (unchanged, reusing the saved check)...")
    except Exception as e:  # a failed check must never silently pass
        if strict and isinstance(e, ModelStopped):
            raise
        flag(flags, None, f"verification call failed ({e}); scores are unchecked")
        return

    by_id = {s["id"]: s for s in data["statements"]}
    for item in result.get("scores", []):
        s = by_id.get(item.get("statement_id"))
        cid = item.get("candidate_id")
        if not s or cid not in s["candidateAgreement"]:
            continue
        current = s["candidateAgreement"][cid]
        if current is None:
            continue
        if item.get("own_position") is False:
            flag(flags, 4, f"quote isn't the candidate's own position ({item.get('note', '')}), set to unknown",
                 s["id"], cid, item=score_snapshot(s, cid))
            set_unknown(s, cid)
            continue
        if item.get("supported") is False:
            flag(flags, 3, f"quote doesn't show a side ({item.get('note', '')}), set to unknown", s["id"], cid,
                 item=score_snapshot(s, cid))
            set_unknown(s, cid)
            continue
        best = item.get("best_score")
        if isinstance(best, int) and 1 <= best <= 5 and best != current:
            # Same side, different strength: correct it rather than erase it.
            # A change that flips sides (e.g. 5 → 2) means the quote doesn't
            # support the original reading, so that becomes unknown instead.
            if (best >= 3) == (current >= 3) or best == 3 or current == 3:
                flag(flags, 3, f"score adjusted {current} → {best} ({item.get('note', '')})", s["id"], cid,
                     item=score_snapshot(s, cid))
                s["candidateAgreement"][cid] = best
            else:
                flag(flags, 3, f"quote points the other way ({current} vs {best}), set to unknown", s["id"], cid,
                     item=score_snapshot(s, cid))
                set_unknown(s, cid)
                continue
        # Record how directly the quote states the policy (shown in the app)
        if item.get("direct") is False:
            s.setdefault("evidenceDirect", {})[cid] = False
        if item.get("summary_faithful") is False:
            flag(flags, 3, f"summary adds claims not in the quote ({item.get('note', '')}), rewrite it", s["id"], cid,
                 item=score_snapshot(s, cid))
    loaded = set()
    for item in result.get("wording", []):
        sid = item.get("statement_id")
        if item.get("loaded"):
            fixed = (item.get("fixed_text") or "").strip()
            if fixed and sid in by_id:
                st = by_id[sid]
                old = st["text"]
                st.setdefault("draft", old)
                st["text"] = fixed
                flag(flags, 5, f'reworded by the check ({item.get("note", "")}): "{old[:50]}" → "{fixed[:50]}"',
                     sid, item={"text": old})
                continue
            loaded.add(sid)
            flag(flags, 5, f"dropped for loaded wording ({item.get('note', '')})", sid, item=by_id.get(sid))
            if sid in by_id:   # kept aside in case a caller wants to rewrite it
                data.setdefault("rejected", []).append((by_id[sid], item.get("note", "")))
        elif (item.get("contested") is False and sid in by_id
              and not any(v is not None and v <= 2 for v in by_id[sid]["candidateAgreement"].values())):
            # A candidate who disagrees proves it's contested, whatever the check says
            by_id[sid]["consensus"] = True
            flag(flags, 8, f"nearly every voter would agree ({item.get('note', '')}); kept out of the core", sid)
    data["statements"] = [s for s in data["statements"] if s["id"] not in loaded]


def equalize_coverage(data: dict, race: dict, positions: dict, flags: list) -> None:
    """
    Keep candidates on roughly equal footing: if one candidate is scored on
    more statements than another, drop their single-candidate statements
    until the gap is at most 1. Shared statements are never dropped.
    """
    def counts():
        return {c: sum(1 for s in data["statements"] if s["candidateAgreement"].get(c) is not None)
                for c in race["candidates"]}

    active = [c for c in race["candidates"] if positions.get(c)]
    while True:
        n = counts()
        floor = max(min(n[c] for c in active), PER_CANDIDATE) if active else 0
        over = [c for c in active if n[c] > floor + 1]
        if not over:
            break
        cid = max(over, key=lambda c: n[c])
        solo = [s for s in data["statements"]
                if s["candidateAgreement"].get(cid) is not None
                and sum(v is not None for v in s["candidateAgreement"].values()) == 1]
        if not solo:
            break
        drop = solo[-1]
        data["statements"].remove(drop)
        flag(flags, 6, f"dropped \"{drop['text'][:70]}\" so this candidate doesn't outnumber others", drop["id"], cid,
             item=drop)


def check_structure(data: dict, race: dict, flags: list) -> None:
    """Direction balance and thin-data checks on the statements that survived."""
    statements = data["statements"]
    if len(statements) < 3:
        flag(flags, 3, f"only {len(statements)} usable statement(s)")

    # Direction: if a candidate agrees (4-5) with every one of their scored
    # statements, "agree" always means siding with them.
    for cid in race["candidates"]:
        vals = [s["candidateAgreement"][cid] for s in statements
                if s["candidateAgreement"].get(cid) is not None]
        if len(vals) >= 3 and all(v >= 4 for v in vals):
            flag(flags, 5, f"agrees with all {len(vals)} statements, phrase some the other way", candidate=cid)
        if len(vals) < 2:
            flag(flags, 3, f"scored on only {len(vals)} statement(s), too few to show a score", candidate=cid)


def drop_unscorable(data: dict, flags: list, stage: str) -> None:
    """Remove statements that can't compare at least MIN_SCORED candidates."""
    kept = []
    for s in data["statements"]:
        known = [v for v in s["candidateAgreement"].values() if v is not None]
        if len(known) >= MIN_SCORED:
            kept.append(s)
        else:
            flag(flags, 2, f"dropped ({stage}): \"{s['text'][:70]}\" had {len(known)} scored candidate(s)", s["id"], item=s)
    data["statements"] = kept


def extract_positions(race: dict, sources: dict, flags: list) -> dict:
    """
    Step 1: one call per candidate to list their stated positions, then keep
    only positions whose quote is verbatim in the sources.
    Returns candidate id → list of {id, issue, stance, quote, source}.
    """
    positions = {}
    total = len(race["candidates"])

    def listed(cid):
        """The model's list for one candidate (saved answer if nothing changed), or None."""
        info = CANDIDATES[cid]
        prompt = EXTRACT_PROMPT_TEMPLATE.format(
            name=info["name"], party=info["party"], race_title=RACE_TITLES[info["race"]],
            excerpt=select_excerpt(sources[cid]), max_positions=MAX_POSITIONS,
        )
        # Sources, prompt and model unchanged since last run → reuse the
        # model's answer, no call. The raw answer is kept, not the checked
        # one, so a change to the quote check applies without new calls.
        key = cache_key(prompt, *step_settings("positions"))
        found = cache_get(f"positions_raw_{cid}", key)
        if found is not None:
            return found
        try:
            found = call_json(prompt, step="positions").get("positions", [])
        except Exception as e:
            flag(flags, None, f"position listing failed ({e})", candidate=cid)
            return None
        cache_put(f"positions_raw_{cid}", key, found)
        return found

    todo = [c for c in race["candidates"] if sources.get(c)]
    print(f"   Listing positions for {len(todo)} candidates, {WORKERS} at a time...")
    answers = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for n, (cid, found) in enumerate(zip(todo, ex.map(listed, todo)), 1):
            answers[cid] = found
    for n, cid in enumerate(race["candidates"], 1):
        info = CANDIDATES[cid]
        positions[cid] = []
        if not sources.get(cid):
            flag(flags, 1, "no sources, no positions", candidate=cid)
            continue
        found = answers.get(cid)
        if found is None:
            continue
        missed, joined = [], []
        for p in found:
            match = match_quote(p.get("quote", ""), sources[cid])
            if match is None:
                missed.append(p)
                continue
            source, quote, pieces = match
            pos = {
                "id": f"{cid}.{len(positions[cid]) + 1}",
                "issue": p.get("issue", ""),
                "stance": p.get("stance", ""),
                "quote": quote,
                "source": source,
            }
            positions[cid].append(pos)
            if pieces > 1:
                joined.append({"position": pos["id"], "as_listed": p.get("quote", "")})
        print(f"   [{n}/{total}] {info['name']}: {len(positions[cid])} positions kept "
              f"({len(joined)} from separate sentences), {len(missed)} dropped (quote not in source)")
        if joined:
            flag(flags, 1, f"{len(joined)} quote(s) joined sentences from different parts of a page; "
                           "each sentence checked on its own and shown with …", candidate=cid, item=joined)
        if missed:
            flag(flags, 1, f"{len(missed)} listed position(s) dropped, quote not found in sources",
                 candidate=cid, item=missed)
    return positions


# ---------------------------------------------------------------------------
# Positions only: one race, and how much candidates' positions overlap
# ---------------------------------------------------------------------------

# A question at least this many candidates take a position on tells a voter
# something about a good part of the field (heuristic 8).
WIDE_QUESTION = 5

GROUP_PROMPT_TEMPLATE = """
Below are {n} policy positions taken by candidates in the {race_title}.
Each line is: <position id> | <issue label> | <stance>
Candidate names are left out on purpose.

{lines}

---

Group the positions by the policy question they answer, so we can see which
positions speak to the same question.
- A question is one specific policy a voter could agree or disagree with,
  such as rent control, high-speed rail funding or single-payer health care.
  Not a broad area such as housing or health care.
- Positions on opposite sides belong together: "expand rent control" and
  "repeal rent control" answer one question.
- Only group positions that answer the same question. Zoning reform and rent
  control are two questions, even though both are about housing.
- A position no other position shares gets a question of its own.
- Name each question with a short neutral label that names the policy, not
  a side ("Rent control", not "Protect renters").
- Give each question a broad "area" (Housing, Health care, Economy...), and
  use the same area name every time it applies.
- Every position id appears in exactly one question.

Respond with this exact JSON structure:
{{"questions": [{{"area": "...", "question": "...", "positions": ["P1", "P7"]}}]}}
"""


def clean_groups(raw: list, short: dict, flags: list) -> list:
    """
    Check the model's grouping against the positions it was given. Ids it
    made up are dropped, an id it used twice keeps its first question, and a
    position it left out becomes a question of its own. Leaving one out can
    only make overlap look smaller, never larger.
    short maps the ids shown to the model (P1, P2...) to position ids.
    """
    seen, unknown, repeated, out = set(), [], [], []
    for q in raw:
        ids = []
        for s in q.get("positions", []):
            if s not in short:
                unknown.append(s)
            elif s in seen:
                repeated.append(s)
            else:
                seen.add(s)
                ids.append(short[s])
        if ids:
            out.append({
                "area": (q.get("area") or "Other").strip().capitalize(),
                "question": (q.get("question") or "").strip(),
                "positions": ids,
            })
    missing = [short[s] for s in short if s not in seen]
    for pid in missing:
        out.append({"area": "Ungrouped", "question": "(not grouped)", "positions": [pid]})
    if unknown or repeated or missing:
        flag(flags, None, f"grouping: {len(unknown)} unknown id(s) dropped, {len(repeated)} "
                          f"repeated id(s) kept once, {len(missing)} position(s) left out "
                          "and counted as unshared",
             item={"unknown": unknown, "repeated": repeated, "missing": missing})
    return out


def group_positions(race: dict, positions: dict, flags: list) -> list:
    """
    One call that sorts every checked position in a race into the policy
    question it answers. Candidates are shown by position id only, so the
    grouping can't lean on who said what or which party they're in.
    """
    items = [p for cid in race["candidates"] for p in positions.get(cid, [])]
    if not items:
        return []
    short = {f"P{i}": p["id"] for i, p in enumerate(items, 1)}
    lines = "\n".join(f"{s} | {p['issue']} | {p['stance']}" for s, p in zip(short, items))
    prompt = GROUP_PROMPT_TEMPLATE.format(n=len(items), race_title=race["title"], lines=lines)
    key = cache_key(prompt, *step_settings("group"))
    cached = cache_get(f"groups_{race['id']}", key)
    if cached is not None:
        print(f"   Positions unchanged, reusing {len(cached)} questions")
        return cached
    print(f"   Grouping {len(items)} positions into shared questions "
          "(one long call, usually a few minutes)...")
    try:
        raw = call_json(prompt, step="group").get("questions", [])
    except Exception as e:
        flag(flags, None, f"grouping failed ({e})")
        return []
    questions = clean_groups(raw, short, flags)
    for q in questions:
        q["candidates"] = sorted({pid.rsplit(".", 1)[0] for pid in q["positions"]})
    cache_put(f"groups_{race['id']}", key, questions)
    return questions


def overlap_report(all_ids: list, positions: dict, questions: list) -> dict:
    """
    How much candidates' positions overlap. A quiz question can only tell
    apart candidates who took a position on it, so this decides how far a
    narrowing quiz can go. It counts positions on a question, not whether
    candidates disagree on it.
    """
    with_pos = [c for c in all_ids if positions.get(c)]
    owners = [q["candidates"] for q in questions]
    sizes = Counter()
    for cs in owners:
        n = len(cs)
        sizes["1" if n == 1 else "2-4" if n < 5 else "5-9" if n < 10 else "10+"] += 1
    on = {c: set() for c in with_pos}
    for i, cs in enumerate(owners):
        for c in cs:
            on[c].add(i)
    wide = {i for i, cs in enumerate(owners) if len(cs) >= WIDE_QUESTION}
    shared = [len(on[a] & on[b]) for a, b in combinations(with_pos, 2)]
    areas = {}
    for q in questions:
        areas.setdefault(q["area"], set()).update(q["candidates"])
    ranked = sorted(range(len(questions)), key=lambda i: (-len(owners[i]), questions[i]["question"]))
    return {
        "candidates": {
            "in_race": len(all_ids),
            "with_sources": sum(1 for c in all_ids if c in positions),
            "with_positions": len(with_pos),
        },
        "positions": sum(len(positions.get(c, [])) for c in all_ids),
        "questions": len(questions),
        "questions_by_candidates": {k: sizes.get(k, 0) for k in ("1", "2-4", "5-9", "10+")},
        "wide_question_min": WIDE_QUESTION,
        "widest": [{"area": questions[i]["area"], "question": questions[i]["question"],
                    "candidates": len(owners[i])} for i in ranked[:15]],
        "areas": dict(sorted(((a, len(cs)) for a, cs in areas.items()), key=lambda x: -x[1])),
        "pairs": {
            "count": len(shared),
            "median_shared_questions": median(shared) if shared else None,
            "share_with_none": round(sum(1 for s in shared if s == 0) / len(shared), 2) if shared else None,
        },
        "per_candidate": {c: {"positions": len(positions[c]), "questions": len(on[c]),
                              "wide_questions": len(on[c] & wide)} for c in with_pos},
        # Candidates no wide question reaches: the quiz has no way to tell them apart
        "on_no_wide_question": [c for c in with_pos if not on[c] & wide],
    }


def print_overlap(race: dict, report: dict) -> None:
    c, b = report["candidates"], report["questions_by_candidates"]
    name = lambda cid: CANDIDATES[cid]["name"]
    print(f"\n📊 Overlap, {race['title']}")
    print(f"   {c['in_race']} candidates, {c['with_sources']} with sources, "
          f"{c['with_positions']} with at least one checked position ({report['positions']} positions)")
    print(f"   Positions fall into {report['questions']} questions. Candidates per question:")
    print(f"     1: {b['1']}   2-4: {b['2-4']}   5-9: {b['5-9']}   10+: {b['10+']}")
    print("   Widest questions:")
    for w in report["widest"][:10]:
        print(f"     {w['candidates']:>3}  {w['area']} · {w['question']}")
    p = report["pairs"]
    if p["count"]:
        print(f"   Candidate pairs share a median of {p['median_shared_questions']} question(s); "
              f"{p['share_with_none']:.0%} share none.")
    left = report["on_no_wide_question"]
    print(f"   {len(left)} candidate(s) take no position on any question {WIDE_QUESTION}+ candidates share"
          + (f": {', '.join(name(x) for x in left)}" if left else ""))


def race_for_run(race_id: str) -> tuple:
    """The race from config, with only the candidates who have sources."""
    from agents.scout import STATE_NAMES
    race = next((r for r in RACES if r["id"] == race_id), None)
    if race is None:
        raise SystemExit(f"No race {race_id!r} in config. Races: {', '.join(r['id'] for r in RACES)}")
    with_sources = [c for c in race["candidates"] if (RAW_DIR / f"{c}.txt").exists()]
    run_race = {**race, "candidates": with_sources, "state_name": STATE_NAMES.get(race["state"], race["state"])}
    return race, run_race


def positions_only(race_id: str) -> dict:
    """
    Step 1 for a single race, with no statements written: each candidate's
    checked positions, grouped into shared questions, and an overlap report.
    Saved to data/processed/positions_<race id>.json.
    """
    race, run_race = race_for_run(race_id)
    print(f"\n🧭 Positions for {race['title']}: {len(run_race['candidates'])} of "
          f"{len(race['candidates'])} candidates")
    print(f"   Models: {step_settings('positions')[0]} (positions), {step_settings('group')[0]} (grouping)")
    flags = []
    positions = extract_positions(run_race, load_sources(run_race), flags)
    questions = group_positions(run_race, positions, flags)
    report = overlap_report(race["candidates"], positions, questions)
    output = {
        "run": run_record(run_race),
        "race_id": race["id"],
        "race_title": race["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": active_model(),
        "fast_model": active_model(fast=True),
        "report": report,
        "cost": METER.summary(),
        "questions": questions,
        "positions": positions,
        "flags": flags,
        "flag_counts": {
            (f"H{h}" if h else "system"): sum(1 for f in flags if f["heuristic"] == h)
            for h in dict.fromkeys(f["heuristic"] for f in flags)
        },
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"positions_{race['id']}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print_overlap(race, report)
    print(f"\n   ✓ Saved → data/processed/{out_path.name}")
    system = [f for f in flags if f["heuristic"] is None]
    if system:
        print(f"   ⚠ {len(system)} step(s) failed; see flags in the file:")
        for f in system:
            print(f"      - {flag_text(f)}")
    return output


def build_statement_prompt(race: dict, positions: dict) -> str:
    blocks = []
    for cid in race["candidates"]:
        info = CANDIDATES[cid]
        lines = [f"## {info['name']} ({info['party']}, {RACE_TITLES[info['race']].split(' — ')[0]}, id: {cid})"]
        if positions[cid]:
            lines += [f"- [{p['id']}] {p['issue']}: {p['stance']}" for p in positions[cid]]
        else:
            lines.append("- (no documented positions)")
        blocks.append("\n".join(lines))
    return WEDGE_PROMPT_TEMPLATE.format(
        race_title=race["title"],
        position_blocks="\n\n".join(blocks),
        max_statements=MAX_STATEMENTS,
        per_candidate=PER_CANDIDATE,
        state_name=race["state_name"],
        candidate_ids=", ".join(race["candidates"]),
    )


def attach_evidence(data: dict, race: dict, positions: dict, flags: list) -> None:
    """
    Turn each statement's cited position ids into evidence. Quotes come from
    the checked positions, never from the model's statement output.
    """
    by_id = {p["id"]: p for cid in positions for p in positions[cid]}
    for s in data["statements"]:
        basis = s.pop("basis", {}) or {}
        s["evidence"] = {}
        for cid in race["candidates"]:
            score = s["candidateAgreement"].get(cid)
            pos = by_id.get(basis.get(cid) or "")
            valid_score = isinstance(score, int) and 1 <= score <= 5
            if score is None:
                set_unknown(s, cid)
            elif not valid_score or pos is None or not pos["id"].startswith(f"{cid}."):
                flag(flags, 1, f"score {score!r} without a valid cited position, set to unknown", s["id"], cid,
                     item={"score": score, "cited": basis.get(cid),
                           "summary": s["candidatePositions"].get(cid)})
                set_unknown(s, cid)
            else:
                s["evidence"][cid] = {"quote": pos["quote"], "source": pos["source"], "position": pos["id"]}


def fill_gaps(data: dict, race: dict, positions: dict, flags: list) -> None:
    """
    For every statement, ask whether each unscored candidate has a position
    that answers it directly. Catches cases where the writer built a
    statement around one candidate and overlooked another's stated position.
    """
    by_id = {p["id"]: p for cid in positions for p in positions[cid]}
    items = []
    for s in data["statements"]:
        for cid in race["candidates"]:
            if s["candidateAgreement"].get(cid) is None and positions.get(cid):
                plist = "\n".join(f"    - [{p['id']}] {p['issue']}: {p['stance']}" for p in positions[cid])
                items.append(
                    f'- statement_id: {s["id"]}\n  statement: "{s["text"]}"\n'
                    f"  candidate_id: {cid}\n  positions:\n{plist}"
                )
    if not items:
        return
    print(f"   Checking {len(items)} blank score(s) against each candidate's positions...")
    try:
        result = call_json(FILL_PROMPT_TEMPLATE.format(items="\n\n".join(items)), step="fill")
    except Exception as e:
        flag(flags, None, f"gap-filling call failed ({e})")
        return

    stmts = {s["id"]: s for s in data["statements"]}
    filled = []
    for f in result.get("fills", []):
        s, cid = stmts.get(f.get("statement_id")), f.get("candidate_id")
        pos, score = by_id.get(f.get("position_id") or ""), f.get("score")
        if not s or cid not in race["candidates"] or s["candidateAgreement"].get(cid) is not None:
            continue
        if pos is None or not pos["id"].startswith(f"{cid}.") or not isinstance(score, int) or not 1 <= score <= 5:
            continue
        s["candidateAgreement"][cid] = score
        s["evidence"][cid] = {"quote": pos["quote"], "source": pos["source"]}
        s["candidatePositions"][cid] = f.get("summary") or pos["stance"]
        filled.append({"statement": s["id"], "candidate": cid, "position": pos["id"], "score": score})
    print(f"     filled {len(filled)}")
    if filled:
        flag(flags, 2, f"gap-filling added {len(filled)} score(s) the first pass missed (checked below)",
             item=filled)


def scored_counts(data: dict, race: dict) -> dict:
    return {c: sum(1 for s in data["statements"] if s["candidateAgreement"].get(c) is not None)
            for c in race["candidates"]}


def top_up(data: dict, race: dict, positions: dict, flags: list) -> list:
    """
    Raise under-covered candidates to PER_CANDIDATE questions by writing more
    statements from their own positions (instead of cutting everyone else).
    """
    by_id = {p["id"]: p for cid in positions for p in positions[cid]}

    new_statements = []

    def next_id() -> int:
        nums = [int(s["id"].rsplit("_s", 1)[1]) for s in data["statements"]]
        return max(nums, default=0) + 1

    for cid, have in scored_counts(data, race).items():
        need = PER_CANDIDATE - have
        if need <= 0 or not positions.get(cid):
            continue
        info = CANDIDATES[cid]
        prompt = TOPUP_PROMPT_TEMPLATE.format(
            name=info["name"], party=info["party"], office=RACE_TITLES[info["race"]].split(" — ")[0],
            have=have, need=need, cid=cid, state_name=race["state_name"],
            positions="\n".join(f"- [{p['id']}] {p['issue']}: {p['stance']}" for p in positions[cid]),
            used="\n".join(f"- {s['topic']}: {s['text']}" for s in data["statements"]) or "- (none)",
        )
        print(f"   Topping up {info['name']} ({have} → {PER_CANDIDATE} questions)...")
        try:
            new = call_json(prompt, step="topup").get("statements", [])
        except Exception as e:
            flag(flags, None, f"top-up failed ({e})", candidate=cid)
            continue
        added = 0
        for n in new[:need]:
            pos, score = by_id.get(n.get("position_id") or ""), n.get("score")
            if pos is None or not pos["id"].startswith(f"{cid}.") or not isinstance(score, int) or not 1 <= score <= 5:
                continue
            st = {
                "id": f"{race['id']}_s{next_id()}",
                "race": race["id"], "topic": n.get("topic", ""), "text": n.get("text", ""),
                "candidateAgreement": {c: None for c in race["candidates"]},
                "evidence": {}, "candidatePositions": {},
                "raceInsight": n.get("raceInsight", ""), "policyBackground": n.get("policyBackground", ""),
            }
            for c in race["candidates"]:
                set_unknown(st, c)
            st["candidateAgreement"][cid] = score
            st["evidence"][cid] = {"quote": pos["quote"], "source": pos["source"]}
            st["candidatePositions"][cid] = n.get("summary") or pos["stance"]
            data["statements"].append(st)
            new_statements.append(st)
            added += 1
        flag(flags, 6, f"topped up with {added} question(s) from their own positions", candidate=cid,
             item=[st["id"] for st in new_statements[len(new_statements) - added:]])
    return new_statements


def balance_direction(data: dict, race: dict, flags: list) -> None:
    """
    If a candidate's single-candidate questions are all "agree" (or all
    "disagree"), rewrite some as the real opposing policy and invert the
    score, so answering "strongly agree" to everything can't max out
    every candidate.
    """
    to_flip = []
    for cid in race["candidates"]:
        solo = [s for s in data["statements"]
                if s["candidateAgreement"].get(cid) is not None
                and sum(v is not None for v in s["candidateAgreement"].values()) == 1]
        scored = [s["candidateAgreement"][cid] for s in data["statements"]
                  if s["candidateAgreement"].get(cid) is not None]
        agree, disagree = sum(v >= 4 for v in scored), sum(v <= 2 for v in scored)
        side = [s for s in solo if (s["candidateAgreement"][cid] >= 4) == (agree > disagree)
                and s["candidateAgreement"][cid] != 3]
        while abs(agree - disagree) > 1 and side:
            st = side.pop()
            to_flip.append(st)
            if agree > disagree:
                agree, disagree = agree - 1, disagree + 1
            else:
                agree, disagree = agree + 1, disagree - 1
    if not to_flip:
        return
    print(f"   Rephrasing {len(to_flip)} question(s) as the opposing policy for balance...")
    batches = [to_flip[i:i + 20] for i in range(0, len(to_flip), 20)]

    def one(batch):
        items = "\n".join(f'- statement_id: {s["id"]}\n  statement: "{s["text"]}"' for s in batch)
        prompt = FLIP_PROMPT_TEMPLATE.format(items=items)
        # Saved, so an unchanged rerun words things the same way and the
        # verify checks that follow can be reused too
        key = cache_key(prompt, *step_settings("flip"))
        found = cache_get(f"flip_{key}", key)
        if found is not None:
            return found
        try:
            found = call_json(prompt, step="flip").get("rewrites", [])
        except Exception as e:
            flag(flags, None, f"direction balancing failed for {len(batch)} statement(s) ({e})")
            return []
        cache_put(f"flip_{key}", key, found)
        return found

    with ThreadPoolExecutor(WORKERS) as ex:
        result = {"rewrites": [r for rs in ex.map(one, batches) for r in rs]}
    by_id = {s["id"]: s for s in to_flip}
    for r in result.get("rewrites", []):
        st = by_id.get(r.get("statement_id"))
        if not st or not r.get("text"):
            continue
        old = st["text"]
        st["text"] = r["text"]
        st["policyBackground"] = r.get("policyBackground") or st.get("policyBackground", "")
        for c, v in st["candidateAgreement"].items():
            if v is not None:
                st["candidateAgreement"][c] = 6 - v
        st.setdefault("draft", old)  # kept so the glass box can show the first draft
        flag(flags, 5, f"rephrased as opposing policy (\"{old[:50]}\" → \"{st['text'][:50]}\")", st["id"])


# ---------------------------------------------------------------------------
# One race: the full question pool, a capped core, and a disagreement report
# ---------------------------------------------------------------------------

CORE_SIZE = 15        # questions every voter is asked first (heuristic 8)
CORE_PER_AREA = 2     # core questions from one area, while other areas still have some
AREA_CHUNK = 25       # positions per statement-writing call; bigger areas split by question
WORKERS = 10          # model calls run at the same time (the SDK retries if a rate limit is hit)

POOL_PROMPT_TEMPLATE = """
You are writing questions for a voter guide on the **{race_title}**. This
batch covers one issue area: **{area}**.

Below are candidates' documented positions in this area, grouped by the
policy question they answer. Candidates are shown by letter codes and
positions by ids; names are left out on purpose. Every position has already
been checked against its source.

{blocks}

---

## Your task

Write statements a voter rates from 1 (strongly disagree) to 5 (strongly
agree). Their agreement with a candidate is measured only on positions that
candidate has actually stated. Silence is never counted for or against.

### Which statements to write
1. **Shared debates first.** Where two or more candidates have positions on
   the same question, write one statement that scores all of them. Write it
   at the level where their positions actually meet: broad enough that each
   cited position answers it directly, never so broad that it stops being a
   real choice.
2. **Then every remaining position.** Write a statement for each position
   not yet used, scoring that candidate and anyone else whose position
   answers it directly.
3. **Skip only positions with no real choice in them**: slogans nobody
   opposes ("support great schools", "create good jobs") or biography.
   List each skipped position id with a short reason.
4. Write as many statements as these rules justify. There is no limit, but
   never two statements on the same policy.

### Writing statements
- Plain language for someone new to politics: one short sentence, no
  jargon, no acronyms without explanation.
- **One idea per statement.** Never combine several policies.
- **No reasons inside the statement.** State the policy, not why.
- Neutral wording: no loaded or emotional words, no names of candidates or
  bills that make one answer sound right.
- Every statement must be a policy real people disagree about, worded the
  way its supporters would say it, never as a negation or strawman ("X
  should be weakened").
- **Vary direction.** For single-candidate statements, phrase about a third
  as the real opposing policy, so the candidate disagrees (1 or 2), and
  agreeing never always means siding with the same people. Score each
  candidate on the statement as phrased.

### Scoring
For each candidate you score, cite the id of the position that answers the
statement and give a score:
- 5 = strongly agrees, 4 = mostly agrees, 3 = documented mixed or middle
  position, 2 = mostly disagrees, 1 = strongly disagrees.
- A position answers a statement when it takes a side on it. A specific
  proposal answers a broader statement it clearly takes a side on: capping
  developer fees answers "California should make it cheaper to build
  housing". A broad goal never answers a specific policy: supporting
  affordable housing does not answer a statement about rent control. A
  position on a related but different policy does not count. Never guess from party, and never use 3 for unknown. Leave out
  candidates you don't score.

### Also provide for each statement
- **candidatePositions**: one plain sentence per scored candidate restating
  only their cited position. Add nothing that is not in it.
- **raceInsight**: 1-2 sentences on why this issue matters to voters in
  {state_name}. Talk about the issue only: never describe or compare the
  candidates, and never mention how much information exists about them.
- **policyBackground**: 2-3 neutral sentences explaining the policy in the
  statement. Only explain terms that appear in the statement itself.

Respond with this exact JSON structure:
{{
  "statements": [
    {{
      "topic": "Rent Control",
      "text": "Statement text here.",
      "basis": {{"C1": "P3", "C4": "P9"}},
      "candidateAgreement": {{"C1": 5, "C4": 2}},
      "candidatePositions": {{"C1": "Their cited stance.", "C4": "Their cited stance."}},
      "raceInsight": "Why this issue matters here.",
      "policyBackground": "Neutral background."
    }}
  ],
  "skipped": [{{"position": "P7", "reason": "slogan with no policy choice"}}]
}}
"""

FILL_CANDIDATE_PROMPT_TEMPLATE = """
Below are one candidate's documented positions, and voter guide statements
this candidate has no score on yet. The candidate's name is left out on
purpose. For each statement, decide whether one of the positions
**directly** answers it.

- A position answers a statement when it takes a side on it. A specific
  proposal answers a broader statement it clearly takes a side on: capping
  developer fees answers "California should make it cheaper to build
  housing". A broad goal never answers a specific policy: supporting
  affordable housing does not answer a statement about rent control. A
  position on a related but different policy does not count.
- If one does, cite its id, give a score (5 strongly agrees, 4 mostly
  agrees, 3 documented mixed position, 2 mostly disagrees, 1 strongly
  disagrees), and write one plain sentence restating only that position.
- List only statements a position answers. Never guess from party.

## Positions
{positions}

## Statements
{statements}

Respond with this exact JSON structure:
{{"fills": [{{"statement": "S12", "position": "P1", "score": 4, "summary": "..."}}]}}
"""


def owner(pid: str) -> str:
    """The candidate id in a position id ("ca_gov_primary_katie_porter.3")."""
    return pid.rsplit(".", 1)[0]


def sides(s: dict) -> tuple:
    """(candidates scored, how many agree, how many disagree) on a statement."""
    vals = [v for v in s["candidateAgreement"].values() if v is not None]
    return len(vals), sum(v >= 4 for v in vals), sum(v <= 2 for v in vals)


def area_chunks(questions: list) -> list:
    """(area, questions) batches of at most AREA_CHUNK positions; a question is never split."""
    by_area = {}
    for q in questions:
        by_area.setdefault(q["area"], []).append(q)
    chunks = []
    for area, qs in by_area.items():
        batch, size = [], 0
        for q in sorted(qs, key=lambda q: -len(q["positions"])):
            if batch and size + len(q["positions"]) > AREA_CHUNK:
                chunks.append((area, batch))
                batch, size = [], 0
            batch.append(q)
            size += len(q["positions"])
        if batch:
            chunks.append((area, batch))
    return chunks


def area_blocks(qs: list, by_id: dict) -> tuple:
    """
    An area's positions as prompt text, grouped by question, with candidates
    as letter codes (C1, C2...) and positions as ids (P1, P2...). Returns
    (text, candidate codes, position codes).
    """
    c_alias, p_alias, blocks = {}, {}, []
    for q in qs:
        lines = [f"### Question: {q['question']}"]
        for pid in q["positions"]:
            c = c_alias.setdefault(owner(pid), f"C{len(c_alias) + 1}")
            p = p_alias.setdefault(pid, f"P{len(p_alias) + 1}")
            lines.append(f"- {c} [{p}] {by_id[pid]['issue']}: {by_id[pid]['stance']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), c_alias, p_alias


def map_back(raw_statements: list, c_alias: dict, p_alias: dict, area: str) -> list:
    """Statements from a coded prompt, with real candidate and position ids. Unknown codes are dropped."""
    c_back = {v: k for k, v in c_alias.items()}
    p_back = {v: k for k, v in p_alias.items()}

    def real(d):
        return {c_back[a]: v for a, v in (d or {}).items() if a in c_back}

    out = []
    for s in raw_statements:
        if not isinstance(s, dict) or not s.get("text"):
            continue
        out.append({
            "topic": s.get("topic", ""), "text": s["text"], "area": area,
            "basis": {c_back[a]: p_back.get(p) for a, p in (s.get("basis") or {}).items() if a in c_back},
            "candidateAgreement": real(s.get("candidateAgreement")),
            "candidatePositions": real(s.get("candidatePositions")),
            "raceInsight": s.get("raceInsight", ""), "policyBackground": s.get("policyBackground", ""),
        })
    return out


ROLLUP_PROMPT_TEMPLATE = """
You are writing questions for a voter guide on the **{race_title}**, issue
area **{area}**. Candidates are shown by letter codes and positions by ids;
names are left out on purpose. Every position has been checked against its
source.

## Positions in this area
{blocks}

## Statements already written for this area
{existing}

---

Many of these positions are specific proposals that no other candidate
mentions. Some of them still take a side on the same **broader debate**:
"cap developer fees" and "declare a housing emergency" both take a side on
"California should make it cheaper and faster to build housing". "Eliminate
property taxes" and "reassess commercial property at market value" take
**opposite** sides on "Property taxes in California should be lower".

Write broader debate statements like these:
- Each must be one that **at least two candidates'** positions take a side
  on. Debates where candidates take opposite sides are the most valuable.
- A specific proposal can count toward a broader debate it clearly takes a
  side on. A broad goal never counts toward a specific policy.
- Each must be a debate real voters split on. Never write something nearly
  everyone agrees with ("Housing should be affordable").
- Don't repeat a statement already written above.
- Plain, neutral, one short sentence, one idea, no reasons inside it.
- If no such debate exists in this area, return an empty list.

Score every candidate whose position takes a side, citing the position:
5 strongly agrees, 4 mostly agrees, 3 documented mixed or middle position,
2 mostly disagrees, 1 strongly disagrees. Never guess from party, never use
3 for unknown, and leave out candidates you don't score.

For each statement also give candidatePositions (one plain sentence per
scored candidate restating only their cited position), raceInsight (1-2
sentences on why the issue matters to voters in {state_name}, never about
the candidates), and policyBackground (2-3 neutral sentences explaining
only terms in the statement).

Respond with this exact JSON structure:
{{"statements": [{{"topic": "...", "text": "...", "basis": {{"C1": "P3", "C2": "P8"}},
  "candidateAgreement": {{"C1": 5, "C2": 1}}, "candidatePositions": {{"C1": "...", "C2": "..."}},
  "raceInsight": "...", "policyBackground": "..."}}]}}
"""


def rollup_area(race: dict, area: str, qs: list, existing: list, by_id: dict, flags: list) -> list:
    """
    The roll-up pass for one area: broader debates that two or more
    candidates' specific positions take a side on, often opposite sides.
    The specific statements stay in the pool for the deep dive.
    """
    blocks, c_alias, p_alias = area_blocks(qs, by_id)
    if len(c_alias) < 2:
        return []
    prompt = ROLLUP_PROMPT_TEMPLATE.format(
        race_title=race["title"], area=area, blocks=blocks, state_name=race["state_name"],
        existing="\n".join(f"- {s['text']}" for s in existing) or "- (none)")
    key = cache_key(prompt, *step_settings("rollup"))
    name = f"rollup_{race['id']}_{cache_key(area, *sorted(p_alias))}"
    raw = cache_get(name, key)
    if raw is None:
        try:
            raw = call_json(prompt, step="rollup")
        except Exception as e:
            flag(flags, None, f"roll-up for {area} failed ({e})")
            return []
        cache_put(name, key, raw)
    out = []
    for st in map_back(raw.get("statements", []), c_alias, p_alias, area):
        if len([v for v in st["candidateAgreement"].values() if v is not None]) >= 2:
            st["rollup"] = True
            out.append(st)
    return out


def write_area_statements(race: dict, area: str, qs: list, by_id: dict, flags: list,
                          retry: bool = True) -> tuple:
    """
    One call writes every statement one issue area justifies. Candidates and
    positions are shown by short codes, so the wording can't lean on names
    or party. Returns (statements with real ids, skipped positions).
    """
    blocks, c_alias, p_alias = area_blocks(qs, by_id)
    prompt = POOL_PROMPT_TEMPLATE.format(race_title=race["title"], area=area,
                                         blocks=blocks, state_name=race["state_name"])
    key = cache_key(prompt, *step_settings("write"))
    name = f"pool_{race['id']}_{cache_key(area, *sorted(p_alias))}"
    raw = cache_get(name, key)
    if raw is None:
        try:
            raw = call_json(prompt, step="write")
        except Exception as e:
            flag(flags, None, f"writing {area} statements failed ({e})")
            return [], []
        cache_put(name, key, raw)
    statements = map_back(raw.get("statements", []), c_alias, p_alias, area)
    p_back = {v: k for k, v in p_alias.items()}
    skipped = [{"position": p_back[x["position"]], "reason": x.get("reason", "")}
               for x in raw.get("skipped", []) if isinstance(x, dict) and x.get("position") in p_back]

    # Heuristic 6: every position is written into a statement or skipped with
    # a reason. Ones the model passed over silently get one more call.
    def left_over():
        used = {pid for st in statements for pid in st["basis"].values() if pid}
        done = used | {x["position"] for x in skipped}
        return [pid for q in qs for pid in q["positions"] if pid not in done]

    left = left_over()
    if left and retry:
        rest = [{**q, "positions": [pid for pid in q["positions"] if pid in left]} for q in qs]
        more, more_skipped = write_area_statements(race, area, [q for q in rest if q["positions"]],
                                                   by_id, flags, retry=False)
        statements += more
        skipped += more_skipped
        left = left_over()
    if left and retry:
        flag(flags, 6, f"{len(left)} position(s) in {area} not written into any statement; "
                       "shown on results cards", item=left)
    return statements, skipped


def fill_gaps_by_candidate(data: dict, race: dict, positions: dict, flags: list) -> None:
    """
    For each candidate, one call asks which unscored statements one of their
    positions answers directly. Catches positions grouped under another area
    ("Taxes" vs "Economy"). Calls run in parallel; scores are applied after.
    """
    stmts = data["statements"]
    s_alias = {s["id"]: f"S{i}" for i, s in enumerate(stmts, 1)}
    s_back = {v: k for k, v in s_alias.items()}
    by_sid = {s["id"]: s for s in stmts}
    jobs = [c for c in race["candidates"] if positions.get(c)]

    def one(cid):
        todo = [s for s in stmts if s["candidateAgreement"].get(cid) is None]
        if not todo:
            return cid, {}, []
        p_alias = {p["id"]: f"P{i}" for i, p in enumerate(positions[cid], 1)}
        prompt = FILL_CANDIDATE_PROMPT_TEMPLATE.format(
            positions="\n".join(f"- [{p_alias[p['id']]}] {p['issue']}: {p['stance']}" for p in positions[cid]),
            statements="\n".join(f'- {s_alias[s["id"]]}: "{s["text"]}"' for s in todo),
        )
        key = cache_key(prompt, *step_settings("fill"))
        found = cache_get(f"fill_{cid}", key)
        if found is None:
            try:
                found = call_json(prompt, step="fill").get("fills", [])
            except Exception as e:
                flag(flags, None, f"gap-filling failed ({e})", candidate=cid)
                return cid, {}, []
            cache_put(f"fill_{cid}", key, found)
        return cid, {v: k for k, v in p_alias.items()}, found

    print(f"   Checking every statement against each of {len(jobs)} candidates' positions...")
    filled = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for n, (cid, p_back, found) in enumerate(ex.map(one, jobs), 1):
            got = 0
            for f in found:
                sid = s_back.get(f.get("statement"))
                pid = p_back.get(f.get("position"))
                score = f.get("score")
                s = by_sid.get(sid)
                if not s or not pid or s["candidateAgreement"].get(cid) is not None:
                    continue
                if not isinstance(score, int) or not 1 <= score <= 5:
                    continue
                pos = next(p for p in positions[cid] if p["id"] == pid)
                s["candidateAgreement"][cid] = score
                s["evidence"][cid] = {"quote": pos["quote"], "source": pos["source"], "position": pid}
                s["candidatePositions"][cid] = f.get("summary") or pos["stance"]
                filled.append({"statement": sid, "candidate": cid, "position": pid, "score": score})
                got += 1
            print(f"     [{n}/{len(jobs)}] {CANDIDATES[cid]['name']}: {got} added")
    if filled:
        flag(flags, 2, f"gap-filling added {len(filled)} score(s) the first pass missed (checked below)",
             item=filled)


def rewrite_rejected(rejected: list, flags: list) -> list:
    """
    Statements rejected for wording (two policies bundled, a reason built in)
    get one rewrite each, so fixing the wording never erases a candidate's
    position. Rewrites keep their scores and are checked again by the caller.
    """
    by_id = {st["id"]: st for st, _ in rejected}
    items = [f'- statement_id: {st["id"]}\n  statement: "{st["text"]}"\n  rejected because: {note}'
             for st, note in rejected]
    batches = [items[i:i + 20] for i in range(0, len(items), 20)]
    print(f"   Rewriting {len(rejected)} statement(s) rejected for wording...")

    def one(batch):
        prompt = REWRITE_PROMPT_TEMPLATE.format(items="\n".join(batch))
        key = cache_key(prompt, *step_settings("rewrite"))
        found = cache_get(f"rewrite_{key}", key)
        if found is not None:
            return found
        try:
            found = call_json(prompt, step="rewrite").get("rewrites", [])
        except Exception as e:
            flag(flags, None, f"rewriting {len(batch)} rejected statement(s) failed ({e})")
            return []
        cache_put(f"rewrite_{key}", key, found)
        return found

    fixed = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for rewrites in ex.map(one, batches):
            for r in rewrites:
                st = by_id.pop(r.get("statement_id"), None)
                if not st or not r.get("text"):
                    continue
                st = copy.deepcopy(st)
                old = st["text"]
                st.setdefault("draft", old)
                st["text"] = r["text"]
                st["policyBackground"] = r.get("policyBackground") or st.get("policyBackground", "")
                flag(flags, 5, f'rewritten for wording ("{old[:50]}" → "{st["text"][:50]}"), checked again', st["id"])
                fixed.append(st)
    return fixed


def verify_in_batches(data: dict, flags: list, size: int = 15, rewrite: bool = False) -> None:
    """
    The second-pass check (verify_with_llm) in parallel batches. A batch whose
    reply runs out of room is split in half and checked again, down to one
    statement, so no score is left unchecked for length. The check fixes
    loaded wording in place; with rewrite=True, statements it couldn't fix
    get one more rewrite call and a second check.
    """
    stmts = data["statements"]
    batches = [stmts[i:i + size] for i in range(0, len(stmts), size)]
    print(f"   Checking scores against their quotes in {len(batches)} batch(es)...")
    rejected = []

    def one(b):
        batch = {"statements": list(b)}
        try:
            verify_with_llm(batch, flags, strict=True)
        except ModelStopped as e:
            if len(b) > 1:
                h = len(b) // 2
                print(f"     (a reply ran out of room; splitting {len(b)} statements into {h} + {len(b) - h})")
                return one(b[:h]) + one(b[h:])
            flag(flags, None, f"verification failed ({e}); scores are unchecked", b[0]["id"])
            return list(b)
        rejected.extend(batch.get("rejected", []))
        return batch["statements"]

    with ThreadPoolExecutor(WORKERS) as ex:
        data["statements"] = [st for kept in ex.map(one, batches) for st in kept]
    if rewrite and rejected:
        again = {"statements": rewrite_rejected(rejected, flags)}
        if again["statements"]:
            verify_in_batches(again, flags, size, rewrite=False)
            data["statements"] += again["statements"]


def select_core(statements: list) -> list:
    """
    Heuristic 8: the core every voter is asked first, at most CORE_SIZE.
    No two core questions rest on the same candidate's same quote, so one
    position is never asked about twice in different words.
    Shared statements (2+ candidates take a side) come first, those with
    candidates on opposite sides ahead, then by how many take a side, with
    no more than CORE_PER_AREA from one area while others have some left.
    When a race has fewer shared statements than that (a small field),
    single-candidate statements fill the rest, going to whichever candidate
    has the fewest core questions so far. Picked from the field, never from
    anything about the voter. Sets s["core"] to the rank, or None.
    """
    def rank(s):
        n, a, d = sides(s)
        return (-(min(a, d) > 0), -n, -min(a, d), s["id"])

    open_ = [s for s in statements if not s.get("consensus")]   # nearly everyone agrees: tells no one anything
    shared = sorted((s for s in open_ if sides(s)[0] >= 2), key=rank)
    core, chosen, per_area, used = [], set(), Counter(), set()

    def rests_on(s):
        """(candidate, position) pairs a statement's scores rest on."""
        ev = s.get("evidence") or {}
        return {(c, (ev.get(c) or {}).get("position") or s["id"])
                for c, v in s["candidateAgreement"].items() if v is not None}

    def take(s):
        core.append(s)
        chosen.add(s["id"])
        per_area[s["area"]] += 1
        used.update(rests_on(s))

    for limit in (CORE_PER_AREA, CORE_SIZE):   # the second pass lifts the area limit
        for s in shared:
            if len(core) >= CORE_SIZE:
                break
            if s["id"] in chosen or per_area[s["area"]] >= limit:
                continue
            if rests_on(s) & used:
                continue   # same candidate, same quote: a near-repeat of a question already asked
            take(s)
    solo = [s for s in open_ if sides(s)[0] == 1]
    on_core = Counter(c for s in core for c, v in s["candidateAgreement"].items() if v is not None)

    def who(s):
        return next(c for c, v in s["candidateAgreement"].items() if v is not None)

    solo = [s for s in solo if not rests_on(s) & used]
    while len(core) < CORE_SIZE and solo:
        s = min(solo, key=lambda s: (on_core[who(s)], per_area[s["area"]], s["id"]))
        solo = [x for x in solo if x is not s and not rests_on(x) & rests_on(s)]
        take(s)
        on_core[who(s)] += 1
    for s in statements:
        s["core"] = None
    for i, s in enumerate(core, 1):
        s["core"] = i
    return core


def pool_report(all_ids: list, positions: dict, statements: list, core: list, skipped: list) -> dict:
    """How many statements put candidates on opposite sides, and who the core reaches."""
    with_pos = [c for c in all_ids if positions.get(c)]
    counts = [sides(s) for s in statements]
    on = {c: sum(1 for s in statements if s["candidateAgreement"].get(c) is not None) for c in with_pos}
    on_core = {c: sum(1 for s in core if s["candidateAgreement"].get(c) is not None) for c in with_pos}
    size = Counter("1" if n == 1 else "2-4" if n < 5 else "5-9" if n < 10 else "10+" for n, _, _ in counts)
    cited = {(s.get("evidence") or {}).get(c, {}).get("position")
             for s in statements for c in s["candidateAgreement"]
             if s["candidateAgreement"][c] is not None and (s.get("evidence") or {}).get(c)}
    return {
        "candidates": {"in_race": len(all_ids), "with_positions": len(with_pos)},
        "statements": len(statements),
        "shared": sum(1 for n, _, _ in counts if n >= 2),
        "opposite_sides": sum(1 for _, a, d in counts if a and d),
        "consensus": sum(1 for s in statements if s.get("consensus")),
        "rollups": sum(1 for s in statements if s.get("rollup")),
        "rollups_opposite_sides": sum(1 for s in statements if s.get("rollup") and sides(s)[1] and sides(s)[2]),
        "by_candidates_scored": {k: size.get(k, 0) for k in ("1", "2-4", "5-9", "10+")},
        "areas": dict(Counter(s["area"] for s in statements).most_common()),
        "core": [{"rank": s["core"], "area": s["area"], "text": s["text"],
                  "scored": sides(s)[0], "agree": sides(s)[1], "disagree": sides(s)[2]} for s in core],
        "core_reaches": sum(1 for c in with_pos if on_core[c]),
        "not_in_core": [c for c in with_pos if not on_core[c]],
        "on_no_statement": [c for c in with_pos if not on[c]],
        "positions_skipped": len(skipped),
        # Checked positions no statement uses: still shown on the candidate's results card
        "positions_card_only": sum(1 for c in with_pos for p in positions[c] if p["id"] not in cited),
        "per_candidate": {c: {"positions": len(positions[c]), "statements": on[c], "core": on_core[c]}
                          for c in with_pos},
    }


def print_pool(race: dict, report: dict) -> None:
    name = lambda cid: CANDIDATES[cid]["name"]
    b = report["by_candidates_scored"]
    print(f"\n📊 Question pool, {race['title']}")
    print(f"   {report['statements']} statements from {report['candidates']['with_positions']} candidates "
          f"with positions ({report['candidates']['in_race']} in the race)")
    print(f"   {report['shared']} shared by 2+ candidates; {report['opposite_sides']} have candidates on opposite sides; "
          f"{report['consensus']} nearly every voter would agree with (kept out of the core)")
    print(f"   {report['rollups']} broader debates from the roll-up, "
          f"{report['rollups_opposite_sides']} with opposite sides")
    print(f"   Candidates per statement:  1: {b['1']}   2-4: {b['2-4']}   5-9: {b['5-9']}   10+: {b['10+']}")
    print(f"   {report['positions_skipped']} position(s) skipped as slogans; "
          f"{report['positions_card_only']} shown on results cards only")
    print(f"\n   Core ({len(report['core'])} of max {CORE_SIZE}), asked of every voter first:")
    for c in report["core"]:
        side = f"{c['agree']} agree / {c['disagree']} disagree"
        print(f"     {c['rank']:>2}. [{c['scored']} scored: {side}] {c['area']} · {c['text']}")
    print(f"\n   The core reaches {report['core_reaches']} of {report['candidates']['with_positions']} candidates")
    if report["not_in_core"]:
        print(f"   Not in the core (deep dive only): {', '.join(name(c) for c in report['not_in_core'])}")
    if report["on_no_statement"]:
        print(f"   On no statement at all: {', '.join(name(c) for c in report['on_no_statement'])}")


def process_pool(race_id: str, verify: bool = True) -> dict:
    """
    Step 2 for a single race: every statement the race's positions justify,
    written one issue area at a time, then checked; the core picked from
    them; and a report on how many put candidates on opposite sides.
    Saved to data/processed/statements_<race id>.json.
    """
    race, run_race = race_for_run(race_id)
    print(f"\n⚖️  Question pool for {race['title']}: {len(run_race['candidates'])} of "
          f"{len(race['candidates'])} candidates")
    print(f"   Models: {step_settings('write')[0]} drafts, extracts and fills; "
          f"{step_settings('verify')[0]} groups and checks")
    flags = []
    sources = load_sources(run_race)
    # Bios depend on nothing else, so they run alongside the rest
    side = ThreadPoolExecutor(1)
    bios_job = side.submit(build_bios, run_race, sources, flags)
    with timed("positions"):
        positions = extract_positions(run_race, sources, flags)
    with timed("grouping"):
        questions = group_positions(run_race, positions, flags)
    by_id = {p["id"]: p for c in positions for p in positions[c]}

    chunks = area_chunks(questions)
    print(f"   Writing statements for {len(chunks)} area batch(es), {WORKERS} at a time...")
    statements, skipped = [], []

    def one(job):
        area, qs = job
        return area, qs, write_area_statements(run_race, area, qs, by_id, flags)

    with timed("writing"), ThreadPoolExecutor(WORKERS) as ex:
        for n, (area, qs, (st, sk)) in enumerate(ex.map(one, chunks), 1):
            statements += st
            skipped += sk
            print(f"     [{n}/{len(chunks)}] {area}: {len(st)} statement(s) from "
                  f"{sum(len(q['positions']) for q in qs)} position(s), {len(sk)} skipped")
    if skipped:
        flag(flags, 5, f"{len(skipped)} position(s) skipped as slogans with no policy choice; "
                       "shown on results cards", item=skipped)

    # Roll-up: broader debates that specific positions take sides on, per area
    areas = {}
    for q in questions:
        areas.setdefault(q["area"], []).append(q)
    print(f"   Looking for broader debates across {len(areas)} area(s), {WORKERS} at a time...")

    def roll(item):
        area, qs = item
        return area, rollup_area(run_race, area, qs, [s for s in statements if s["area"] == area], by_id, flags)

    with timed("roll-up"), ThreadPoolExecutor(WORKERS) as ex:
        found = list(ex.map(roll, areas.items()))
    for n, (area, ups) in enumerate(found, 1):
        statements += ups
        if ups:
            print(f"     {area}: {len(ups)} broader debate(s)")

    data = {"statements": statements}
    check_schema(data, run_race)
    for i, s in enumerate(data["statements"], 1):
        s["id"], s["race"] = f"{race['id']}_s{i}", race["id"]
    attach_evidence(data, run_race, positions, flags)
    with timed("gap filling"):
        fill_gaps_by_candidate(data, run_race, positions, flags)
    drop_unscorable(data, flags, "before check")
    # No separate rewording pass: the writer already varies direction, and
    # the check fixes loaded wording in place (see verify_with_llm)
    if verify:
        with timed("checking"):
            verify_in_batches(data, flags)
        drop_unscorable(data, flags, "after check")
    with timed("bios (alongside)"):
        bios = bios_job.result()
    side.shutdown()
    check_structure(data, run_race, flags)
    core = select_core(data["statements"])
    report = pool_report(race["candidates"], positions, data["statements"], core, skipped)

    output = {
        "run": run_record(run_race),
        "race_id": race["id"],
        "race_title": race["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": active_model(),
        "fast_model": active_model(fast=True),
        "verified": verify,
        "core_size": CORE_SIZE,
        "report": report,
        "cost": METER.summary(),
        "timing": dict(TIMES),
        "statements": data["statements"],
        "flags": flags,
        "flag_counts": {
            (f"H{h}" if h else "system"): sum(1 for f in flags if f["heuristic"] == h)
            for h in dict.fromkeys(f["heuristic"] for f in flags)
        },
        "questions": questions,
        "positions": positions,
        "bios": bios,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"statements_{race['id']}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print_pool(race, report)
    print(f"\n   ✓ Saved → data/processed/{out_path.name}")
    system = [f for f in flags if f["heuristic"] is None]
    if system:
        print(f"   ⚠ {len(system)} step(s) failed; see flags in the file:")
        for f in system:
            print(f"      - {flag_text(f)}")
    return output


def bio_excerpt(segments: list, lines_per_source: int = 30) -> tuple:
    """
    Background text for the bio: the opening of the Wikipedia article if there
    is one (where biography lives), otherwise the campaign About page.
    Returns (excerpt, kind) where kind is "wikipedia" or "bio page".
    """
    for kind in ("wikipedia", "bio page"):
        segs = [s for s in segments if s.kind == kind]
        if segs:
            lines = [l for l in segs[0].lines if not l.startswith("#")][:lines_per_source]
            return f"### Source: {segs[0].source}\n" + "\n".join(lines), kind
    return "", None


def build_bios(race: dict, sources: dict, flags: list) -> dict:
    """Short sourced background per candidate; unsupported sentences are dropped."""
    bios = {}

    def one(cid):
        """(cid, kind, saved bio or None, model sentences or None)"""
        info = CANDIDATES[cid]
        excerpt, kind = bio_excerpt(sources.get(cid, []))
        if not excerpt:
            flag(flags, 1, "no Wikipedia or About page, no bio", candidate=cid)
            return cid, kind, None, None, None
        prompt = BIO_PROMPT_TEMPLATE.format(
            name=info["name"], party=info["party"],
            office=RACE_TITLES[info["race"]].split(" — ")[0], excerpt=excerpt,
        )
        key = cache_key(prompt, *step_settings("bios"))
        cached = cache_get(f"bio_{cid}", key)
        if cached is not None:
            return cid, kind, key, cached, None
        try:
            return cid, kind, key, None, call_json(prompt, step="bios").get("sentences", [])
        except Exception as e:
            flag(flags, None, f"bio failed ({e})", candidate=cid)
            return cid, kind, None, None, None

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(one, race["candidates"]))
    written = 0
    for cid, kind, key, cached, found in results:
        if cached is not None:
            bios[cid] = cached
            continue
        if found is None:
            continue
        written += 1
        sentences, dropped = [], []
        for snt in found:
            match = match_quote(snt.get("quote", ""), sources[cid])
            if match and snt.get("text"):
                sentences.append({"text": snt["text"], "quote": match[1], "source": match[0]})
            else:
                dropped.append(snt)
        if dropped:
            flag(flags, 1, f"{len(dropped)} bio sentence(s) dropped, quote not found in sources",
                 candidate=cid, item=dropped)
        # "self-described" when the only source is the candidate's own About page
        bios[cid] = {"sentences": sentences, "selfDescribed": kind == "bio page"}
        cache_put(f"bio_{cid}", key, bios[cid])
    print(f"   Bios: {written} written, {len(bios) - written} reused")
    return bios


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_race(race: dict, verify: bool = True):
    print(f"\n⚖️  Generating statements for: {race['title']}")
    sources = load_sources(race)
    flags = []

    # Step 1: each candidate's checked positions, and a sourced background
    positions = extract_positions(race, sources, flags)
    bios = build_bios(race, sources, flags)

    # Step 2: statements built only from those positions
    print("   Writing statements...")
    try:
        data = call_json(build_statement_prompt(race, positions), step="statements")
        check_schema(data, race)
    except (json.JSONDecodeError, AssertionError, ModelStopped) as e:
        print(f"   ❌ Could not use LLM output: {e}")
        return None

    for i, s in enumerate(data["statements"], 1):
        s["id"], s["race"] = f"{race['id']}_s{i}", race["id"]

    attach_evidence(data, race, positions, flags)
    fill_gaps(data, race, positions, flags)
    drop_unscorable(data, flags, "before check")
    top_up(data, race, positions, flags)
    balance_direction(data, race, flags)
    if verify:
        verify_with_llm(data, flags)
        drop_unscorable(data, flags, "after check")
        # The checker can remove scores; replace those losses from the same
        # candidate's positions, and check the replacements too.
        new = top_up(data, race, positions, flags)
        if new:
            batch = {"statements": new}
            verify_with_llm(batch, flags)
            kept_ids = {s["id"] for s in batch["statements"]}
            data["statements"] = [s for s in data["statements"]
                                  if s not in new or s["id"] in kept_ids]
            drop_unscorable(data, flags, "after second check")
    equalize_coverage(data, race, positions, flags)
    check_structure(data, race, flags)

    # How many distinct public sources each candidate's scores rest on, so the
    # app can say "based on 1 source" when a profile is thin
    sources_used = {
        cid: sorted({s["evidence"][cid]["source"] for s in data["statements"]
                     if (s.get("evidence") or {}).get(cid)})
        for cid in race["candidates"]
    }

    output = {
        "run": run_record(race),
        "sourcesUsed": sources_used,
        "race_id": race["id"],
        "race_title": race["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": active_model(),
        "fast_model": active_model(fast=True),
        "verified": verify,
        "statements": data["statements"],
        "flags": flags,
        # How often each heuristic fired, for the rules scorecard
        "flag_counts": {
            (f"H{h}" if h else "system"): sum(1 for f in flags if f["heuristic"] == h)
            for h in dict.fromkeys(f["heuristic"] for f in flags)
        },
        # Step 1 output, kept for review (not used by the app)
        "positions": positions,
        # Sourced backgrounds, shown in the results dropdown
        "bios": bios,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"statements_{race['id']}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"   ✓ {len(data['statements'])} statements saved → data/processed/{out_path.name}")
    print(f"   📋 Topics: {', '.join(s['topic'] for s in data['statements'])}")
    if flags:
        print(f"   🚩 {len(flags)} flag(s) to review:")
        for f in flags:
            print(f"      - {flag_text(f)}")
    else:
        print("   ✓ No flags")
    return output


def elapsed(started: float) -> str:
    m, sec = divmod(int(time.monotonic() - started), 60)
    return f"{m} min {sec} s" if m else f"{sec} s"


def main():
    # Print each line as it happens, even through a pipe (| tee), so a long
    # run never looks frozen
    sys.stdout.reconfigure(line_buffering=True)
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="ZipVote Wedge Agent")
    parser.add_argument("--no-verify", action="store_true", help="Skip the second-pass LLM check")
    parser.add_argument("--state", help="Only generate one state's ballot (e.g. CA)", default=None)
    parser.add_argument("--race", metavar="RACE_ID",
                        help="Write one race's full question pool and pick its core "
                             "(e.g. ca_gov_primary)")
    parser.add_argument("--positions", metavar="RACE_ID",
                        help="Only list and group one race's positions and report how much they "
                             "overlap (e.g. ca_gov_primary). Writes no statements.")
    args = parser.parse_args()

    if args.race:
        process_pool(args.race, verify=not args.no_verify)
        METER.print()
        print_times()
        print(f"\n✅ Done in {elapsed(started)}. Read every flag before wiring into the app.")
        return
    if args.positions:
        positions_only(args.positions)
        METER.print()
        print(f"   ⏱  {elapsed(started)}")
        return

    STATE_NAMES = {"MA": "Massachusetts", "CA": "California"}
    only = args.state.upper() if args.state else None

    for state in dict.fromkeys(r["state"] for r in RACES):
        if only and state != only:
            continue
        # Cost cuts before any model call:
        #  - candidates with no scraped sources are skipped (nothing to quote)
        #  - races with fewer than 2 remaining candidates are skipped
        #    (uncontested, or only one side has sources: nothing to compare)
        included = []
        for r in (r for r in RACES if r["state"] == state):
            with_sources = [c for c in r["candidates"] if (RAW_DIR / f"{c}.txt").exists()]
            for c in set(r["candidates"]) - set(with_sources):
                print(f"   – {CANDIDATES[c]['name']}: no sources, skipped")
            if len(with_sources) < 2:
                print(f"   – {r['title']}: fewer than 2 candidates with sources, race skipped")
                continue
            included.append({**r, "candidates": with_sources})
        if not included:
            continue

        # One question set per state's ballot, so a topic several candidates
        # share (health care, energy...) is asked once, across offices.
        ballot = {
            "id": f"ballot_{state}",
            "state_name": STATE_NAMES.get(state, state),
            "title": f"{STATE_NAMES.get(state, state)} ballot, Nov 3 2026: "
                     + ", ".join(r["title"].split(" — ")[0] for r in included),
            "candidates": [c for r in included for c in r["candidates"]],
        }
        print(f"\n🗳️  {ballot['title']} — {len(ballot['candidates'])} candidates")
        print(f"   Models: {active_model()} (writing, checking), {active_model(fast=True)} (extraction)")
        process_race(ballot, verify=not args.no_verify)
    METER.print()
    print(f"\n✅ Done in {elapsed(started)}. Read every flag in data/processed/ before wiring into the app.")


if __name__ == "__main__":
    main()
