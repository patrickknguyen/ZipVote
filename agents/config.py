"""
Agent configuration: candidates and race definitions for the ZipVote pipeline.

Scout finds each candidate's sources from public records (state voter
guide, FEC filings, Wikipedia) and follows them to the candidate's own site,
so most candidates need only a name, party, race and state.

To add a new race:
  1. Add an entry to RACES with a unique id, title, and list of candidate ids.
  2. Add each candidate to CANDIDATES with id, name, party, race, and state.
  3. Run `python -m agents.scout` — URLs are discovered automatically.

Source strategy is described in agents/scout.py. Every source is a public
page; nothing is typed in by hand.

Race options:
  - voter_guide: {"format": name, "live": url, "archive": url} for the
    state's official candidate statement page. "format" picks the parser in
    scout.GUIDE_PARSERS (each state's guide is laid out differently). As-of
    runs read the state's "archive" if it keeps one, otherwise the Internet
    Archive's copy of the live page.
  - office: the office's name ("Governor", "Attorney General"), used to
    check that a page is about this race. Defaults to the start of the title.
  - election: "YYYY-MM-DD type". Manifests set it for every race; the FEC
    lookup takes the year from it.
  - fec_office: "S" or "H" for federal races, so Scout can find the
    campaign's website on its FEC filing. House races also need "district".
  - identity_terms: words a third-party page must mention to count as this
    race's candidate, when the defaults (office name) aren't enough.

Candidate options:
  - campaign_urls: the candidate's own issue pages, when Scout can't find
    them from public records. Read as listed, with no link following.
  - fec_candidate_id: the FEC id when the name search finds more than one.
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

Each race has a "state"; each state gets its own ballot and question set.
Massachusetts races below are the November 3, 2026 general election checked against the Secretary of the Commonwealth's
official candidate list on Sep 25, 2026:
  - U.S. Senate and Governor are contested and included.
  - U.S. House MA-7 is left out: Ayanna Pressley is unopposed, so there is
    nothing to compare.
"""

RACES = [
    {
        "id": "senate",
        "state": "MA",
        "fec_office": "S",
        "title": "U.S. Senate — Massachusetts, Nov 3 2026",
        "candidates": ["markey", "deaton"],
    },
    {
        "id": "governor",
        "state": "MA",
        "title": "Governor — Massachusetts, Nov 3 2026",
        "candidates": ["healey", "minogue", "james"],
    },
    # California has no U.S. Senate race in 2026. Governor is a top-two
    # general: Becerra and Hilton advanced from the June 2 primary.
    {
        "id": "ca_governor",
        "state": "CA",
        # Statements candidates wrote for the official guide. The live page
        # moves on to the next election; the archive keeps June 2, 2026.
        "voter_guide": {
            "format": "ca_sos",
            "live": "https://voterguide.sos.ca.gov/candidates/governor-candidate-statements.htm",
            "archive": "https://vigarchive.sos.ca.gov/2026/primary/candidates/governor-candidate-statements.htm",
        },
        "title": "Governor — California, Nov 3 2026",
        "candidates": ["becerra", "hilton"],
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
    # ── California governor ──────────────────────────────────────────────
    "becerra": {
        "id": "becerra",
        "name": "Xavier Becerra",
        "party": "Democrat",
        "race": "ca_governor",
        "state": "CA",
        "campaign_urls": [
            "https://www.xavierbecerra2026.com/priorities/health-care/",
            "https://www.xavierbecerra2026.com/priorities/housing/",
            "https://www.xavierbecerra2026.com/priorities/economy-and-affordability/",
            "https://www.xavierbecerra2026.com/priorities/energy-utilities/",
            "https://www.xavierbecerra2026.com/priorities/california-disaster-preparedness-resilience/",
            "https://www.xavierbecerra2026.com/priorities/ai/",
            "https://www.xavierbecerra2026.com/priorities/homelessness/",
            "https://www.xavierbecerra2026.com/priorities/filmindustry/",
            "https://www.xavierbecerra2026.com/priorities/power-hour/",
            "https://www.xavierbecerra2026.com/priorities/protecting-our-families-communities-from-the-next-wildfire-disaster/",
            "https://www.xavierbecerra2026.com/priorities/fighting-donald-trump/",
        ],
    },
    "hilton": {
        "id": "hilton",
        "name": "Steve Hilton",
        "party": "Republican",
        "race": "ca_governor",
        "state": "CA",
        # This page loads with JavaScript; if the scraper gets almost no
        # text it is skipped, never filled in by hand
        "campaign_urls": ["https://stevehiltonforgovernor.com/policies"],
    },
}


# ── Races loaded from manifests ──────────────────────────────────────────────
# Each file in data/manifests/ is one election's official candidate list,
# transcribed from the state's certified list and citing it. Candidates get
# only a name, party, race and state: Scout finds every source itself, the
# same way for everyone.

import json as _json
import re as _re
import unicodedata as _ud
from pathlib import Path as _Path

_MANIFESTS = _Path(__file__).parent.parent / "data" / "manifests"


def _slug(name: str) -> str:
    text = "".join(c for c in _ud.normalize("NFKD", name) if not _ud.combining(c))
    return _re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# Races written above, before manifests, are the November 3 general election
for _r in RACES:
    _r.setdefault("election", "2026-11-03 general")

for _path in sorted(_MANIFESTS.glob("*.json")):
    _m = _json.loads(_path.read_text(encoding="utf-8"))
    for _race in _m["races"]:
        _ids = []
        for _c in _race["candidates"]:
            _cid = f"{_race['id']}_{_slug(_c['name'])}"
            assert _cid not in CANDIDATES, f"duplicate candidate id {_cid}"
            CANDIDATES[_cid] = {"id": _cid, "name": _c["name"], "party": _c["party"],
                                "race": _race["id"], "state": _m["state"],
                                **{k: v for k, v in _c.items() if k not in ("name", "party")}}
            _ids.append(_cid)
        RACES.append({**{k: v for k, v in _race.items() if k != "candidates"},
                      "state": _m["state"], "election": _m["election"],
                      "manifest": _path.name, "candidates": _ids})
