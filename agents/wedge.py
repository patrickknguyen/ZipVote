"""
Wedge Agent — reads raw candidate source text and uses an LLM to write
Likert statements with a per-candidate agreement score.

Two steps, with guardrails (all at generation time, nothing per user):
  1. Positions. One call per candidate lists the positions their sources
     state explicitly, each with a verbatim quote. Positions whose quote
     isn't found word for word in the sources are dropped.
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

Outputs JSON to data/processed/statements_ballot.json (one question set for
the whole ballot, so shared topics are asked once across offices).

Usage:
    export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY
    export ZIPVOTE_MODEL=...       # optional: override the default model

    python -m agents.wedge
    python -m agents.wedge --no-verify
"""

import argparse
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agents.config import RACES, CANDIDATES

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RACE_TITLES = {r["id"]: r["title"] for r in RACES}
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

EXCERPT_BUDGET_CHARS = 12_000   # per candidate, roughly 3k tokens
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

def active_model() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ.get("ZIPVOTE_MODEL", "claude-sonnet-4-5")
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ.get("ZIPVOTE_MODEL", "gpt-4o")
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ.get("ZIPVOTE_MODEL", "gemini-2.5-pro")
    raise EnvironmentError(
        "No LLM API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY."
    )


def call_llm(prompt: str) -> str:
    """Send a prompt to the available LLM and return the text response."""
    model = active_model()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_anthropic(prompt, model)
    if os.environ.get("OPENAI_API_KEY"):
        return _call_openai(prompt, model)
    return _call_gemini(prompt, model)


def _call_anthropic(prompt: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT + " Respond ONLY with valid JSON, no markdown fences.",
        messages=[{"role": "user", "content": prompt}],
    )
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


def call_json(prompt: str, retries: int = 2) -> dict:
    """Call the model and parse JSON, asking again if the reply isn't valid JSON."""
    last_error = None
    for attempt in range(retries + 1):
        ask = prompt if attempt == 0 else (
            prompt + "\n\nYour previous reply was not valid JSON "
            f"({last_error}). Reply again with valid JSON only. Escape any double "
            "quotes inside strings as \\\"."
        )
        try:
            return parse_json(call_llm(ask))
        except json.JSONDecodeError as e:
            last_error = e
            if attempt < retries:
                print(f"     (reply wasn't valid JSON, retrying {attempt + 1}/{retries})")
    raise last_error


# ---------------------------------------------------------------------------
# Source text: split by source, pick the most relevant lines
# ---------------------------------------------------------------------------

SOURCE_HEADER = re.compile(
    r"^#{1,3}\s*(Source|Ballotpedia|Wikipedia|Campaign site|Bio page)\s*:\s*(https?://\S+)", re.I
)
MANUAL_HEADER = re.compile(r"^#{1,3}\s*Manual notes", re.I)

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
    source: str                     # URL, or "manual notes"
    lines: list = field(default_factory=list)
    kind: str = "source"            # "campaign site", "wikipedia", ...

    @property
    def is_manual(self) -> bool:
        return not self.source.startswith("http")


def parse_sources(text: str) -> list:
    """Split a raw candidate file into segments, one per source header."""
    segments = []
    current = Segment("manual notes")
    for line in text.splitlines():
        stripped = line.strip()
        url_match = SOURCE_HEADER.match(stripped)
        if url_match or MANUAL_HEADER.match(stripped):
            if current.lines:
                segments.append(current)
            current = (Segment(url_match.group(2), kind=url_match.group(1).lower())
                       if url_match else Segment("manual notes"))
            continue
        if stripped:
            current.lines.append(stripped)
    if current.lines:
        segments.append(current)
    # Drop a leading segment that is only the file's title line
    return [s for s in segments if any(not l.startswith("# ") for l in s.lines)]


def line_score(line: str) -> int:
    return 2 * len(POSITION_WORDS.findall(line)) + len(ISSUE_WORDS.findall(line))


