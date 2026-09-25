"""
Scout Agent — auto-discovers and scrapes candidate issue positions from
Ballotpedia and Wikipedia, saving cleaned plain-text summaries to data/raw/.

URL construction is fully automatic from candidate names, so no URLs need
to be maintained in config.py as new races and states are added.

Usage:
    python -m agents.scout
    python -m agents.scout --candidate markey  # single candidate
"""

import argparse
import re
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import quote

from agents.config import CANDIDATES

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ZipVote-Scout/1.0; "
        "+https://github.com/zipvote research bot)"
    )
}
RATE_LIMIT_SECONDS = 1.5  # Polite rate limit between requests


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New_Hampshire", "NJ": "New_Jersey", "NM": "New_Mexico", "NY": "New_York",
    "NC": "North_Carolina", "ND": "North_Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode_Island", "SC": "South_Carolina",
    "SD": "South_Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West_Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


# ── URL construction ────────────────────────────────────────────────────────

def _name_to_slug(name: str) -> str:
    """Convert 'Ed Markey' → 'Ed_Markey' for use in Ballotpedia/Wikipedia URLs."""
    return "_".join(name.strip().split())


def ballotpedia_candidates(name: str, state: str, custom_slug: str | None = None) -> list[str]:
    """
    Return a prioritised list of Ballotpedia URLs to try for this candidate.
    Ballotpedia disambiguates non-incumbents with a state suffix, e.g.:
      /John_Deaton_(Massachusetts)  — for state-level challengers
      /Ed_Markey                    — for national incumbents (no suffix)
    We try the plain slug first, then the state-disambiguated slug.
    If the caller provides a custom_slug (from config), that is tried first.
    """
    slug = _name_to_slug(name)
    state_full = STATE_NAMES.get(state, state)
    candidates = []
    if custom_slug:
        # Custom slug takes priority; still add state variant as fallback
        candidates.append(f"https://ballotpedia.org/{custom_slug}")
        state_slug = f"{slug}_({state_full})"
        if custom_slug != state_slug:
            candidates.append(f"https://ballotpedia.org/{state_slug}")
    else:
        candidates.append(f"https://ballotpedia.org/{slug}")
        candidates.append(f"https://ballotpedia.org/{slug}_({state_full})")
    return candidates



def wikipedia_candidates(name: str) -> list[str]:
    """
    Return a prioritised list of Wikipedia URLs to try.
    Wikipedia uses the same underscore slug convention, with common
    disambiguation suffixes for politicians.
    """
    slug = _name_to_slug(name)
    return [
        f"https://en.wikipedia.org/wiki/{slug}",
        f"https://en.wikipedia.org/wiki/{slug}_(politician)",
        f"https://en.wikipedia.org/wiki/{slug}_(American_politician)",
    ]


# ── Scraping ─────────────────────────────────────────────────────────────────

def url_exists(url: str) -> bool:
    """HEAD request to check if the URL returns a 200 without downloading content."""
    try:
        resp = requests.head(url, headers=HEADERS, timeout=8, allow_redirects=True)
    except requests.RequestException as e:
        print(f"      (request failed: {e.__class__.__name__})")
        return False
    if resp.status_code != 200:
        # 404 = page doesn't exist; 403/429 = the site is blocking the scraper
        reason = {403: "blocked", 404: "no such page", 429: "rate limited"}.get(resp.status_code, "")
        print(f"      (HTTP {resp.status_code}{' ' + reason if reason else ''})")
    return resp.status_code == 200


