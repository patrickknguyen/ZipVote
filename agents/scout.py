"""
Scout Agent: finds each candidate's public sources and saves them as plain
text in data/raw/, ready for the Wedge agent.

Sources, in the order they're tried:
  1. State voter guide. Statements candidates wrote themselves for the
     official guide, where the state publishes one (California does for
     statewide offices). Also gives the candidate's website, if listed.
  2. FEC. The campaign committee's official website, which federal
     candidates must list on their Statement of Organization.
  3. Wikipedia. Biography and record, plus the website in the infobox.
  4. Campaign site. The candidate's own issue pages, found by following
     links from the websites above. Never found by open web search.

Every page must be public, so anyone can check it. Nothing is typed in by
hand.

Two modes:
  - Live (default): reads every source as it is today. This is how the tool
    would run before an election.
  - As of a date (--as-of YYYY-MM-DD): reads every source as it stood on that
    date, to reproduce a past election. Wikipedia is read at the last edit
    on or before the date, campaign sites from the Internet Archive's last
    copy on or before it, and the voter guide from the state's archive. A
    source with no copy from on or before the date is left out.

Every source is cited by a link to the exact version that was read: a
Wikipedia permanent link, an Internet Archive copy, or the live page with the
time it was read. Each candidate also gets data/raw/<id>.sources.json listing
what was read, how, and what was skipped and why.

Usage:
    python -m agents.scout
    python -m agents.scout --as-of 2026-06-01
    python -m agents.scout --race ca_governor
    python -m agents.scout --candidate markey

Optional: export FEC_API_KEY=... (free from api.data.gov). Without it the
FEC's shared DEMO_KEY is used, which is heavily rate limited.
"""

import argparse
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from agents.config import CANDIDATES, RACES

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"

HEADERS = {"User-Agent": "ZipVote-Scout/2.0 (civic research; https://patrickknguyen.github.io)"}
WIKI_API = "https://en.wikipedia.org/w/api.php"
FEC_API = "https://api.open.fec.gov/v1"
CDX_API = "https://web.archive.org/cdx/search/cdx"
DELAY = {"en.wikipedia.org": 0.5, "web.archive.org": 1.5, "api.open.fec.gov": 0.5}
DEFAULT_DELAY = 1.5
MAX_CAMPAIGN_PAGES = 15

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Links worth following on a campaign site, and links never worth following
ISSUE_WORDS = re.compile(r"issue|priorit|platform|polic|agenda|plan|vision|stand|position|blueprint|record", re.I)
# Matched against whole words of a link's address and text, so "events" is
# skipped but "preventing-gun-violence" is not
SKIP_EXACT = {"press", "news", "blog", "media", "shop", "store", "join", "terms", "jobs", "login",
              "event", "events", "contact", "privacy", "careers", "merch", "rsvp", "unsubscribe"}
SKIP_PREFIX = ("donat", "contribut", "volunteer", "endorse", "accessib", "cookie", "signup",
               "espanol", "español", "newsletter")


def is_skippable(url: str, text: str) -> bool:
    words = re.split(r"[^a-zñ0-9]+", (urlparse(url).path + " " + text).lower())
    return any(w in SKIP_EXACT or w.startswith(SKIP_PREFIX) for w in words if w)
SOCIAL = re.compile(r"(twitter|x|facebook|instagram|tiktok|youtube|linkedin|threads|bsky|actblue|winred)\.(com|app|social)", re.I)


# ── HTTP ─────────────────────────────────────────────────────────────────────

_last_call = {}


