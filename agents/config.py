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
  - voter_guide: {"live": url, "archive": url} for the state's official
    candidate statement page. As-of runs read the archive.
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