def scrape_url(url: str) -> str:
    """Fetch a URL and return cleaned plain text."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    ⚠ Failed to fetch {url}: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove chrome: nav, footer, scripts, styles, sidebars
    for tag in soup(["script", "style", "nav", "footer", "aside", "header"]):
        tag.decompose()

    # Prefer <main> / <article> / role="main" / .content class / full body
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"content|main|body", re.I))
        or soup.body
    )

    root = main or soup

    # Read whole blocks (paragraphs, list items, headings) so sentences stay
    # intact. Splitting on every tag breaks sentences at each link, which
    # makes quotes unreadable and impossible to verify.
    lines, seen = [], set()
    for block in root.find_all(["h2", "h3", "h4", "p", "li", "dd", "blockquote"]):
        if block.name in ("p", "li") and block.find_parent(["li", "blockquote"]):
            continue  # nested inside a block we already read
        # drop citation markers like [12], [ 1 ], [ a ], [ citation needed ]
        text = re.sub(r"\[\s*(?:\d+|[a-z]|citation needed|note \d+)\s*\]", "", block.get_text(" ", strip=True))
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([.,;:])", r"\1", text)
        if block.name.startswith("h"):
            if text:
                lines.append(f"#### {text}")
        elif len(text) > 40 and text not in seen:  # skip stubs like nav links
            seen.add(text)
            lines.append(text)
    return "\n".join(lines)


def resolve_url(candidates: list[str], label: str) -> str | None:
    """
    Try each URL in order; return the first that resolves (HTTP 200).
    Returns None if none resolve.
    """
    for url in candidates:
        if url_exists(url):
            print(f"   ✓ {label}: {url}")
            return url
        else:
            print(f"   ✗ {label} — not found: {url}")
        time.sleep(0.3)  # light delay between HEAD requests
    return None


# ── Main scout logic ──────────────────────────────────────────────────────────

def scout_candidate(candidate_id: str) -> None:
    info = CANDIDATES[candidate_id]
    print(f"\n🔍 Scouting {info['name']} ({info['state']})...")

    all_text = [f"# {info['name']} — Issue Positions & Biography\n"]
    found_any = False
    state_full = STATE_NAMES.get(info["state"], info["state"]).replace("_", " ")

    if info.get("confirm_on_ballot"):
        print("   ⚠ Ballot status not yet confirmed against the official state list")

    def is_right_person(text: str) -> bool:
        """Guard against scraping a different person with the same name."""
        if state_full.lower() in text.lower():
            return True
        print(f"   ✗ Page never mentions {state_full} — likely a different person, skipped")
        return False

    # ── 1. Ballotpedia (positions / campaign themes / key votes) ──────────────
    bp_urls = ballotpedia_candidates(
        name=info["name"],
        state=info["state"],
        custom_slug=info.get("ballotpedia_slug"),
    )
    bp_url = resolve_url(bp_urls, "Ballotpedia")
    if bp_url:
        text = scrape_url(bp_url)
        if text and is_right_person(text):
            all_text.append(f"\n## Ballotpedia: {bp_url}\n\n{text}")
            found_any = True
        time.sleep(RATE_LIMIT_SECONDS)

    # ── 2. Wikipedia (biography / legislative history) ────────────────────────
    wp_url = None
    if info.get("skip_wikipedia"):
        print("   – Wikipedia skipped (skip_wikipedia in config)")
    else:
        wp_url = resolve_url(wikipedia_candidates(info["name"]), "Wikipedia")
    if wp_url:
        text = scrape_url(wp_url)
        if text and is_right_person(text):
            all_text.append(f"\n## Wikipedia: {wp_url}\n\n{text}")
            found_any = True
        time.sleep(RATE_LIMIT_SECONDS)

    # ── 3. Manual fallback ────────────────────────────────────────────────────
    manual_path = RAW_DIR / f"{candidate_id}_manual.txt"
    if manual_path.exists():
        manual_text = manual_path.read_text(encoding="utf-8").strip()
        if manual_text:
            print(f"   ✓ Manual fallback: {manual_path.name}")
            all_text.append(f"\n## Manual notes\n\n{manual_text}")
            found_any = True

    if not found_any:
        print(f"   ⚠ No sources found for {info['name']} — skipping.")
        return

    combined = "\n".join(all_text)
    out_path = RAW_DIR / f"{candidate_id}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(combined, encoding="utf-8")

    word_count = len(combined.split())
    print(f"   ✓ Saved {word_count:,} words → {out_path.relative_to(Path.cwd())}")


def main():
    parser = argparse.ArgumentParser(description="ZipVote Scout Agent")
    parser.add_argument(
        "--candidate",
        help="Scrape a single candidate by ID (e.g. markey)",
        default=None,
    )
    args = parser.parse_args()

    targets = [args.candidate] if args.candidate else list(CANDIDATES.keys())

    print(f"🗳️  ZipVote Scout Agent — scouting {len(targets)} candidate(s)")
    for cid in targets:
        if cid not in CANDIDATES:
            print(f"❌ Unknown candidate: {cid}")
            continue
        scout_candidate(cid)

    print("\n✅ Scout complete. Check data/raw/ for output.")


if __name__ == "__main__":
    main()
