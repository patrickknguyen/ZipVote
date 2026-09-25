"""
Wedge Agent — reads raw candidate source text and uses an LLM to write
Likert statements with a per-candidate agreement score.

Guardrails (all run at generation time, nothing runs per user):
  1. Relevant excerpts, not a blind cutoff. Each candidate's source text is
     ranked line by line for position language (voted, supports, bill...) and
     the best lines are kept up to a character budget.
  2. Unknown is not neutral. A candidate with no documented position on a
     statement gets `null` ("Not enough public information"), never a 3.
  3. Every score needs a quote. The model must copy a verbatim quote from the
     source text for each score. If the quote can't be found in the source,
     the score is dropped to null and the race is flagged.
  4. Second-pass check (skip with --no-verify). A separate LLM call reads each
     score next to its quote and drops any score the quote doesn't support.
     It also flags loaded or one-sided wording.
  5. Structural checks: each topic's two statements should point in opposite
     directions, each statement should actually separate candidates, and
     candidates with thin data are flagged.

Anything flagged is written to the output file under "flags" for review.

Outputs JSON to data/processed/statements_{race_id}.json

Usage:
    export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY
    export ZIPVOTE_MODEL=...       # optional: override the default model

    python -m agents.wedge
    python -m agents.wedge --race senate
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


# ---------------------------------------------------------------------------
# Source text: split by source, pick the most relevant lines
# ---------------------------------------------------------------------------

SOURCE_HEADER = re.compile(
    r"^#{1,3}\s*(?:Source|Ballotpedia|Wikipedia|Campaign site)\s*:\s*(https?://\S+)", re.I
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
            current = Segment(url_match.group(1) if url_match else "manual notes")
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

    ranked = [
        (line_score(line), si, li)
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


# ---------------------------------------------------------------------------
# Topic count scaling
# ---------------------------------------------------------------------------

def compute_topic_count(n_candidates: int) -> int:
    """
    Scale the number of topics with candidate count. Each topic gets 2 statements.
      2 candidates → 2 topics → 4 statements
      3 candidates → 3 topics → 6 statements
      4 candidates → 4 topics → 8 statements
      5+ candidates → 5 topics → 10 statements (cap)
    """
    return max(2, min(n_candidates - 1, 5))


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

WEDGE_PROMPT_TEMPLATE = """
You are helping first-time voters understand where candidates in the
November 3, 2026 general election actually differ.

Below are source excerpts for candidates in the **{race_title}**. Each excerpt
is grouped under "### Source: <url>" headers.

{candidate_blocks}

---

## Your Task

Write exactly **{num_statements} statements** grouped into **{n_topics} topics**,
2 statements per topic. The user will rate each statement from 1 (strongly
disagree) to 5 (strongly agree).

### Choosing topics
- Study the excerpts and pick the {n_topics} policy areas where these candidates
  differ most, based on what the sources document.
- This is a general election, so candidates usually come from different
  parties or run as independents. Prefer concrete differences a voter would
  not guess from party labels alone (a specific vote, bill, or plan), over
  broad party-line splits.
- Only pick a topic if at least two candidates have documented positions on
  it. Candidates with little public record will often be unknown; that is
  expected and fine.
- Give each topic a 1–3 word label (e.g. "Drug Prices", "Defense Spending").

### Writing statements
- Plain language for someone new to politics: one or two short sentences,
  no jargon, no acronyms without explanation.
- Neutral wording. No loaded or emotional words, no framing that makes one
  answer sound obviously right.
