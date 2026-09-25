# ZipVote Agents

This package contains the Scout and Wedge agents that power ZipVote's data pipeline.

## Setup

```bash
cd "/Users/pat/Harvard/Personal Projects/ZipVote"
pip install requests beautifulsoup4 openai   # or google-generativeai for Gemini
```

## Step 1: Scout (Scrape Candidate Positions)

```bash
python -m agents.scout               # all candidates
python -m agents.scout --candidate markey  # single candidate
```

Output: `data/raw/{candidate_id}.txt`

## Step 2: Wedge (Generate Questions via LLM)

Set your API key first:
```bash
export ANTHROPIC_API_KEY=...    # Claude (default: claude-sonnet-4-5)
# OR
export OPENAI_API_KEY=sk-...    # OpenAI (default: gpt-4o)
# OR
export GEMINI_API_KEY=...       # Google Gemini (default: gemini-2.5-pro)

export ZIPVOTE_MODEL=...        # optional: override the default model
```

```bash
python -m agents.wedge               # all races
python -m agents.wedge --race senate # single race
```

Output: `data/processed/questions_{race_id}.json`

Add `--no-verify` to skip the second-pass check (cheaper, less safe).

### Guardrails

- **Relevant excerpts:** each candidate's source text is ranked for position
  language and trimmed to the best ~12k characters, not cut at a fixed length.
- **Unknown is not neutral:** no documented position → score `null` and
  "Not enough public information." The app skips nulls when matching.
- **Every score needs a quote:** the quote must appear word for word in the
  scraped source, or the score becomes `null`. The source URL is attached.
- **Second-pass check:** another model call drops scores their quote doesn't
  support and flags loaded wording.
- **Structure checks:** one-sided topics, statements that don't separate
  candidates, and candidates with thin data are flagged.

Every problem is listed under `"flags"` in the output JSON.

> ⚠️ **Read the flags before wiring into the app.**

## Adding New Candidates / Races

Edit `agents/config.py` — add entries to `CANDIDATES` and `RACES`.
