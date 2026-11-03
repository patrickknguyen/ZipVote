"""
Offline tests for Wedge's positions mode: every model call is answered by a
fake, so these run with no network and no API keys.

    python -m tests.test_wedge
"""

import json
import re
import tempfile
from pathlib import Path

from agents import wedge
from agents.config import CANDIDATES, RACES

RACE = next(r for r in RACES if r["id"] == "ca_gov_primary")
A, B, C = RACE["candidates"][:3]   # three real candidate ids from the manifest

SOURCE = {
    A: "## Campaign site: https://a.example.com\nI will repeal the gas tax on day one for every driver in the state.\n"
       "I support building high-speed rail from Los Angeles to San Francisco.",
    B: "## Voter guide: https://guide.example.com\nWe must keep the gas tax and spend it on fixing roads and bridges.",
}
POSITIONS = {
    A: [{"issue": "Gas Tax", "stance": "Repeal the gas tax.",
         "quote": "I will repeal the gas tax on day one for every driver in the state."},
        {"issue": "Rail", "stance": "Build high-speed rail.",
         "quote": "I support building high-speed rail from Los Angeles to San Francisco."},
        {"issue": "Water", "stance": "Made up.", "quote": "This sentence appears in no source at all, anywhere."}],
    B: [{"issue": "Gas Tax", "stance": "Keep the gas tax.",
         "quote": "We must keep the gas tax and spend it on fixing roads and bridges."}],
}
prompts = []


def fake_call_json(prompt, retries=2, fast=False, step=None):
    prompts.append(prompt)
    if "List up to" in prompt:
        for cid in (A, B):
            if CANDIDATES[cid]["name"] in prompt:
                return {"positions": POSITIONS[cid]}
        return {"positions": []}
    if "covers one issue area" in prompt:
        # One statement per question block: the first candidate agrees, the rest disagree
        out = []
        for block in prompt.split("### Question: ")[1:]:
            rows = re.findall(r"^- (C\d+) \[(P\d+)\]", block, re.M)
            if not rows:
                continue
            out.append({"topic": block.split("\n")[0], "text": f"Policy on {block.split(chr(10))[0]}.",
                        "basis": {c: p for c, p in rows},
                        "candidateAgreement": {c: (5 if i == 0 else 1) for i, (c, _) in enumerate(rows)},
                        "candidatePositions": {c: "Stance." for c, _ in rows},
                        "raceInsight": "x", "policyBackground": "y"})
        return {"statements": out, "skipped": []}
    if "Group the positions" not in prompt:
        return {}   # bios, gap filling, checks: nothing to add
    # Grouping: gas tax lines together, everything else alone
    lines = re.findall(r"^(P\d+) \| ([^|]+) \|", prompt, re.M)
    gas = [p for p, issue in lines if "gas" in issue.lower()]
    rest = [p for p, issue in lines if "gas" not in issue.lower()]
    return {"questions": [{"area": "transportation", "question": "Gas tax", "positions": gas}]
            + [{"area": "Transportation", "question": "High-speed rail", "positions": [p]} for p in rest]}


def setup(tmp):
    tmp = Path(tmp)
    wedge.RAW_DIR, wedge.CACHE_DIR, wedge.PROCESSED_DIR = tmp / "raw", tmp / "cache", tmp / "processed"
    wedge.RAW_DIR.mkdir()
    for cid, text in SOURCE.items():
        (wedge.RAW_DIR / f"{cid}.txt").write_text(text, encoding="utf-8")
    wedge.call_json = fake_call_json
    wedge.active_model = lambda fast=False: "fake-fast" if fast else "fake"