- **One idea per statement.** Never combine several policies ("X and Y and
  Z"); a voter must be able to agree or disagree with the whole thing.
- **No reasons inside the statement.** State the policy, not why someone
  might want it ("Sex work should be legal", not "...because it is safer").
- **Balance**: the two statements in a topic must point in opposite
  directions, so agreeing is not always the same "side".
- Prefer positions from the last few years over older ones when both exist.
- Each statement must separate at least two candidates by 2 or more points.

### Scoring each candidate (1–5)
- 5 = strongly agrees, 4 = mostly agrees, 3 = a documented mixed or middle
  position, 2 = mostly disagrees, 1 = strongly disagrees.
- **Every score must be backed by a quote** copied word for word from that
  candidate's excerpt: one or two consecutive sentences, at least 25
  characters, no edits, no ellipses.
- If the excerpt does not document the candidate's position on the
  statement, the score is **null**, the position is "{unknown}", and the
  evidence is null. Never guess from party, and never use 3 for unknown.

### Also provide for each statement
- **raceInsight**: 1–2 sentences on why this issue matters to voters in this
  place. Talk about the issue only: never describe or compare the candidates,
  and never mention how much information exists about them.
- **policyBackground**: 2–3 neutral sentences of context.
- **candidatePositions**: one plain sentence per candidate that restates
  only what their quote says. Add nothing that is not in the quote: no extra
  policies, outcomes, vote counts, or party comparisons. Use the same kind of
  noun the quote uses (a "resolution" is not "legislation"). Or "{unknown}".

### Candidate parties
{party_lines}

Respond with this exact JSON structure:
{{
  "race_id": "{race_id}",
  "statements": [
    {{
      "id": "s1",
      "topic": "Drug Prices",
      "text": "Statement text here.",
      "candidateAgreement": {{"candidate_a": 5, "candidate_b": null}},
      "evidence": {{
        "candidate_a": {{"quote": "Exact words copied from the excerpt."}},
        "candidate_b": null
      }},
      "candidatePositions": {{
        "candidate_a": "Their documented stance.",
        "candidate_b": "{unknown}"
      }},
      "raceInsight": "Why this matters in this race.",
      "policyBackground": "Neutral background."
    }}
  ]
}}

Use candidate ids exactly as given: {candidate_ids}.
Statements for the same topic must be adjacent in the array.
"""

VERIFY_PROMPT_TEMPLATE = """
You are checking a voter guide for accuracy and neutrality. Be strict.

For each item below, a candidate was given a 1–5 agreement score on a
statement, backed by a quote from a source, plus a one-sentence summary of
their position. Decide:
  - supported: does the quote, on its own, clearly support the score? If the
    quote is about something else, is too vague, or supports a different
    score by more than 1 point, mark it unsupported.
  - summary_faithful: does the summary say only what the quote says? Mark it
    false if it adds any claim, detail, or comparison not in the quote.

Also review each statement's wording. Mark it loaded if it uses emotional or
one-sided language, makes one answer sound obviously correct, combines more
than one policy, or includes a reason for the policy.

## Scores to check
{score_items}

## Statements to review for wording
{statement_items}

Respond with this exact JSON structure:
{{
  "scores": [
    {{"statement_id": "s1", "candidate_id": "x", "supported": true, "summary_faithful": true, "note": "short reason"}}
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
            sources[cid] = parse_sources(raw_path.read_text(encoding="utf-8"))
        else:
            print(f"   ⚠ No raw data for {cid} — run scout first")
            sources[cid] = []
    return sources


def build_prompt(race: dict, sources: dict) -> str:
    blocks = []
    for cid in race["candidates"]:
        info = CANDIDATES[cid]
        excerpt = select_excerpt(sources[cid]) if sources[cid] else ""
        excerpt = excerpt or "[No source text available for this candidate.]"
        blocks.append(f"## {info['name']} ({info['party']}, id: {cid})\n\n{excerpt}")

    n_topics = compute_topic_count(len(race["candidates"]))
    return WEDGE_PROMPT_TEMPLATE.format(
        race_title=race["title"],
        race_id=race["id"],
        candidate_blocks="\n\n---\n\n".join(blocks),
        party_lines=build_party_lines(race),
        n_topics=n_topics,
        num_statements=n_topics * 2,
        unknown=UNKNOWN_POSITION,
        candidate_ids=", ".join(race["candidates"]),
    )


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
        for key in ("id", "topic", "text", "candidateAgreement"):
            assert key in s, f"Statement {s.get('id', '?')} missing '{key}'"
        s.setdefault("evidence", {})
        s.setdefault("candidatePositions", {})
        for cid in race["candidates"]:
            s["candidateAgreement"].setdefault(cid, None)
        # Drop any candidate ids the model invented
        for key in ("candidateAgreement", "evidence", "candidatePositions"):
            for cid in list(s[key]):
                if cid not in race["candidates"]:
                    del s[key][cid]


def check_quotes(data: dict, sources: dict, flags: list) -> None:
    """Guardrail 3: every non-null score needs a verbatim quote from the sources."""
    for s in data["statements"]:
        for cid, score in list(s["candidateAgreement"].items()):
            if score is None:
                set_unknown(s, cid)
                continue
            if not isinstance(score, int) or not 1 <= score <= 5:
                flags.append(f"{s['id']} / {cid}: invalid score {score!r}, set to unknown")
                set_unknown(s, cid)
                continue
            ev = s["evidence"].get(cid) or {}
            source = locate_quote(ev.get("quote", ""), sources.get(cid, []))
            if source is None:
                flags.append(f"{s['id']} / {cid}: quote not found in sources, set to unknown")
                set_unknown(s, cid)
                continue
            s["evidence"][cid] = {"quote": ev["quote"].strip(), "source": source}
            if not source.startswith("http"):
                flags.append(f"{s['id']} / {cid}: backed only by manual notes, needs a real source")


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
        result = parse_json(call_llm(prompt))
    except Exception as e:  # a failed check must never silently pass
        flags.append(f"verification call failed ({e}); scores are unchecked")
        return

    by_id = {s["id"]: s for s in data["statements"]}
    for item in result.get("scores", []):
        s = by_id.get(item.get("statement_id"))
        cid = item.get("candidate_id")
        if not s or cid not in s["candidateAgreement"]:
            continue
        if item.get("supported") is False and s["candidateAgreement"][cid] is not None:
            flags.append(f"{s['id']} / {cid}: quote doesn't support score ({item.get('note', '')}), set to unknown")
            set_unknown(s, cid)
        elif item.get("summary_faithful") is False:
            flags.append(f"{s['id']} / {cid}: summary adds claims not in the quote ({item.get('note', '')}), rewrite it")
    for item in result.get("wording", []):
        if item.get("loaded"):
            flags.append(f"{item.get('statement_id')}: wording problem ({item.get('note', '')})")


def check_structure(data: dict, race: dict, flags: list) -> None:
    """Guardrail 5: balance, separation, and thin data."""
    statements = data["statements"]
    expected = compute_topic_count(len(race["candidates"])) * 2
    if len(statements) != expected:
        flags.append(f"expected {expected} statements, got {len(statements)}")

    by_topic = {}
    for s in statements:
        by_topic.setdefault(s["topic"], []).append(s)
        known = [v for v in s["candidateAgreement"].values() if v is not None]
        if len(known) < 2 or max(known) - min(known) < 2:
            flags.append(f"{s['id']}: doesn't separate candidates (known scores: {known})")

    for topic, group in by_topic.items():
        if len(group) != 2:
            flags.append(f"topic '{topic}' has {len(group)} statements (expected 2)")
            continue
        tops = []
        for s in group:
            known = {c: v for c, v in s["candidateAgreement"].items() if v is not None}
            # Framing balance only means something when 2+ candidates are known
            tops.append(max(known, key=known.get) if len(known) >= 2 else None)
        if tops[0] is not None and tops[0] == tops[1]:
            flags.append(f"topic '{topic}': both statements favor {tops[0]}, framing may be one-sided")

    for cid in race["candidates"]:
        unknown = sum(1 for s in statements if s["candidateAgreement"].get(cid) is None)
        if statements and unknown > len(statements) / 2:
            flags.append(f"{cid}: unknown on {unknown} of {len(statements)} statements, thin source data")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_race(race: dict, verify: bool = True):
    n_topics = compute_topic_count(len(race["candidates"]))
    print(f"\n⚖️  Generating statements for: {race['title']}")
    print(f"   {len(race['candidates'])} candidates → {n_topics} topics → {n_topics * 2} statements")

    sources = load_sources(race)
    prompt = build_prompt(race, sources)

    print("   Calling LLM...")
    raw_response = call_llm(prompt)
    try:
        data = parse_json(raw_response)
        check_schema(data, race)
    except (json.JSONDecodeError, AssertionError) as e:
        print(f"   ❌ Could not use LLM output: {e}")
        print(f"   Raw response:\n{raw_response[:500]}")
        return None

    for i, s in enumerate(data["statements"], 1):
        s["id"], s["race"] = f"{race['id']}_s{i}", race["id"]

    flags = []
    check_quotes(data, sources, flags)
    if verify:
        verify_with_llm(data, flags)
    check_structure(data, race, flags)

    output = {
        "race_id": race["id"],
        "race_title": race["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": active_model(),
        "verified": verify,
        "statements": data["statements"],
        "flags": flags,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"statements_{race['id']}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"   ✓ {len(data['statements'])} statements saved → {out_path}")
    print(f"   📋 Topics: {', '.join(sorted({s['topic'] for s in data['statements']}))}")
    if flags:
        print(f"   🚩 {len(flags)} flag(s) to review:")
        for f in flags:
            print(f"      - {f}")
    else:
        print("   ✓ No flags")
    return output


def main():
    parser = argparse.ArgumentParser(description="ZipVote Wedge Agent")
    parser.add_argument("--race", help="Process a single race by ID (e.g. senate)", default=None)
    parser.add_argument("--no-verify", action="store_true", help="Skip the second-pass LLM check")
    args = parser.parse_args()

    races = [r for r in RACES if r["id"] == args.race] if args.race else RACES
    if not races:
        print(f"❌ Unknown race: {args.race}")
        return

    print(f"🗳️  ZipVote Wedge Agent — processing {len(races)} race(s)")
    for race in races:
        process_race(race, verify=not args.no_verify)

    print("\n✅ Done. Read every flag in data/processed/ before wiring into the app.")


if __name__ == "__main__":
    main()