def select_excerpt(segments: list, budget: int = EXCERPT_BUDGET_CHARS) -> str:
    """
    Keep manual notes in full, then fill the rest of the budget with the
    highest-scoring lines from scraped sources. Lines stay in their original
    order under their source header so the model can cite them.
    """
    keep = set()   # (segment index, line index)
    used = 0

    for si, seg in enumerate(segments):
        if seg.is_manual:
            for li, line in enumerate(seg.lines):
                if used + len(line) > budget:
                    break
                keep.add((si, li))
                used += len(line) + 1

    # Campaign sites state current positions in the candidate's own words, so
    # their lines outrank Wikipedia's (which skews toward older, notable moments).
    ranked = [
        (line_score(line) + (3 if seg.kind == "campaign site" else 0), si, li)
        for si, seg in enumerate(segments) if not seg.is_manual
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


def locate_quote(quote: str, segments: list):
    """Return the source a quote came from, or None if it isn't verbatim in any source."""
    if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
        return None
    needle = _norm(quote).strip(" .\"'")
    for seg in segments:
        if needle in _norm(" ".join(seg.lines)):
            return seg.source
    return None


MAX_STATEMENTS = 12      # for the whole ballot: one statement per topic
PER_CANDIDATE = 3        # target statements each candidate is scored on
MAX_POSITIONS = 12       # per candidate, from step 1
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
- When both a campaign page and older history cover an issue, quote the
  campaign page: it is the candidate's current position in their own words.

For each position give:
- "issue": a 1–3 word label (e.g. "Drug Prices", "Nuclear Power")
- "stance": one plain sentence restating only what the quote says
- "quote": one or two consecutive sentences copied **word for word** from
  the excerpt, at least 25 characters, no edits, no ellipses

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
- The cited position must speak to this statement **directly**. A position
  on a related but different policy means null. Never guess from party,
  and never use 3 for unknown.

### Also provide for each statement
- **candidatePositions**: one plain sentence per scored candidate restating
  only their cited position. Add nothing that is not in it.
- **raceInsight**: 1–2 sentences on why this issue matters to voters in
  Massachusetts. Talk about the issue only: never describe or compare the
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

- Directly means the same specific policy question. A position on a
  related but different policy does not count.
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
      "raceInsight": "1-2 sentences on why this issue matters to Massachusetts voters, about the issue only.",
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
    statement the candidate is on (agree or disagree)? Mark it unsupported
    only if the quote is about a different policy or the side is unclear.
  - best_score: how strongly the candidate holds this position, 1–5, based
    on what the quote says (a firm commitment is 5 or 1; a qualified or
    partial one is 4 or 2). Judge the candidate's stance, **not** how
    directly the quote is worded: an indirect quote showing firm support is
    still a 5.
  - direct: true if the quote states this exact policy; false if it shows
    the candidate's side through a closely related action or statement.
  - summary_faithful: does the summary say only what the quote says? Mark it
    false if it adds any claim, detail, or comparison not in the quote.

Also review each statement's wording. Mark it loaded if it uses emotional or
one-sided language, makes one answer sound obviously correct, combines more
than one policy, includes a reason for the policy, or describes a position
no real candidate or advocate would put that way (for example "X should be
weakened" or "loopholes should remain open"). Loaded statements are removed.

## Scores to check
{score_items}

## Statements to review for wording
{statement_items}

Respond with this exact JSON structure:
{{
  "scores": [
    {{"statement_id": "s1", "candidate_id": "x", "supported": true, "best_score": 5, "direct": false, "summary_faithful": true, "note": "short reason"}}
  ],
  "wording": [
    {{"statement_id": "s1", "loaded": false, "note": "short reason"}}
  ]
}}
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


def verify_with_llm(data: dict, flags: list) -> None:
    """Guardrail 4: a second model call checks each score against its quote."""
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
    print("   Checking scores against their quotes...")
    try:
        result = call_json(prompt)
    except Exception as e:  # a failed check must never silently pass
        flags.append(f"verification call failed ({e}); scores are unchecked")
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
        if item.get("supported") is False:
            flags.append(f"{s['id']} / {cid}: quote doesn't show a side ({item.get('note', '')}), set to unknown")
            set_unknown(s, cid)
            continue
        best = item.get("best_score")
        if isinstance(best, int) and 1 <= best <= 5 and best != current:
            # Same side, different strength: correct it rather than erase it.
            # A change that flips sides (e.g. 5 → 2) means the quote doesn't
            # support the original reading, so that becomes unknown instead.
            if (best >= 3) == (current >= 3) or best == 3 or current == 3:
                s["candidateAgreement"][cid] = best
                flags.append(f"{s['id']} / {cid}: score adjusted {current} → {best} ({item.get('note', '')})")
            else:
                flags.append(f"{s['id']} / {cid}: quote points the other way ({current} vs {best}), set to unknown")
                set_unknown(s, cid)
                continue
        # Record how directly the quote states the policy (shown in the app)
        if item.get("direct") is False:
            s.setdefault("evidenceDirect", {})[cid] = False
        if item.get("summary_faithful") is False:
            flags.append(f"{s['id']} / {cid}: summary adds claims not in the quote ({item.get('note', '')}), rewrite it")
    loaded = set()
    for item in result.get("wording", []):
        if item.get("loaded"):
            loaded.add(item.get("statement_id"))
            flags.append(f"dropped (wording): {item.get('statement_id')} ({item.get('note', '')})")
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
        flags.append(f"dropped (coverage): \"{drop['text'][:70]}\" so {cid} doesn't outnumber others")


def check_structure(data: dict, race: dict, flags: list) -> None:
    """Direction balance and thin-data checks on the statements that survived."""
    statements = data["statements"]
    if len(statements) < 3:
        flags.append(f"only {len(statements)} usable statement(s)")

    # Direction: if a candidate agrees (4-5) with every one of their scored
    # statements, "agree" always means siding with them.
    for cid in race["candidates"]:
        vals = [s["candidateAgreement"][cid] for s in statements
                if s["candidateAgreement"].get(cid) is not None]
        if len(vals) >= 3 and all(v >= 4 for v in vals):
            flags.append(f"{cid}: agrees with all {len(vals)} statements, phrase some the other way")
        if len(vals) < 2:
            flags.append(f"{cid}: scored on only {len(vals)} statement(s), too few to show a score")


def drop_unscorable(data: dict, flags: list, stage: str) -> None:
    """Remove statements that can't compare at least MIN_SCORED candidates."""
    kept = []
    for s in data["statements"]:
        known = [v for v in s["candidateAgreement"].values() if v is not None]
        if len(known) >= MIN_SCORED:
            kept.append(s)
        else:
            flags.append(f"dropped ({stage}): \"{s['text'][:70]}\" had {len(known)} scored candidate(s)")
    data["statements"] = kept


def extract_positions(race: dict, sources: dict, flags: list) -> dict:
    """
    Step 1: one call per candidate to list their stated positions, then keep
    only positions whose quote is verbatim in the sources.
    Returns candidate id → list of {id, issue, stance, quote, source}.
    """
    positions = {}
    for cid in race["candidates"]:
        info = CANDIDATES[cid]
        positions[cid] = []
        if not sources.get(cid):
            flags.append(f"{cid}: no sources, no positions")
            continue
        prompt = EXTRACT_PROMPT_TEMPLATE.format(
            name=info["name"], party=info["party"], race_title=RACE_TITLES[info["race"]],
            excerpt=select_excerpt(sources[cid]), max_positions=MAX_POSITIONS,
        )
        print(f"   Listing {info['name']}'s positions...")
        try:
            found = call_json(prompt).get("positions", [])
        except Exception as e:
            flags.append(f"{cid}: position listing failed ({e})")
            continue
        missed = 0
        for p in found:
            source = locate_quote(p.get("quote", ""), sources[cid])
            if source is None:
                missed += 1
                continue
            positions[cid].append({
                "id": f"{cid}.{len(positions[cid]) + 1}",
                "issue": p.get("issue", ""),
                "stance": p.get("stance", ""),
                "quote": p["quote"].strip(),
                "source": source,
            })
        print(f"     {len(positions[cid])} positions kept, {missed} dropped (quote not in source)")
        if missed:
            flags.append(f"{cid}: {missed} listed position(s) dropped, quote not found in sources")
    return positions


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
                flags.append(f"{s['id']} / {cid}: score {score!r} without a valid cited position, set to unknown")
                set_unknown(s, cid)
            else:
                s["evidence"][cid] = {"quote": pos["quote"], "source": pos["source"]}


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
        result = call_json(FILL_PROMPT_TEMPLATE.format(items="\n\n".join(items)))
    except Exception as e:
        flags.append(f"gap-filling call failed ({e})")
        return

    stmts = {s["id"]: s for s in data["statements"]}
    filled = 0
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
        filled += 1
    print(f"     filled {filled}")
    if filled:
        flags.append(f"gap-filling added {filled} score(s) the first pass missed (checked below)")


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
            have=have, need=need, cid=cid,
            positions="\n".join(f"- [{p['id']}] {p['issue']}: {p['stance']}" for p in positions[cid]),
            used="\n".join(f"- {s['topic']}: {s['text']}" for s in data["statements"]) or "- (none)",
        )
        print(f"   Topping up {info['name']} ({have} → {PER_CANDIDATE} questions)...")
        try:
            new = call_json(prompt).get("statements", [])
        except Exception as e:
            flags.append(f"{cid}: top-up failed ({e})")
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
        flags.append(f"{cid}: topped up with {added} question(s) from their own positions")
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
    items = "\n".join(f'- statement_id: {s["id"]}\n  statement: "{s["text"]}"' for s in to_flip)
    try:
        result = call_json(FLIP_PROMPT_TEMPLATE.format(items=items))
    except Exception as e:
        flags.append(f"direction balancing failed ({e})")
        return
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
        flags.append(f"{st['id']}: rephrased as opposing policy (\"{old[:50]}\" → \"{st['text'][:50]}\")")


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
    for cid in race["candidates"]:
        info = CANDIDATES[cid]
        excerpt, kind = bio_excerpt(sources.get(cid, []))
        if not excerpt:
            flags.append(f"{cid}: no Wikipedia or About page, no bio")
            continue
        print(f"   Writing {info['name']}'s bio from {kind}...")
        try:
            found = call_json(BIO_PROMPT_TEMPLATE.format(
                name=info["name"], party=info["party"],
                office=RACE_TITLES[info["race"]].split(" — ")[0], excerpt=excerpt,
            )).get("sentences", [])
        except Exception as e:
            flags.append(f"{cid}: bio failed ({e})")
            continue
        sentences = []
        for snt in found:
            source = locate_quote(snt.get("quote", ""), sources[cid])
            if source and snt.get("text"):
                sentences.append({"text": snt["text"], "quote": snt["quote"].strip(), "source": source})
        dropped = len(found) - len(sentences)
        if dropped:
            flags.append(f"{cid}: {dropped} bio sentence(s) dropped, quote not found in sources")
        # "self-described" when the only source is the candidate's own About page
        bios[cid] = {"sentences": sentences, "selfDescribed": kind == "bio page"}
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
        data = call_json(build_statement_prompt(race, positions))
        check_schema(data, race)
    except (json.JSONDecodeError, AssertionError) as e:
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

    output = {
        "race_id": race["id"],
        "race_title": race["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": active_model(),
        "verified": verify,
        "statements": data["statements"],
        "flags": flags,
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
            print(f"      - {f}")
    else:
        print("   ✓ No flags")
    return output


def main():
    parser = argparse.ArgumentParser(description="ZipVote Wedge Agent")
    parser.add_argument("--no-verify", action="store_true", help="Skip the second-pass LLM check")
    args = parser.parse_args()

    # One question set for the whole ballot, so a topic several candidates
    # share (health care, energy...) is asked once, across offices.
    ballot = {
        "id": "ballot",
        "title": "Massachusetts ballot, Nov 3 2026: " + ", ".join(r["title"].split(" — ")[0] for r in RACES),
        "candidates": [c for r in RACES for c in r["candidates"]],
    }
    print(f"🗳️  ZipVote Wedge Agent — {len(ballot['candidates'])} candidates across {len(RACES)} races")
    process_race(ballot, verify=not args.no_verify)
    print("\n✅ Done. Read every flag in data/processed/ before wiring into the app.")


if __name__ == "__main__":
    main()
