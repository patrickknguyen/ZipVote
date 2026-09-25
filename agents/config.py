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
  - skip_wikipedia: True when the candidate's name matches a different,
    better-known person on Wikipedia.
  - confirm_on_ballot: True when ballot status came from Ballotpedia or
    Wikipedia and still needs checking against the official list from the
    Massachusetts Secretary of the Commonwealth.

Races below are the November 3, 2026 general election for zip 02144
(Somerville, MA): U.S. Senate, U.S. House MA-7, and Governor.
"""

RACES = [
    {
        "id": "senate",
        "title": "U.S. Senate — Massachusetts, Nov 3 2026",
        "candidates": ["markey", "deaton"],
    },
    {
        "id": "house_ma7",
        "title": "U.S. House — Massachusetts 7th District, Nov 3 2026",
        "candidates": ["pressley", "linardon"],
    },
    {
        "id": "governor",
        "title": "Governor — Massachusetts, Nov 3 2026",
        "candidates": ["healey", "minogue", "james", "kokonezis_hanino"],
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
    },
    "deaton": {
        "id": "deaton",
        "name": "John Deaton",
        "party": "Republican",
        "race": "senate",
        "state": "MA",
        "ballotpedia_slug": "John_Deaton_(Massachusetts)",
        "skip_wikipedia": True,
    },
    # ── U.S. House, MA-7 ─────────────────────────────────────────────────────
    "pressley": {
        "id": "pressley",
        "name": "Ayanna Pressley",
        "party": "Democrat",
        "race": "house_ma7",
        "state": "MA",
    },
    "linardon": {
        "id": "linardon",
        "name": "Kelechi Linardon",
        "party": "Independent",
        "race": "house_ma7",
        "state": "MA",
        "skip_wikipedia": True,
        "confirm_on_ballot": True,
    },
    # ── Governor ─────────────────────────────────────────────────────────────
    "healey": {
        "id": "healey",
        "name": "Maura Healey",
        "party": "Democrat",
        "race": "governor",
        "state": "MA",
    },
    "minogue": {
        "id": "minogue",
        "name": "Mike Minogue",
        "party": "Republican",
        "race": "governor",
        "state": "MA",
        "ballotpedia_slug": "Mike_Minogue",
    },
    "james": {
        "id": "james",
        "name": "Andrea James",
        "party": "Independent",
        "race": "governor",
        "state": "MA",
        "ballotpedia_slug": "Andrea_James_(Massachusetts)",
        "skip_wikipedia": True,  # a different Andrea James has a Wikipedia page
        "confirm_on_ballot": True,
    },
    "kokonezis_hanino": {
        "id": "kokonezis_hanino",
        "name": "Muhammed Kokonezis-Hanino",
        "party": "Independent",
        "race": "governor",
        "state": "MA",
        "skip_wikipedia": True,
        "confirm_on_ballot": True,
    },
}