def test_clean_groups():
    short = {"P1": "x.1", "P2": "x.2", "P3": "y.1"}
    flags = []
    out = wedge.clean_groups([
        {"area": "housing", "question": "Rent control", "positions": ["P1", "P3", "P9"]},
        {"area": "Housing", "question": "Zoning", "positions": ["P1"]},
    ], short, flags)
    assert out[0] == {"area": "Housing", "question": "Rent control", "positions": ["x.1", "y.1"]}, out[0]
    assert len(out) == 2 and out[1]["positions"] == ["x.2"], "a left-out position stands alone"
    assert out[1]["area"] == "Ungrouped"
    assert flags and flags[0]["heuristic"] is None
    assert flags[0]["item"] == {"unknown": ["P9"], "repeated": ["P1"], "missing": ["x.2"]}, flags[0]["item"]
    flags = []
    wedge.clean_groups([{"area": "A", "question": "Q", "positions": list(short)}], short, flags)
    assert not flags, "a clean grouping raises no flag"


def test_overlap_report():
    pos = {"a": [{"id": "a.1"}, {"id": "a.2"}], "b": [{"id": "b.1"}], "c": [], "d": [{"id": "d.1"}]}
    qs = [{"area": "Tax", "question": "Gas tax", "candidates": ["a", "b"]},
          {"area": "Tax", "question": "Income tax", "candidates": ["a"]},
          {"area": "Water", "question": "Dams", "candidates": ["d"]}]
    r = wedge.overlap_report(["a", "b", "c", "d", "e"], pos, qs)
    assert r["candidates"] == {"in_race": 5, "with_sources": 4, "with_positions": 3}, r["candidates"]
    assert r["questions_by_candidates"] == {"1": 2, "2-4": 1, "5-9": 0, "10+": 0}
    assert r["pairs"]["count"] == 3 and r["pairs"]["median_shared_questions"] == 0
    assert r["pairs"]["share_with_none"] == 0.67, "only a and b share a question"
    assert r["areas"] == {"Tax": 2, "Water": 1}
    assert r["widest"][0]["question"] == "Gas tax"
    assert sorted(r["on_no_wide_question"]) == ["a", "b", "d"], "no question reaches 5 candidates"


def test_positions_only_end_to_end():
    prompts.clear()
    out = wedge.positions_only("ca_gov_primary")
    # Only candidates with sources are sent to the model
    listed = [p for p in prompts if "List up to" in p]
    assert len(listed) == 2, len(listed)
    # The made-up quote is dropped (heuristic 1); the real ones are kept
    assert [p["issue"] for p in out["positions"][A]] == ["Gas Tax", "Rail"]
    assert any(f["heuristic"] == 1 and f["candidate"] == A for f in out["flags"])
    # The grouping call never sees names or ids
    group = next(p for p in prompts if "Group the positions" in p)
    for cid in (A, B):
        assert CANDIDATES[cid]["name"] not in group and cid not in group, "names stay out of grouping"
    gas = next(q for q in out["questions"] if q["question"] == "Gas tax")
    assert gas["candidates"] == sorted([A, B]) and gas["area"] == "Transportation"
    rep = out["report"]
    assert rep["candidates"]["in_race"] == len(RACE["candidates"])
    assert rep["candidates"]["with_sources"] == 2 and rep["positions"] == 3
    saved = json.loads((wedge.PROCESSED_DIR / "positions_ca_gov_primary.json").read_text())
    assert saved["report"] == rep
    # A second run with nothing changed makes no model calls
    prompts.clear()
    wedge.positions_only("ca_gov_primary")
    assert not prompts, f"{len(prompts)} call(s) on an unchanged rerun"


