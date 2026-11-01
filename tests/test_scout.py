"""
Offline tests for the Scout agent: every web request is answered by a fake,
so these run with no network and no API keys.

    python -m tests.test_scout
"""

import json
import tempfile
from pathlib import Path

from agents import scout, wedge
from agents.config import CANDIDATES, RACES

GUIDE_LIVE = "https://voterguide.sos.ca.gov/candidates/test-governor.htm"
GUIDE_ARCHIVE = "https://vigarchive.sos.ca.gov/2026/primary/candidates/test-governor.htm"
SITE = "https://www.portertest.com"

GUIDE_HTML = """<html><body><nav>Home | Candidates</nav>
<h1>Governor</h1><p>The Governor is the chief executive of the state and signs or vetoes every bill.</p>
<hr>
<h2>Katie Porter | DEMOCRATIC</h2>
<p>I will lower housing costs by building more homes near transit and cutting permit delays for every city in the state.</p>
<p>I support a state public option for health insurance so every family can see a doctor without fear of the bill.</p>
<p>123 Main St.<br>Irvine, CA 92618<br>Tel: (555) 555-0100<br>E-mail: &lt;katie@example.com&gt;<br>@katieporter<br>portertest.com</p>
<hr>
<h2>Tony K. Thurmond | DEMOCRATIC</h2>
<p>As your Governor I will raise teacher pay and fund universal preschool for every four year old in California.</p>
<p><a href="https://www.facebook.com/thurmond">Facebook</a> <a href="https://thurmondtest.com">thurmondtest.com</a></p>
<h2>Scott P Shields | DEMOCRATIC</h2>
<p>Visit ShieldsForGovernor.com</p>
<p>Los Angeles, CA 90012<br>(213) 555-0199<br>E-mail: &lt;scott@shieldsforgovernor.com&gt;</p>
<hr>
<h2>Rafael M. Hernandez | REPUBLICAN</h2>
<p>To Me: The people of California Come First.</p>
<ol><li>Lower the high cost of living.</li><li>Create more good pay jobs.</li></ol>
<p>P.O. Box 1234<br>Fresno, CA</p>
<hr>
</body></html>"""

WIKI_HTML = """<div><table class="infobox"><tr><th>Political party</th><td>Democratic</td></tr>
<tr><th>Website</th><td><a href="https://www.portertest.com">Campaign website</a></td></tr></table>
<p>Katherine Moore Porter is an American politician and member of the Democratic Party who ran for Governor of California in 2026.</p>
<p>Porter supports banning congressional stock trading and has introduced legislation to that effect in the House.</p>
<div class="mw-heading mw-heading2"><h2 id="External_links">External links</h2></div>
<ul><li><a class="external text" href="https://www.portertest.com">Campaign website</a></li></ul></div>"""

WIKI_WRONG_PERSON = """<div><p>Katie Porter is an American swimmer from California who competed at the 2012 Olympic Games in London.</p></div>"""

HOME = f"""<html><body><nav><a href="/donate">Donate</a><a href="/events">Events</a></nav>
<main><h1>Katie Porter for Governor</h1><p>Katie Porter is running for governor to take on corporate special interests in Sacramento.</p>
<a href="/issues">Issues</a> <a href="/volunteer">Volunteer</a> <a href="https://othersite.com/issues">Elsewhere</a></main></body></html>"""

ISSUES = """<html><body><nav><a href="/news">News</a></nav><main><h1>Issues</h1>
<p>Here is where Katie stands on the issues that matter most to California families this year, from the cost of housing to the safety of our schools and streets.</p>
<a href="/preventing-gun-violence">Preventing gun violence</a> <a href="/housing">Housing</a> <a href="/press">Press</a></main></body></html>"""

GUNS = """<html><body><main><p>Katie supports universal background checks for every gun sale in California, including private sales between strangers, and a waiting period for every new purchase.</p></main></body></html>"""
HOUSING = """<html><body><main><p>Katie will end exclusionary zoning near transit stations and speed up permits for new apartment buildings across the state.</p></main></body></html>"""