def http_get(url: str, params: dict = None, retries: int = 2):
    """GET with a polite per-site delay. Returns the response, or None on failure."""
    host = urlparse(url).netloc
    wait = DELAY.get(host, DEFAULT_DELAY) - (time.time() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    for attempt in range(retries + 1):
        _last_call[host] = time.time()
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            print(f"      (request failed: {e.__class__.__name__})")
            return None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code != 200:
            reason = {202: "bot check", 403: "blocked", 404: "no such page", 429: "rate limited"}.get(resp.status_code, "")
            print(f"      (HTTP {resp.status_code}{' ' + reason if reason else ''}: {url})")
            return None
        return resp
    return None


def http_json(url: str, params: dict = None):
    resp = http_get(url, params)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# ── Reading pages ────────────────────────────────────────────────────────────

def html_to_lines(html: str, whole_page: bool = False) -> list:
    """
    Clean HTML into lines of text: whole paragraphs, list items and headings,
    so sentences stay intact and quotes can be checked word for word.
    whole_page reads the full <body> instead of guessing the main content
    area (campaign sites use many different page builders).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "header", "noscript"]):
        tag.decompose()
    main = soup.body if whole_page else (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"content|main|body", re.I))
        or soup.body
    )
    root = main or soup
    lines, seen = [], set()
    for block in root.find_all(["h2", "h3", "h4", "p", "li", "dd", "blockquote"]):
        if block.name in ("p", "li") and block.find_parent(["li", "blockquote"]):
            continue  # nested inside a block already read
        text = re.sub(r"\[\s*(?:\d+|[a-z]|citation needed|note \d+)\s*\]", "", block.get_text(" ", strip=True))
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([.,;:])", r"\1", text)
        if block.name.startswith("h"):
            if text:
                lines.append(f"#### {text}")
        elif len(text) > 40 and text not in seen:  # skip stubs like nav links
            seen.add(text)
            lines.append(text)
    return lines


def ymd(as_of: str) -> str:
    return as_of.replace("-", "")


def fetch_page(url: str, as_of: str = None):
    """
    One web page, live or as of a date. Returns (html, cited_url, snapshot)
    or None. In as-of mode the page comes from the Internet Archive's last
    copy on or before the date; cited_url links to that exact copy.
    """
    if not as_of:
        resp = http_get(url)
        return (resp.text, url, None) if resp is not None else None
    rows = http_json(CDX_API, {
        "url": url, "to": ymd(as_of), "output": "json", "fl": "timestamp,original",
        "filter": "statuscode:200", "limit": "-1",
    })
    # The first row is the column names; the next is the last copy before the date
    if not rows or len(rows) < 2:
        print(f"      (no Internet Archive copy on or before {as_of}: {url})")
        return None
    ts, original = rows[-1][0], rows[-1][1]
    if ts[:8] > ymd(as_of):
        return None
    resp = http_get(f"https://web.archive.org/web/{ts}id_/{original}")
    if resp is None:
        return None
    return resp.text, f"https://web.archive.org/web/{ts}/{original}", ts


# ── Who is this? ─────────────────────────────────────────────────────────────

PARTY_WORDS = {
    "democrat": ["democrat"],          # matches Democrat and Democratic
    "republican": ["republican"],
    "libertarian": ["libertarian"],
    "green": ["green party"],
}
FEC_PARTY = {"democrat": "DEM", "republican": "REP", "libertarian": "LIB", "green": "GRE"}


def race_of(info: dict) -> dict:
    return next(r for r in RACES if r["id"] == info["race"])


def plain(text: str) -> str:
    """Lowercase, no accents or punctuation, for comparing names."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def name_parts(name: str) -> tuple:
    """(first, last) of a name, ignoring suffixes like Jr."""
    words = [w for w in plain(name).split() if w not in SUFFIXES]
    return words[0], words[-1]


def same_name(a: str, b: str) -> bool:
    """True when two names share a first and last name ("Tony Thurmond" = "Tony K. Thurmond")."""
    fa, la = name_parts(a)
    words = plain(b).split()
    return fa in words and la in words


def identity_terms(info: dict) -> list:
    """
    What a third-party page must mention to count as this candidate:
    (label, [any of these words]) for the state, the office, and the party.
    A race can override the office words with "identity_terms" in config.
    Candidates with no party (independent, no party preference) skip the
    party check, since pages describe them too many different ways.
    """
    race = race_of(info)
    state_full = STATE_NAMES.get(info["state"], info["state"]).lower()
    office = race["title"].split(" — ")[0].lower()
    if race.get("identity_terms"):
        office_words = [t.lower() for t in race["identity_terms"]]
    elif "governor" in office:
        office_words = ["governor", "gubernatorial"]
    elif "senate" in office:
        office_words = ["senate", "senator"]
    elif "house" in office:
        office_words = ["house of representatives", "congress", "congressional"]
    else:
        office_words = [office]
    terms = [("state", [state_full]), ("office", office_words)]
    party = PARTY_WORDS.get(info.get("party", "").lower())
    if party:
        terms.append(("party", party))
    return terms


def is_right_person(info: dict, text: str) -> bool:
    """
    Guard against a different person with the same name: a third-party page
    must mention the state, the office, and the candidate's party. A page
    that only mentions the state is not enough, since many people share a
    name and a state.
    """
    low = text.lower()
    missing = [label for label, options in identity_terms(info) if not any(o in low for o in options)]
    if missing:
        print(f"   ✗ Page never mentions the {', '.join(missing)}, likely a different person, skipped")
    return not missing


# ── 1. State voter guide ─────────────────────────────────────────────────────

DOMAIN_LINE = re.compile(r"^(https?://)?(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", re.I)


def parse_voter_guide(html: str, page_url: str) -> dict:
    """
    Split a voter guide page into one entry per candidate. Each entry starts
    at a heading like "Name | PARTY" and runs to the next one. Returns
    {heading name: {"party", "statement": [paragraphs], "websites": [urls]}}.
    Phone numbers, addresses and e-mail are never kept: only statement
    paragraphs and website links.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    entries, current = {}, None
    blocks = ("p", "li", "dd", "address", "div")
    for tag in soup.find_all(True):
        if tag.name in ("h2", "h3"):
            text = tag.get_text(" ", strip=True)
            if "|" in text:
                name, party = [t.strip() for t in text.split("|", 1)]
                current = entries.setdefault(name, {"party": party, "statement": [], "websites": []})
            else:
                current = None
            continue
        if current is None:
            continue
        if tag.name == "hr":
            current = None
            continue
        if tag.name == "a" and tag.get("href", "").startswith("http"):
            href = tag["href"]
            host = urlparse(href).netloc.lower()
            if host and host not in urlparse(page_url).netloc.lower() and not SOCIAL.search(host):
                current["websites"].append(href)
            continue
        if tag.name not in blocks or tag.find(list(blocks)):
            continue  # only innermost blocks, so nothing is read twice
        for line in tag.get_text("\n", strip=True).split("\n"):
            line = re.sub(r"\s+", " ", line).strip()
            if len(line.split()) >= 12:
                current["statement"].append(line)
            elif DOMAIN_LINE.match(line) and "@" not in line and not SOCIAL.search(line):
                current["websites"].append(line if line.startswith("http") else f"https://{line}")
    for e in entries.values():
        e["websites"] = list(dict.fromkeys(e["websites"]))
    return entries


_guide_cache = {}


def voter_guide_entry(info: dict, as_of: str = None):
    """
    The candidate's entry in their race's voter guide, or None. Config gives
    the race a "voter_guide" with a "live" page and an "archive" page; as-of
    runs read the state's archive, since the live guide moves on to the next
    election. Returns (entry, cited_url).
    """
    guide = race_of(info).get("voter_guide")
    if not guide:
        return None
    url = guide.get("archive") if as_of else guide.get("live")
    if not url:
        return None
    if url not in _guide_cache:
        resp = http_get(url)
        _guide_cache[url] = parse_voter_guide(resp.text, url) if resp is not None else {}
    for name, entry in _guide_cache[url].items():
        if same_name(info["name"], name):
            return entry, url
    return None


# ── 2. FEC ───────────────────────────────────────────────────────────────────

def fec_website(info: dict):
    """
    The campaign website on the candidate's FEC filing, or None. Federal
    races only. The match must agree on name, state, office, district, year
    and party, or a candidate id can be set in config as "fec_candidate_id".
    """
    race = race_of(info)
    office = race.get("fec_office")
    if not office:
        return None
    key = os.environ.get("FEC_API_KEY", "DEMO_KEY")
    if info.get("fec_candidate_id"):
        found = (http_json(f"{FEC_API}/candidate/{info['fec_candidate_id']}/", {"api_key": key}) or {}).get("results", [])
    else:
        params = {"api_key": key, "q": name_parts(info["name"])[1], "state": info["state"],
                  "office": office, "election_year": race.get("year", 2026), "per_page": 50}
        if office == "H" and race.get("district"):
            params["district"] = f"{int(race['district']):02d}"
        found = (http_json(f"{FEC_API}/candidates/", params) or {}).get("results", [])
        party = FEC_PARTY.get(info.get("party", "").lower())
        # FEC names read "MARKEY, EDWARD J": match the last name and first
        # initial, since campaigns often use a short first name (Ed, Edward)
        first, last = name_parts(info["name"])

        def matches(c):
            fec_last, _, fec_rest = c.get("name", "").partition(",")
            fec_first = plain(fec_rest).split()[:1]
            return (plain(fec_last).replace(" ", "") == last
                    and fec_first and fec_first[0][0] == first[0])
        found = [c for c in found if matches(c) and (not party or c.get("party") == party)]
    if len(found) != 1:
        if len(found) > 1:
            print(f"   ⚠ FEC: {len(found)} candidates match {info['name']}; set fec_candidate_id in config")
        return None
    cand_id = found[0]["candidate_id"]
    committees = (http_json(f"{FEC_API}/candidate/{cand_id}/committees/",
                            {"api_key": key, "designation": "P"}) or {}).get("results", [])
    for c in committees:
        site = (c.get("website") or "").strip()
        if not site:
            detail = (http_json(f"{FEC_API}/committee/{c['committee_id']}/", {"api_key": key}) or {}).get("results", [])
            site = ((detail[0].get("website") if detail else "") or "").strip()
        if site:
            return site if site.startswith("http") else f"https://{site}"
    return None


# ── 3. Wikipedia ─────────────────────────────────────────────────────────────

def wikipedia_titles(info: dict) -> list:
    if info.get("wikipedia_slug"):
        return [info["wikipedia_slug"].replace("_", " ")]
    name = info["name"]
    return [name, f"{name} (politician)", f"{name} (American politician)"]


def wikipedia_page(info: dict, as_of: str = None):
    """
    The candidate's Wikipedia article, live or as of a date. Returns
    (html, permanent link, revision id, timestamp) or None. Disambiguation
    pages and pages that fail the identity check are skipped.
    """
    if info.get("skip_wikipedia"):
        return None
    titles = wikipedia_titles(info)
    data = http_json(WIKI_API, {"action": "query", "titles": "|".join(titles), "redirects": 1,
                                "prop": "pageprops", "format": "json", "formatversion": 2})
    pages = {p["title"]: p for p in (data or {}).get("query", {}).get("pages", [])
             if not p.get("missing") and "disambiguation" not in p.get("pageprops", {})}
    redirects = {r["from"]: r["to"] for r in (data or {}).get("query", {}).get("redirects", [])}
    for t in titles:
        title = redirects.get(t, t)
        if title not in pages:
            continue
        params = {"action": "query", "titles": title, "prop": "revisions", "rvprop": "ids|timestamp",
                  "rvlimit": 1, "format": "json", "formatversion": 2}
        if as_of:
            params.update({"rvstart": f"{as_of}T23:59:59Z", "rvdir": "older"})
        revs = ((http_json(WIKI_API, params) or {}).get("query", {}).get("pages", [{}])[0]).get("revisions", [])
        if not revs:
            print(f"   – Wikipedia: no version of {title} on or before {as_of}")
            continue
        rev = revs[0]
        parsed = http_json(WIKI_API, {"action": "parse", "oldid": rev["revid"], "prop": "text",
                                      "format": "json", "formatversion": 2})
        html = (parsed or {}).get("parse", {}).get("text")
        if not html:
            continue
        if not is_right_person(info, BeautifulSoup(html, "html.parser").get_text(" ")):
            continue
        link = f"https://en.wikipedia.org/w/index.php?title={title.replace(' ', '_')}&oldid={rev['revid']}"
        return html, link, rev["revid"], rev["timestamp"]
    return None


def infobox_website(html: str):
    """The Website row of a Wikipedia infobox, if it has one."""
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.select("table.infobox tr"):
        th = row.find("th")
        if th and th.get_text(" ", strip=True).lower() == "website":
            a = row.find("a", href=re.compile(r"^https?://"))
            if a and not SOCIAL.search(a["href"]):
                return a["href"]
    return None


# ── 4. Campaign site ─────────────────────────────────────────────────────────

def same_site(a: str, b: str) -> bool:
    strip = lambda u: urlparse(u).netloc.lower().removeprefix("www.")
    return strip(a) == strip(b)


def page_links(html: str, base: str, main_only: bool = False) -> list:
    """(absolute url, link text) for every link on the page, on the same site."""
    soup = BeautifulSoup(html, "html.parser")
    if main_only:
        for tag in soup(["nav", "footer", "header", "aside"]):
            tag.decompose()
    out = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"]).split("#")[0].rstrip("/")
        if url.startswith("http") and same_site(url, base):
            out.append((url, a.get_text(" ", strip=True)))
    return list(dict.fromkeys(out))


def campaign_pages(info: dict, start_urls: list, as_of: str = None, crawl: bool = True) -> tuple:
    """
    Read a candidate's own site. From each starting page, follow links to
    issue pages (by link text or address), then links inside those pages'
    main content, up to MAX_CAMPAIGN_PAGES in all. With crawl=False only the
    given pages are read (pages listed by hand in config).
    The site counts only if at least one page names the candidate.
    Returns ([(cited_url, lines, snapshot)], [(url, reason)]).
    """
    got, skipped, seen, sites_read, site_text = [], [], set(), [], []
    queue = [(u.rstrip("/"), 0) for u in start_urls]
    while queue and len(got) < MAX_CAMPAIGN_PAGES:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if depth == 0 and crawl and any(same_site(url, s) for s in sites_read):
            continue   # another address for a site already read
        page = fetch_page(url, as_of)
        if page is None:
            skipped.append((url, "could not read" if not as_of else f"no copy on or before {as_of}"))
            continue
        html, cited, snap = page
        if depth == 0:
            sites_read.append(url)
        lines = html_to_lines(html, whole_page=True)
        site_text.append(BeautifulSoup(html, "html.parser").get_text(" "))
        # Almost no text usually means the page needs JavaScript to show anything
        if sum(len(l) for l in lines) >= 80:
            got.append((cited, lines, snap, html))
        elif depth == 0 and not crawl:
            skipped.append((url, "almost no text; may need JavaScript"))
        if not crawl or depth >= 2:
            continue
        for link, text in page_links(html, url, main_only=depth == 1):
            if link in seen or is_skippable(link, text):
                continue
            if depth == 0 and not (ISSUE_WORDS.search(urlparse(link).path) or ISSUE_WORDS.search(text)):
                continue
            queue.append((link, depth + 1))
    # Site check: some page on the site, the home page included, must name
    # the candidate in full. Issue pages often use only a first name.
    last = name_parts(info["name"])[1]
    if got and not any(last in plain(t).split() for t in site_text):
        skipped += [(c, f"site never names {info['name']}") for c, _, _, _ in got]
        got = []
    return [(c, l, s) for c, l, s, _ in got], skipped


# ── Main scout logic ─────────────────────────────────────────────────────────

def scout_candidate(candidate_id: str, as_of: str = None) -> dict:
    info = CANDIDATES[candidate_id]
    mode = f"as of {as_of}" if as_of else "live"
    print(f"\n🔍 Scouting {info['name']} ({info['state']}, {mode})...")
    if info.get("confirm_on_ballot"):
        print("   ⚠ Ballot status not yet confirmed against the official state list")
    text = [f"# {info['name']}: public sources ({mode})\n"]
    record = {"candidate": candidate_id, "name": info["name"], "mode": "as-of" if as_of else "live",
              "as_of": as_of, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "sources": [], "skipped": []}
    websites = []

    # 1. Voter guide: the candidate's own statement, in the state's guide
    found = voter_guide_entry(info, as_of)
    if found:
        entry, url = found
        if entry["statement"]:
            text.append(f"\n## Voter guide: {url}\n\n" + "\n".join(entry["statement"]))
            record["sources"].append({"kind": "voter guide", "url": url, "words": len(" ".join(entry["statement"]).split())})
            print(f"   ✓ Voter guide statement ({record['sources'][-1]['words']} words)")
        websites += entry["websites"]

    # 2. FEC: the committee's official website
    site = fec_website(info)
    if site:
        print(f"   ✓ FEC lists website: {site}")
        websites.append(site)

    # 3. Wikipedia
    wiki = wikipedia_page(info, as_of)
    if wiki:
        html, link, revid, ts = wiki
        lines = html_to_lines(html)
        text.append(f"\n## Wikipedia: {link}\n\n" + "\n".join(lines))
        record["sources"].append({"kind": "wikipedia", "url": link, "revision": revid, "revision_time": ts,
                                  "words": len(" ".join(lines).split())})
        print(f"   ✓ Wikipedia, version of {ts[:10]}: {link}")
        site = infobox_website(html)
        if site:
            websites.append(site)

    # 4. Campaign site: pages listed in config, or found from the sites above
    hand_listed = info.get("campaign_urls", [])
    # Up to 2 sites; every address found for a site is kept, so if one form
    # fails (portertest.com) another can work (www.portertest.com)
    sites = []
    for w in websites:
        if not any(same_site(w, v) for v in sites):
            sites.append(w)
    starts = hand_listed or list(dict.fromkeys(w for w in websites if any(same_site(w, v) for v in sites[:2])))
    if starts:
        pages, skipped = campaign_pages(info, starts, as_of, crawl=not hand_listed)
        for cited, lines, snap in pages:
            text.append(f"\n## Campaign site: {cited}\n\n" + "\n".join(lines))
            record["sources"].append({"kind": "campaign site", "url": cited, "snapshot": snap,
                                      "words": len(" ".join(lines).split())})
        record["skipped"] += [{"url": u, "reason": r} for u, r in skipped]
        print(f"   {'✓' if pages else '✗'} Campaign site: {len(pages)} page(s) read, {len(skipped)} skipped")
    else:
        print("   – No campaign website found in the voter guide, FEC or Wikipedia")

    # 5. About page, for the bio when there's no Wikipedia page
    for url in info.get("bio_urls", []):
        page = fetch_page(url, as_of)
        lines = html_to_lines(page[0], whole_page=True) if page else []
        if sum(len(l) for l in lines) >= 200:
            text.append(f"\n## Bio page: {page[1]}\n\n" + "\n".join(lines))
            record["sources"].append({"kind": "bio page", "url": page[1], "snapshot": page[2]})
        else:
            record["skipped"].append({"url": url, "reason": "could not read"})

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"{candidate_id}.txt"
    side = RAW_DIR / f"{candidate_id}.sources.json"
    if not record["sources"]:
        print(f"   ⚠ No sources found for {info['name']}")
        out.unlink(missing_ok=True)   # never leave an older run's file behind
    else:
        out.write_text("\n".join(text), encoding="utf-8")
        print(f"   ✓ Saved {len(' '.join(text).split()):,} words → data/raw/{out.name}")
    side.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description="ZipVote Scout Agent")
    parser.add_argument("--candidate", help="Scout one candidate id")
    parser.add_argument("--race", help="Scout every candidate in one race id")
    parser.add_argument("--as-of", dest="as_of", help="Read sources as they stood on YYYY-MM-DD")
    args = parser.parse_args()
    if args.as_of:
        datetime.strptime(args.as_of, "%Y-%m-%d")   # fail early on a bad date

    ids = list(CANDIDATES)
    if args.candidate:
        ids = [args.candidate]
    elif args.race:
        ids = [c for c in ids if CANDIDATES[c]["race"] == args.race]

    records = [scout_candidate(cid, args.as_of) for cid in ids]

    have = [r for r in records if r["sources"]]
    print(f"\n✅ {len(have)} of {len(records)} candidates have at least one source.")
    for kind in ("voter guide", "wikipedia", "campaign site"):
        n = sum(any(s["kind"] == kind for s in r["sources"]) for r in records)
        print(f"   {kind}: {n}")
    missing = [r["name"] for r in records if not r["sources"]]
    if missing:
        print(f"   No sources: {', '.join(missing)}")


if __name__ == "__main__":
    main()