def test_anthropic_call():
    """Effort is sent to 5.x models, thinking blocks are skipped, stops raise."""
    import sys, types, os
    sent = []

    class Block:
        def __init__(self, type, text=""):
            self.type, self.text = type, text

    def client_with(stop, blocks):
        class Stream:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get_final_message(self):
                return types.SimpleNamespace(stop_reason=stop, content=blocks)

        class Messages:
            def stream(self, **kw):
                sent.append(kw)
                return Stream()
        return lambda: types.SimpleNamespace(messages=Messages())

    real = sys.modules.get("anthropic")
    try:
        sys.modules["anthropic"] = types.SimpleNamespace(
            Anthropic=client_with("end_turn", [Block("thinking"), Block("text", '{"ok": 1}')]))
        assert wedge._call_anthropic("p", "claude-haiku-5-5", "low") == '{"ok": 1}'
        assert sent[-1]["extra_body"] == {"output_config": {"effort": "low"}}
        assert sent[-1]["max_tokens"] == wedge.MAX_TOKENS
        wedge._call_anthropic("p", "claude-haiku-4-5", "low")
        assert "extra_body" not in sent[-1], "older models get no effort setting"
        for stop in ("refusal", "max_tokens"):
            sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=client_with(stop, []))
            try:
                wedge._call_anthropic("p", "claude-sonnet-5-5")
                assert False, f"{stop} must raise"
            except wedge.ModelStopped:
                pass
    finally:
        if real is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = real
    os.environ["ANTHROPIC_API_KEY"] = "test"
    real_active = wedge.active_model
    try:
        wedge.active_model = lambda fast=False: "claude-haiku-5-5" if fast else "claude-sonnet-5-5"
        assert wedge.step_settings("write") == ("claude-haiku-5-5", "medium"), "cheap writer"
        assert wedge.step_settings("verify") == ("claude-haiku-5-5", "high"), "checks run on the cheap model too"
        os.environ["ZIPVOTE_EFFORT_VERIFY"] = "medium"
        assert wedge.step_settings("verify")[1] == "medium", "one step's effort can be overridden"
    finally:
        wedge.active_model = real_active
        os.environ.pop("ZIPVOTE_EFFORT_VERIFY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_match_quote():
    page = wedge.Segment("https://becerra.example.com", [
        "As the husband of a high-risk Ob-Gyn, I know that abortion access is health care.",
        "As HHS Secretary, I ensured that Planned Parenthood funding got into the states.",
        "As governor, I will ensure that California continues to lead on abortion access.",
    ], "campaign site")
    other = wedge.Segment("https://news.example.com", ["Becerra said he will raise the minimum wage to twenty dollars."])
    pages = [other, page]
    # As written, found on one page
    q = "I know that abortion access is health care. As HHS Secretary, I ensured that Planned Parenthood funding got into the states."
    assert wedge.match_quote(q, pages) == ("https://becerra.example.com", q, 1)
    # Two real sentences from different parts of the page: kept, shown with the gap
    joined = ("As the husband of a high-risk Ob-Gyn, I know that abortion access is health care. "
              "As governor, I will ensure that California continues to lead on abortion access.")
    src, shown, pieces = wedge.match_quote(joined, pages)
    assert src == "https://becerra.example.com" and pieces == 2
    assert shown == ("As the husband of a high-risk Ob-Gyn, I know that abortion access is health care. … "
                     "As governor, I will ensure that California continues to lead on abortion access."), shown
    # The model's own ellipsis is a gap too
    assert wedge.match_quote("I know that abortion access is health care... As governor, I will ensure that California", pages)[2] == 2
    # One sentence not on the page: the whole quote is dropped
    assert wedge.match_quote("I know that abortion access is health care. I will ban all fracking by next year.", pages) is None
    # Real sentences from two different pages don't combine
    assert wedge.match_quote("I know that abortion access is health care. "
                             "Becerra said he will raise the minimum wage to twenty dollars.", pages) is None
    # A tiny real fragment can't carry a made-up sentence
    assert wedge.match_quote("As governor, I will. Abolish every tax in the state today.", pages) is None
    assert wedge.locate_quote(joined, pages) == "https://becerra.example.com"


def st(sid, area, scores):
    return {"id": sid, "area": area, "text": sid, "candidateAgreement": scores}


def test_select_core_large_field():
    wedge.CORE_SIZE, saved = 3, wedge.CORE_SIZE
    try:
        pool = [
            st("agree_all", "Housing", {"a": 5, "b": 4, "c": 5, "d": 4}),     # 4 scored, nobody disagrees
            st("split", "Housing", {"a": 5, "b": 1}),                        # 2 scored, opposite sides
            st("split_big", "Taxes", {"a": 5, "b": 1, "c": 2}),              # 3 scored, opposite sides
            st("housing3", "Housing", {"c": 1, "d": 5}),
            st("solo", "Water", {"e": 5}),
        ]
        core = wedge.select_core(pool)
        assert [s["id"] for s in core] == ["split_big", "housing3", "split"], [s["id"] for s in core]
        assert pool[0]["core"] is None and pool[4]["core"] is None
        # Area limit: a third Housing question only comes in after other areas run out
        wedge.CORE_PER_AREA = 1
        core = wedge.select_core(pool)
        assert [s["id"] for s in core] == ["split_big", "housing3", "split"], "second pass lifts the area limit"
    finally:
        wedge.CORE_SIZE, wedge.CORE_PER_AREA = saved, 2


def test_select_core_small_field():
    wedge.CORE_SIZE, saved = 4, wedge.CORE_SIZE
    try:
        pool = [st("shared", "Tax", {"a": 5, "b": 1}),
                st("a1", "Tax", {"a": 5}), st("a2", "Rail", {"a": 4}), st("a3", "Water", {"a": 2}),
                st("b1", "Water", {"b": 5}), st("c1", "Guns", {"c": 1})]
        core = [s["id"] for s in wedge.select_core(pool)]
        assert core[0] == "shared", core
        assert "c1" in core and "b1" in core, "single-candidate questions are spread, fewest first"
        assert len(core) == 4
    finally:
        wedge.CORE_SIZE = saved


def test_area_chunks():
    qs = [{"area": "Housing", "positions": [f"x.{i}" for i in range(20)]},
          {"area": "Housing", "positions": ["y.1", "y.2", "y.3", "y.4", "y.5", "y.6"]},
          {"area": "Housing", "positions": ["z.1"]},
          {"area": "Taxes", "positions": ["x.99"]}]
    chunks = wedge.area_chunks(qs)
    assert [(a, [len(q["positions"]) for q in b]) for a, b in chunks] == \
        [("Housing", [20]), ("Housing", [6, 1]), ("Taxes", [1])], chunks


def test_pool_end_to_end():
    out = wedge.process_pool("ca_gov_primary")
    statements = out["statements"]
    gas = next(s for s in statements if "Gas tax" in s["text"])
    assert gas["candidateAgreement"][A] == 5 and gas["candidateAgreement"][B] == 1
    assert gas["evidence"][A]["position"].startswith(A), "evidence names the cited position"
    assert gas["area"] == "Transportation"
    assert out["report"]["opposite_sides"] == 1 and out["report"]["shared"] == 1
    assert all(s["core"] for s in statements), "a small pool all fits in the core"
    assert next(s for s in statements if s["core"] == 1) is gas, "the shared split comes first"
    assert out["report"]["positions_card_only"] == 0
    # The writing prompt never sees names or ids
    pool_prompts = [p for p in prompts if "covers one issue area" in p]
    assert pool_prompts and all(CANDIDATES[c]["name"] not in p and c not in p
                                for p in pool_prompts for c in (A, B))


def scored(sid, cid, text):
    return {"id": sid, "area": "Taxes", "text": text, "candidateAgreement": {cid: 5},
            "evidence": {cid: {"quote": "q", "source": "s", "position": f"{cid}.1"}},
            "candidatePositions": {cid: "x"}}


def test_verify_splits_fixes_wording_and_marks_consensus():
    real = wedge.call_json
    calls = []

    def fake(prompt, retries=2, fast=False, step=None):
        calls.append(step)
        n = prompt.count("  candidate_id:")
        if n > 1:
            raise wedge.ModelStopped("ran out of room (max_tokens)")
        sid = re.search(r"statement_id: (\S+)", prompt).group(1)
        wording = {"statement_id": sid, "loaded": False, "contested": True}
        if sid == "t_s2":
            wording["contested"] = False
        if sid == "t_s3":
            wording = {"statement_id": sid, "loaded": True, "note": "bundles two policies",
                       "fixed_text": "California should tax AI companies."}
        if sid == "t_s4":
            wording = {"statement_id": sid, "loaded": True, "note": "a strawman", "fixed_text": None}
        return {"scores": [], "wording": [wording]}

    wedge.call_json = fake
    try:
        data = {"statements": [scored("t_s1", "a", "California should cap rent increases."),
                               scored("t_s2", "b", "State spending should be audited."),
                               scored("t_s3", "c", "California should tax AI companies to fund a wealth fund."),
                               scored("t_s4", "d", "Loopholes should stay open for big companies.")]}
        flags = []
        wedge.verify_in_batches(data, flags, size=4)
        ids = {s["id"]: s for s in data["statements"]}
        assert set(ids) == {"t_s1", "t_s2", "t_s3"}, "split, fixed in place; the unfixable one is dropped"
        assert "rewrite" not in calls, "no separate rewrite round"
        assert not [f for f in flags if f["heuristic"] is None], "no score left unchecked"
        assert ids["t_s2"].get("consensus") is True
        assert ids["t_s3"]["text"] == "California should tax AI companies."
        assert ids["t_s3"]["draft"].startswith("California should tax AI companies to fund")
        assert ids["t_s3"]["candidateAgreement"] == {"c": 5}, "the fix keeps the candidate's score"
        core = [s["id"] for s in wedge.select_core(data["statements"])]
        assert "t_s2" not in core, "a statement nearly everyone agrees with stays out of the core"
    finally:
        wedge.call_json = real

def test_writer_gets_a_second_call_for_skipped_positions():
    real = wedge.call_json
    seen = []

    def fake(prompt, retries=2, fast=False, step=None):
        seen.append(prompt)
        # Writes a statement for the first position listed only, as a model that loses track might
        c, p = re.search(r"^- (C\d+) \[(P\d+)\]", prompt, re.M).groups()
        return {"statements": [{"topic": "t", "text": f"Statement {len(seen)}.", "basis": {c: p},
                                "candidateAgreement": {c: 4}, "candidatePositions": {c: "x"}}], "skipped": []}

    wedge.call_json = fake
    try:
        by_id = {"x.1": {"issue": "Rent", "stance": "Cap rent."}, "y.1": {"issue": "Rail", "stance": "Build rail."}}
        race = {"id": "t_retry", "title": "Test", "state_name": "California"}
        qs = [{"area": "Housing", "question": "Rent", "positions": ["x.1"]},
              {"area": "Housing", "question": "Rail", "positions": ["y.1"]}]
        flags = []
        statements, skipped = wedge.write_area_statements(race, "Housing", qs, by_id, flags)
        assert len(seen) == 2 and "Build rail" in seen[1] and "Cap rent" not in seen[1]
        assert {pid for s in statements for pid in s["basis"].values()} == {"x.1", "y.1"}
        assert not [f for f in flags if f["heuristic"] == 6]
    finally:
        wedge.call_json = real


def test_rollup_finds_opposite_sides():
    real = wedge.call_json
    seen = []

    def fake(prompt, retries=2, fast=False, step=None):
        seen.append(prompt)
        codes = dict(re.findall(r"^- (C\d+) \[(P\d+)\]", prompt, re.M))
        (c1, p1), (c2, p2) = list(codes.items())[:2]
        return {"statements": [
            {"text": "Property taxes in California should be lower.", "basis": {c1: p1, c2: p2},
             "candidateAgreement": {c1: 5, c2: 2}, "candidatePositions": {c1: "a", c2: "b"}},
            {"text": "Only one candidate scored here.", "basis": {c1: p1},
             "candidateAgreement": {c1: 4}, "candidatePositions": {c1: "a"}},
            {"text": "Uses a made-up code.", "basis": {"C9": "P9"}, "candidateAgreement": {"C9": 5}},
        ]}

    wedge.call_json = fake
    try:
        by_id = {"x.1": {"issue": "Property Tax", "stance": "Eliminate property taxes."},
                 "y.1": {"issue": "Property Tax", "stance": "Reassess commercial property at market value."}}
        race = {"id": "t_roll", "title": "Test", "state_name": "California"}
        qs = [{"area": "Taxes", "question": "Eliminate property taxes", "positions": ["x.1"]},
              {"area": "Taxes", "question": "Commercial reassessment", "positions": ["y.1"]}]
        existing = [{"text": "California should eliminate property taxes.", "area": "Taxes"}]
        ups = wedge.rollup_area(race, "Taxes", qs, existing, by_id, [])
        assert len(ups) == 1, "only debates two or more candidates take a side on are kept"
        up = ups[0]
        assert up["rollup"] and up["basis"] == {"x": "x.1", "y": "y.1"}
        assert up["candidateAgreement"] == {"x": 5, "y": 2}
        assert "eliminate property taxes." in seen[0], "the existing statements are shown, so none is repeated"
        assert "x.1" not in seen[0] and "Eliminate property taxes." in seen[0], "codes, not ids, in the prompt"
        # One candidate in the area: no call at all
        seen.clear()
        assert wedge.rollup_area(race, "Taxes", qs[:1], [], by_id, []) == [] and not seen
    finally:
        wedge.call_json = real


def test_meter_and_prices():
    m = wedge.Meter()
    m.add("write", "claude-haiku-5-5", 50_000, 1_000_000)    # 0.005 in + 0.50 out
    m.add("verify", "claude-sonnet-5-5", 100_000, 50_000)    # 0.20 in + 0.50 out
    m.add("write", "claude-haiku-5-5", 200_000, 0)          # over 100K: the higher rate, 0.10
    sm = m.summary()
    by = {r["step"]: r for r in sm["steps"]}
    assert by["write"]["calls"] == 2 and by["write"]["usd"] == round(0.005 + 0.50 + 0.10, 4), by["write"]
    assert by["verify"]["usd"] == round(0.2 + 0.5, 4)
    assert sm["total_usd"] == round(0.605 + 0.70, 4)


def test_core_never_repeats_a_position_and_disagreement_beats_consensus():
    def s_(sid, area, scores, pos):
        return {"id": sid, "area": area, "text": sid, "candidateAgreement": scores,
                "evidence": {c: {"position": pos[c]} for c in scores}}
    pool = [s_("loosen_guns", "Guns", {"a": 1, "b": 5}, {"a": "a.1", "b": "b.1"}),
            s_("restrict_guns", "Guns", {"a": 5, "b": 1}, {"a": "a.1", "b": "b.1"}),   # same quotes, flipped
            s_("ban_rifles", "Guns", {"a": 5, "c": 1}, {"a": "a.2", "c": "c.1"})]
    core = [s["id"] for s in wedge.select_core(pool)]
    assert "loosen_guns" in core and "restrict_guns" not in core, core
    assert "ban_rifles" in core, "a different quote from the same candidate is a different question"

    real = wedge.call_json
    try:
        wedge.call_json = lambda prompt, retries=2, fast=False, step=None: {
            "scores": [], "wording": [{"statement_id": "x_s1", "loaded": False, "contested": False}]}
        data = {"statements": [{"id": "x_s1", "text": "California should protect legal access to abortion.",
                                "candidateAgreement": {"a": 5, "b": 1},
                                "evidence": {"a": {"quote": "q", "source": "s"}, "b": {"quote": "q", "source": "s"}},
                                "candidatePositions": {"a": "x", "b": "y"}}]}
        wedge.verify_with_llm(data, [])
        assert not data["statements"][0].get("consensus"), "a candidate who disagrees proves it's contested"
    finally:
        wedge.call_json = real


def test_verify_reuses_an_unchanged_check():
    real = wedge.call_json
    calls = []

    def fake(prompt, retries=2, fast=False, step=None):
        calls.append(step)
        return {"scores": [], "wording": []}

    wedge.call_json = fake
    try:
        def batch():
            return {"statements": [scored("v_s1", "a", "California should cap rent increases at five percent.")]}
        wedge.verify_with_llm(batch(), [])
        wedge.verify_with_llm(batch(), [])
        assert calls == ["verify"], calls
    finally:
        wedge.call_json = real


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        failed = 0
        for t in tests:
            try:
                t()
                print(f"PASS {t.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    raise SystemExit(1 if failed else 0)