class Resp:
    def __init__(self, body, status=200, content=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self._body = body
        self.content = content if content is not None else self.text.encode("utf-8")
        self.encoding = "utf-8"

    def json(self):
        return self._body if not isinstance(self._body, str) else json.loads(self._body)


def fake_web(as_of_copies=None, wiki_html=WIKI_HTML):
    """A fake internet. Returns (http_get, list of requests made)."""
    calls = []
    pages = {GUIDE_LIVE: GUIDE_HTML, GUIDE_ARCHIVE: GUIDE_HTML, SITE: HOME,
             f"{SITE}/issues": ISSUES, f"{SITE}/preventing-gun-violence": GUNS, f"{SITE}/housing": HOUSING}
    copies = as_of_copies or {}

    def get(url, params=None, retries=2):
        params = params or {}
        calls.append((url, dict(params)))
        if url == scout.WIKI_API:
            if params.get("action") == "parse":
                return Resp({"parse": {"text": wiki_html}})
            if params.get("prop") == "pageprops":
                return Resp({"query": {"pages": [{"title": "Katie Porter", "pageprops": {}},
                                                  {"title": "Katie Porter (politician)", "missing": True}]}})
            if params.get("prop") == "revisions":
                rev = {"revid": 111, "timestamp": "2026-05-01T10:00:00Z"} if params.get("rvstart") \
                    else {"revid": 999, "timestamp": "2026-10-30T10:00:00Z"}
                return Resp({"query": {"pages": [{"title": "Katie Porter", "revisions": [rev]}]}})
        if url == scout.CDX_API:
            original = params["url"].rstrip("/")
            if original in copies:
                return Resp([["timestamp", "original"], [copies[original], original]])
            return Resp([["timestamp", "original"]])
        if url.startswith("https://web.archive.org/web/"):
            original = url.split("id_/", 1)[1]
            return Resp(pages[original]) if original in pages else None
        if url in pages:
            return Resp(pages[url])
        return None   # like the real http_get on a failed request
    return get, calls


def setup(tmp):
    scout.RAW_DIR = wedge.RAW_DIR = Path(tmp)
    RACES.append({"id": "test_gov", "state": "CA", "title": "Governor — California, test",
                  "candidates": ["porter_t", "thurmond_t"],
                  "voter_guide": {"format": "ca_sos", "live": GUIDE_LIVE, "archive": GUIDE_ARCHIVE}})
    CANDIDATES["porter_t"] = {"id": "porter_t", "name": "Katie Porter", "party": "Democrat",
                              "race": "test_gov", "state": "CA"}
    CANDIDATES["thurmond_t"] = {"id": "thurmond_t", "name": "Tony Thurmond", "party": "Democrat",
                                "race": "test_gov", "state": "CA", "skip_wikipedia": True}
    scout._guide_cache.clear()


def kinds(rec):
    return [s["kind"] for s in rec["sources"]]


def test_voter_guide_parsing():
    entries = scout.parse_voter_guide(GUIDE_HTML, GUIDE_ARCHIVE)
    assert set(entries) == {"Katie Porter", "Tony K. Thurmond", "Scott P Shields", "Rafael M. Hernandez"}, entries.keys()
    porter = entries["Katie Porter"]
    assert len(porter["statement"]) == 2, porter["statement"]
    text = " ".join(porter["statement"])
    assert "555" not in text and "@" not in text and "Main St" not in text, "contact info must never be kept"
    assert porter["websites"] == ["https://portertest.com"], porter["websites"]
    assert entries["Tony K. Thurmond"]["websites"] == ["https://thurmondtest.com"], "social links are skipped"
    shields = entries["Scott P Shields"]
    assert shields["statement"] == ["Visit ShieldsForGovernor.com"], shields["statement"]
    assert shields["websites"] == ["https://ShieldsForGovernor.com"], "an address inside a sentence is found, an e-mail is not"
    hern = entries["Rafael M. Hernandez"]["statement"]
    assert "Lower the high cost of living." in hern and "Create more good pay jobs." in hern, "short lines and lists are kept"
    assert not any("Box" in l or "Fresno" in l for l in hern), hern
    assert "chief executive" not in text, "text before the first candidate isn't anyone's"


def test_live_run():
    scout.http_get, calls = fake_web()
    rec = scout.scout_candidate("porter_t")
    assert kinds(rec) == ["voter guide", "wikipedia", "campaign site", "campaign site", "campaign site", "campaign site"], kinds(rec)
    wiki = next(s for s in rec["sources"] if s["kind"] == "wikipedia")
    assert wiki["url"].endswith("oldid=999"), "live runs still cite the exact Wikipedia version"
    read = [s["url"] for s in rec["sources"] if s["kind"] == "campaign site"]
    assert f"{SITE}/preventing-gun-violence" in read, "a link containing 'event' inside a word is still followed"
    assert not any(w in u for u in read for w in ("donate", "events", "volunteer", "press", "news", "othersite"))
    raw = (scout.RAW_DIR / "porter_t.txt").read_text()
    segs = wedge.parse_sources(raw)
    assert [s.kind for s in segs][:2] == ["voter guide", "wikipedia"], [s.kind for s in segs]
    assert "universal background checks" in raw


def test_as_of_run():
    copies = {SITE: "20260420120000", f"{SITE}/issues": "20260421120000",
              f"{SITE}/housing": "20260422120000"}          # no copy of the gun page
    scout.http_get, calls = fake_web(as_of_copies=copies)
    rec = scout.scout_candidate("porter_t", as_of="2026-05-03")
    assert rec["mode"] == "as-of" and rec["as_of"] == "2026-05-03"
    assert any(u == GUIDE_ARCHIVE for u, _ in calls) and not any(u == GUIDE_LIVE for u, _ in calls), \
        "as-of runs read the state's archive, not the live guide"
    revs = [p for u, p in calls if p.get("prop") == "revisions"]
    assert revs and revs[0]["rvstart"] == "2026-05-03T23:59:59Z" and revs[0]["rvdir"] == "older"
    wiki = next(s for s in rec["sources"] if s["kind"] == "wikipedia")
    assert wiki["url"].endswith("oldid=111")
    cites = [s["url"] for s in rec["sources"] if s["kind"] == "campaign site"]
    assert cites and all(c.startswith("https://web.archive.org/web/2026042") for c in cites), cites
    cdx = [p for u, p in calls if u == scout.CDX_API]
    assert all(p["to"] == "20260503" for p in cdx), "never a copy from after the date"
    assert any("preventing-gun-violence" in s["url"] for s in rec["skipped"]), "a page with no copy is left out, and recorded"


def test_redirect_to_election_article_skipped():
    get, calls = fake_web()

    def redirecting(url, params=None, retries=2):
        if url == scout.WIKI_API and params.get("prop") == "pageprops":
            return Resp({"query": {"redirects": [{"from": "Katie Porter", "to": "2026 California gubernatorial election"}],
                                   "pages": [{"title": "2026 California gubernatorial election", "pageprops": {}}]}})
        return get(url, params)
    scout.http_get = redirecting
    assert scout.wikipedia_page(CANDIDATES["porter_t"]) is None, "an election article is never a candidate's page"


def test_office_site_only_as_fallback():
    # No voter guide, and Wikipedia gives only an infobox Website: it's used
    office = WIKI_HTML.replace(">Campaign website<", ">Biography<")
    RACES.append({"id": "test_noguide", "state": "CA", "title": "Governor — California, test", "candidates": []})
    CANDIDATES["porter_t"]["race"] = "test_noguide"
    try:
        scout.http_get, _ = fake_web(wiki_html=office)
        rec = scout.scout_candidate("porter_t")
        assert "office site" in kinds(rec) and "campaign site" not in kinds(rec), \
            "with no campaign link, the infobox site is read, labeled as an office site"
        # With a campaign link, the infobox site is never read
        scout.http_get, calls = fake_web()
        rec = scout.scout_candidate("porter_t")
        assert "office site" not in kinds(rec)
    finally:
        CANDIDATES["porter_t"]["race"] = "test_gov"


def test_wrong_person_skipped():
    scout.http_get, _ = fake_web(wiki_html=WIKI_WRONG_PERSON)
    rec = scout.scout_candidate("porter_t")
    assert "wikipedia" not in kinds(rec), "a page about someone else with the same name is skipped"


def test_name_matching():
    assert scout.same_name("Tony Thurmond", "Tony K. Thurmond")
    assert scout.same_name("Erin Zezulak", 'Erin "Zez" Zezulak')
    assert scout.same_name("Gretha Solorzano", "Gretha Solórzano")
    assert scout.same_name("Duane Loynes", "Duane Terrence Loynes Jr.")
    assert not scout.same_name("Katie Porter", "Katie Hill")


def test_wikipedia_title_variants():
    t = lambda name: scout.wikipedia_titles({"name": name})
    assert "Tony Thurmond" in t("Tony K. Thurmond")
    assert "Betty Yee" in t("Betty T. Yee")
    assert "Erin Zezulak" in t('Erin "Zez" Zezulak')
    assert "James Athans Jr." in t("James Athans Jr."), "suffixes are kept"
    assert "Duane Loynes Jr." in t("Duane Terrence Loynes Jr.")
    assert t("Katie Porter")[0] == "Katie Porter"


def test_campaign_link_in_external_links():
    html = """<div><p>Xavier Becerra is a Democratic politician from California.</p>
    <div class="mw-heading mw-heading2"><h2 id="External_links">External links</h2></div>
    <ul><li><a class="external text" href="https://www.xavierbecerra2026.com/">Campaign website</a></li>
    <li><a class="external text" href="https://bioguide.congress.gov/x">Biography</a> at the Biographical Directory</li>
    <li><a class="external text" href="https://www.joincalifornia.com/candidate/123">Join California Xavier Becerra</a></li></ul></div>"""
    assert scout.campaign_link(html) == "https://www.xavierbecerra2026.com/"
    assert scout.campaign_link("<div><p>No links section here.</p></div>") is None


def test_fec_match():
    def get(url, params=None, retries=2):
        if url.endswith("/candidates/"):
            return Resp({"results": [
                {"candidate_id": "S4MA00999", "name": "MARKEY, EDWARD J", "party": "DEM"},
                {"candidate_id": "S6MA00111", "name": "MARKEY, ANNE", "party": "REP"},
            ]})
        if url.endswith("/committees/"):
            return Resp({"results": [{"committee_id": "C001", "website": "www.edmarkey.com"}]})
        return Resp("", 404)
    scout.http_get = get
    assert scout.fec_website(CANDIDATES["markey"]) == "https://www.edmarkey.com", "Ed matches EDWARD; wrong party doesn't"


def test_text_in_plain_divs():
    html = """<html><body><nav><div>Home About Issues Donate Volunteer and other links</div></nav>
    <div class="elementor"><div class="widget"><div class="text">Xavier will expand Medi-Cal to cover every
    Californian who cannot afford private insurance, starting with children.</div></div>
    <div class="widget"><p>He supports a public option for health insurance sold on Covered California.</p></div></div>
    </body></html>"""
    lines = scout.html_to_lines(html, whole_page=True)
    assert any("Medi-Cal" in l for l in lines), lines
    assert any("public option" in l for l in lines), lines
    assert not any("Donate" in l for l in lines), "navigation is never read"
    assert len(lines) == 2, f"nothing read twice: {lines}"


def test_archive_failure_is_not_absence():
    get, _ = fake_web(as_of_copies={SITE: "20260420120000"})

    def flaky(url, params=None, retries=2):
        return None if url == scout.CDX_API else get(url, params)
    scout.http_get = flaky
    pages, skipped = scout.campaign_pages(CANDIDATES["porter_t"], [SITE], as_of="2026-05-03")
    assert not pages and skipped and "lookup failed" in skipped[0][1], skipped


def test_compressed_archive_copies():
    import brotli
    import zstandard
    html = "<html><body><main><p>Xavier will lower prescription drug prices for every family in California.</p></main></body></html>"
    for packed in (zstandard.ZstdCompressor().compress(html.encode()), brotli.compress(html.encode())):
        text = scout.page_text(Resp("noise", content=packed))
        assert "prescription drug prices" in text, text[:80]
    assert "drug" in scout.page_text(Resp(html)), "plain pages pass through unchanged"


def test_no_sources_removes_old_file():
    scout.http_get, _ = fake_web()
    (scout.RAW_DIR / "nobody_t.txt").write_text("old run")
    CANDIDATES["nobody_t"] = {"id": "nobody_t", "name": "Nobody Atall", "party": "Democrat",
                              "race": "test_gov", "state": "CA", "skip_wikipedia": True}
    rec = scout.scout_candidate("nobody_t")
    assert not rec["sources"] and not (scout.RAW_DIR / "nobody_t.txt").exists()


def test_contact_lines_any_state():
    for line in ("Boston, MA 02108", "Portland, Oregon", "Austin, TX 78701-1234", "Fresno, CA 93721"):
        assert scout.CONTACT_LINE.search(line), line
    assert not scout.CONTACT_LINE.search("I will fight for working families in Ohio, Texas and beyond."), \
        "a sentence that names a state is kept"


def test_guide_problems_are_reported():
    scout.http_get, _ = fake_web()
    race = next(r for r in RACES if r["id"] == "test_gov")
    saved = race["voter_guide"]
    try:
        # A guide with no parser for its format: reported, run carries on
        race["voter_guide"] = {"format": "or_sos", "live": GUIDE_LIVE}
        scout._guide_cache.clear()
        rec = scout.scout_candidate("thurmond_t")
        assert any("no parser" in s["reason"] for s in rec["skipped"]), rec["skipped"]
        # A guide whose page doesn't match its format: 0 candidates is a problem, not "no statement"
        race["voter_guide"] = {"format": "ca_sos", "live": SITE}
        scout._guide_cache.clear()
        rec = scout.scout_candidate("thurmond_t")
        assert any("0 candidates" in s["reason"] for s in rec["skipped"]), rec["skipped"]
    finally:
        race["voter_guide"] = saved
        scout._guide_cache.clear()


def test_guide_without_state_archive_uses_internet_archive():
    scout.http_get, calls = fake_web(as_of_copies={GUIDE_LIVE: "20260501120000"})
    race = next(r for r in RACES if r["id"] == "test_gov")
    saved = race["voter_guide"]
    try:
        race["voter_guide"] = {"format": "ca_sos", "live": GUIDE_LIVE}
        scout._guide_cache.clear()
        entry, cited, problem = scout.voter_guide_entry(CANDIDATES["porter_t"], as_of="2026-05-03")
        assert entry and not problem
        assert cited == f"https://web.archive.org/web/20260501120000/{GUIDE_LIVE}", cited
        assert not any(u == GUIDE_LIVE for u, _ in calls), "never the live guide in an as-of run"
    finally:
        race["voter_guide"] = saved
        scout._guide_cache.clear()


def test_office_words():
    w = lambda office, title="X — Y": scout.office_words({"office": office, "title": title})
    assert w("Governor") == ["governor", "gubernatorial"]
    assert w("Lieutenant Governor") == ["lieutenant governor"], "not matched as plain governor"
    assert w("U.S. Senate") == ["senate", "senator"]
    assert w("State Assembly District 12") == ["assembly"]
    assert "congress" in w("U.S. House of Representatives, District 6")
    assert w("Attorney General") == ["attorney general"]
    assert w(None, "Governor — California, Nov 3 2026") == ["governor", "gubernatorial"], "falls back to the title"


def test_election_year():
    assert scout.election_year({"id": "a", "election": "2028-03-03 primary"}) == 2028
    assert scout.election_year({"id": "a", "year": 2030, "election": "2028-03-03 primary"}) == 2030
    try:
        scout.election_year({"id": "a"})
        assert False, "a race with no date must fail loudly, not guess a year"
    except ValueError:
        pass


if __name__ == "__main__":
    real_get = scout.http_get
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        failed = 0
        for t in tests:
            scout._guide_cache.clear()
            try:
                t()
                print(f"PASS {t.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {t.__name__}: {e}")
        scout.http_get = real_get
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    raise SystemExit(1 if failed else 0)
