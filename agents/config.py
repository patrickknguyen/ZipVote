"""
Agent configuration: candidates and race definitions for the ZipVote pipeline.

This file intentionally contains NO hardcoded URLs. The Scout agent
auto-constructs and validates Ballotpedia + Wikipedia URLs from candidate
names at runtime, making this configuration scale to any race in any state
without manual URL curation.

To add a new race:
  1. Add an entry to RACES with a unique id, title, and list of candidate ids.
  2. Add each candidate to CANDIDATES with id, name, party, race, and state.
  3. Run `python -m agents.scout` — URLs are discovered automatically.

Source strategy (enforced by the scout, not here):
  1. Ballotpedia  — primary source for issue positions and campaign themes.
  2. Wikipedia    — primary source for biography and legislative history.
  Both are third-party curated with visible citations, minimising hallucination
  risk on politically sensitive content.

Candidate options:
  - ballotpedia_slug: exact Ballotpedia page name when the plain name is
    ambiguous (non-incumbents usually need a state suffix).
  - campaign_urls: the candidate's own issue or platform pages. Read first
    and ranked highest, since they state current positions in their words.
  - skip_wikipedia: True when the candidate has no Wikipedia page and their
    name matches a different person's.
  - wikipedia_slug: exact Wikipedia page name when the plain name belongs to
    someone else.
  - bio_urls: an About page, used for the bio only when the candidate has no
    Wikipedia page. The app labels bios from these as self-described.
  - exclude_lines: text snippets; any source line containing one is dropped
    before generation. Use when a spot-check finds a source is wrong, and
    note why and the correcting source next to it.
  - confirm_on_ballot: True when ballot status came from Ballotpedia or
    Wikipedia and still needs checking against the official list from the
    Massachusetts Secretary of the Commonwealth.

Races below are the November 3, 2026 general election for zip 02144
(Somerville, MA), checked against the Secretary of the Commonwealth's
official candidate list on Sep 25, 2026:
  - U.S. Senate and Governor are contested and included.
  - U.S. House MA-7 is left out: Ayanna Pressley is unopposed, so there is
    nothing to compare.
"""

RACES = [
    {
        "id": "senate",
        "title": "U.S. Senate — Massachusetts, Nov 3 2026",
        "candidates": ["markey", "deaton"],
    },
    {
        "id": "governor",
        "title": "Governor — Massachusetts, Nov 3 2026",
        "candidates": ["healey", "minogue", "james"],
    },
]

CANDIDATES = {
    # ── U.S. Senate ──────────────────────────────────────────────────────────
    "markey": {
        "id": "markey",
        "name": "Ed Markey",
        "party": "Democrat",
        "race": "senate",
        "state": "MA",
        "campaign_urls": [
            "https://www.edmarkey.com/issues-priorities/pathways-to-opportunity/",
            "https://www.edmarkey.com/issues-priorities/affordable-care-for-all/",
            "https://www.edmarkey.com/issues-priorities/building-a-resilient-future/",
            "https://www.edmarkey.com/issues-priorities/freedom-equality-opportunity/",
            "https://www.edmarkey.com/issues-priorities/justice-safety-for-all/",
        ],
    },
    "deaton": {
        "id": "deaton",
        "name": "John Deaton",
        "party": "Republican",
        "race": "senate",
        "state": "MA",
        "ballotpedia_slug": "John_Deaton_(Massachusetts)",
        "campaign_urls": ["https://www.johndeatonforsenate.com/issues"],
        # No Wikipedia page, so the bio comes from his campaign's About page
        "bio_urls": ["https://www.johndeatonforsenate.com/meet-john"],
        "skip_wikipedia": True,
    },
    # ── Governor ─────────────────────────────────────────────────────────────
    "healey": {
        "id": "healey",
        "name": "Maura Healey",
        "party": "Democrat",
        "race": "governor",
        "state": "MA",
        # No issues page on the campaign site; this is her stated record
        "campaign_urls": ["https://maurahealey.com/accomplishments/"],
        # Human corrections: source lines that are wrong, removed before generation.
        # Wikipedia said the 2026 law removed "all restrictions"; the cited NBC
        # Boston article says it changed the rules for abortions at 24+ weeks.
        # https://www.nbcboston.com/news/local/massachusets-new-abortion-law-signed/3995004/
        "exclude_lines": ["removing all restrictions on abortion"],
    },
    "minogue": {
        "id": "minogue",
        "name": "Mike Minogue",
        "party": "Republican",
        "race": "governor",
        "state": "MA",
        "ballotpedia_slug": "Michael_Minogue",
        "campaign_urls": ["https://minogueforma.com/blueprint-for-a-better-future/"],
        # No Wikipedia page, so the bio comes from his campaign's About page
        "bio_urls": ["https://minogueforma.com/meet-mike/"],
    },
    "james": {
        "id": "james",
        "name": "Andrea James",
        "party": "Independent",
        "race": "governor",
        "state": "MA",
        "ballotpedia_slug": "Andrea_James",
        # "Andrea_James" on Wikipedia is a different person; hers is this one
        "wikipedia_slug": "Andrea_C._James",
        # The 13 issue pages linked from https://www.ajforma.com/platform
        # (the platform page itself only shows headings)
        "campaign_urls": [
            "https://www.ajforma.com/housing-affordability",
            "https://www.ajforma.com/universal-healthcare",
            "https://www.ajforma.com/universal-childcare",
            "https://www.ajforma.com/education",
            "https://www.ajforma.com/immigration-protection",
            "https://www.ajforma.com/lowering-utility-costs",
            "https://www.ajforma.com/labor-rights",
            "https://www.ajforma.com/lgbtq-protections",
            "https://www.ajforma.com/climate-and-clean-energy",
            "https://www.ajforma.com/ai-and-data-centers",
            "https://www.ajforma.com/invest-in-communities-not-prisons",
            "https://www.ajforma.com/no-war-no-genocide",
            "https://www.ajforma.com/justice-and-freedom",
        ],
    },
}
