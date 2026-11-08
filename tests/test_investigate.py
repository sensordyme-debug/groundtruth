"""Investigations: plan compilation + validation, dependency-aware execution, evidence, findings, report, policy."""
import json
import pytest
from pydantic import ValidationError

from groundtruth import investigate as I
from groundtruth.envelope import Envelope, Result, Source, declined
from groundtruth.tools import _people

HOUSE = lambda i, **p: {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[-80.9 + i * 1e-3, 35.5], [-80.899 + i * 1e-3, 35.5],
                                                                                                [-80.899 + i * 1e-3, 35.501], [-80.9 + i * 1e-3, 35.5]]]},
                         "properties": {"id": f"h{i}", "kind": "house_with_pool", **p}}


def test_policy_whole_words():
    assert _people("who owns these homes")
    assert _people("the residents of this street")
    assert not _people("residential properties near Lake Norman with swimming pools")
    assert not _people("terrace houses and the racetrack")


def test_compile_flagship_plan():
    plan, info = I.compile_plan("Find residential properties near Lake Norman with swimming pools that intersect mapped "
                                "100-year flood zones. Rank the strongest candidates.", {})
    assert plan is not None, info
    ops = [s.op for s in plan.steps]
    assert ops[:3] == ["resolve_area", "check_coverage", "find_derived"]
    assert "filter" in ops and "explain" in ops and "rank" in ops and ops[-1] == "conclude"
    flt = next(s for s in plan.steps if s.op == "filter")
    assert flt.args["where"] == {"in_flood_zone": {"kind": "100yr"}}
    assert plan.scope["place"] == "Lake Norman"
    # every input step depends on an earlier step
    for s in plan.steps:
        if s.op in ("filter", "explain", "rank"):
            assert s.depends_on and all(d < s.id or len(d) < len(s.id) for d in s.depends_on)


def test_compile_asks_where_without_area():
    plan, info = I.compile_plan("homes with pools in the 100-year flood zone", {})
    assert plan is None and info["question"] and info["suggestions"]


def test_compile_falls_back_to_chat_for_non_investigations():
    plan, info = I.compile_plan("hello there", {})
    assert plan is None and info.get("fallback") == "chat"


def test_plan_validation_rejects_bad_plans():
    with pytest.raises(ValidationError):
        I.InvestigationPlan(objective="x", steps=[I.PlanStep(id="s1", op="run_python", args={})])
    with pytest.raises(ValidationError):  # filter needs an input step
        I.InvestigationPlan(objective="x", steps=[I.PlanStep(id="s1", op="filter", args={"where": {"in_flood_zone": {"kind": "100yr"}}})])
    with pytest.raises(ValidationError):  # unknown predicate
        I.InvestigationPlan(objective="x", steps=[I.PlanStep(id="s1", op="resolve_area", args={"place": "Lake Norman"}),
                                                  I.PlanStep(id="s2", op="filter", args={"where": {"sql": "drop table"}}, depends_on=["s1"])])
    with pytest.raises(ValidationError):  # dependency must come first
        I.InvestigationPlan(objective="x", steps=[I.PlanStep(id="s1", op="explain", args={"facts": ["flood"]}, depends_on=["s2"]),
                                                  I.PlanStep(id="s2", op="resolve_area", args={"place": "Lake Norman"})])


class FakeProvider:
    def list_scenes(self, bb, year):
        return []

    def local_scene_path(self, *a):
        raise AssertionError


class FakeEngine:
    """Deterministic stand-in for the engine: derived houses, a flood filter, explain. Fails on demand."""
    provider = FakeProvider()

    def __init__(self, fail_derived=False):
        self.fail_derived = fail_derived

    def place_bbox(self, place):
        return [-80.96, 35.42, -80.84, 35.52] if place == "Lake Norman" else None

    def find_derived(self, kind, place=None, require=(), docks_new_since=None, exclude_ids=(), **kw):
        if self.fail_derived:
            return declined("find_derived", "x", "not precomputed here")
        fs = [HOUSE(i, pool_area_m2=30 + i) for i in range(5)]
        return Envelope(tool="find_derived", version="x", status="ok", result=Result(
            geojson={"type": "FeatureCollection", "features": fs}, metrics={"count": 5, "of_houses_with_pools": 9, "pools_in_place": 20}),
            sources=[Source(name="NAIP 2022", url="u", date="2022", licence="PD")])

    def filter(self, gj, where):
        fs = [f for f in gj["features"] if int(f["properties"]["id"][1:]) % 2 == 0]
        for f in fs:
            f["properties"].update(flood_kind="100yr", flood_zone="AE")
        return Envelope(tool="filter", version="x", status="ok", result=Result(
            geojson={"type": "FeatureCollection", "features": fs}, metrics={"count_in": len(gj["features"]), "count_out": len(fs)}),
            sources=[Source(name="FEMA NFHL", url="u", date="2024", licence="PD")])

    def explain(self, gj, facts=(), label=None, unit="results", **kw):
        fs = [dict(f, properties={**f["properties"], "why": "Pool · Zone AE", "elev_above_bfe_m": (0.5 if f["properties"]["id"] != "h4" else None),
                                  "elev_bfe_status": "ok" if f["properties"]["id"] != "h4" else "no_bfe", "shore_dist_m": 20.0})
              for f in gj["features"]]
        return Envelope(tool="explain", version="x", status="ok", result=Result(
            geojson={"type": "FeatureCollection", "features": fs}, metrics={"insights": ["3 homes"]}),
            sources=[Source(name="USGS 3DEP lidar", url="u", date="2016", licence="PD")])


def _flagship(tmp_path, monkeypatch, eng):
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(I, "coverage", lambda e, a: {"flood maps": {"covered": True, "why": ""},
                                                       "lidar": {"covered": False, "why": "no lidar here"}})
    inv = I.start("homes with pools in Lake Norman in the 100-year flood zone, rank them", {}, lambda y: eng,
                  lambda t: "people" if _people(t) else None, background=False)
    return inv


def test_run_flagship_with_fake_engine(tmp_path, monkeypatch):
    inv = _flagship(tmp_path, monkeypatch, FakeEngine())
    assert inv.status == "done", [(s.id, s.status, s.error) for s in inv.plan.steps]
    assert all(s.status == "done" for s in inv.plan.steps)
    ranked = [f for f in inv.result["features"]]
    assert [f["properties"]["id"] for f in ranked] == ["h0", "h2", "h4"]  # missing BFE height sorts last
    kinds = {e.kind for e in inv.evidence}
    assert {"scope", "coverage", "detection", "association", "spatial_intersection", "measurement", "ordering"} <= kinds
    head = inv.findings[0]
    assert "3 of 5" in head.statement and head.strength == "weak"  # weakest link: unmeasured detector precision
    assert any(f.nature == "gap" for f in inv.findings)  # 1 of 3 lacks a BFE + no lidar coverage
    md = I.report_markdown(inv)
    assert "## Findings" in md and "## Limitations and gaps" in md and inv.objective in md
    ev = I.feature_evidence(inv, "h4")
    assert ev["found"] and ev["missing"]


def test_failed_step_blocks_dependents(tmp_path, monkeypatch):
    inv = _flagship(tmp_path, monkeypatch, FakeEngine(fail_derived=True))
    st = {s.op: s.status for s in inv.plan.steps}
    assert st["find_derived"] == "failed" and st["filter"] == "blocked" and st["explain"] == "blocked"
    assert st["resolve_area"] == "done" and st["conclude"] == "done"
    assert inv.status in ("partial", "failed") and inv.result is None


def test_declined_before_planning(tmp_path, monkeypatch):
    inv = _flagship.__wrapped__ if False else None
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    inv = I.start("who owns these homes", {}, lambda y: FakeEngine(), lambda t: "no people data" if _people(t) else None,
                  background=False)
    assert inv.status == "declined" and inv.plan is None


class ElevEngine(FakeEngine):
    """Filter on elevation: 3 known (0.5, 0.5, 2.0 m), 1 unknown; < 1 m keeps 2."""

    def explain(self, gj, facts=(), label=None, unit="results", **kw):
        vals = {"h0": 0.5, "h1": 2.0, "h2": 0.5, "h3": None, "h4": None}
        fs = [dict(f, properties={**f["properties"], "elev_above_bfe_m": vals.get(f["properties"]["id"])}) for f in gj["features"]]
        return Envelope(tool="explain", version="x", status="ok", result=Result(
            geojson={"type": "FeatureCollection", "features": fs}, metrics={"insights": []}),
            sources=[Source(name="USGS 3DEP lidar", url="u", date="2016", licence="PD")])

    def filter(self, gj, where):
        if "elevation_above_flood_m" in where:
            lt = where["elevation_above_flood_m"]["lt"]
            fs = [f for f in gj["features"] if f["properties"].get("elev_above_bfe_m") is not None and f["properties"]["elev_above_bfe_m"] < lt]
            return Envelope(tool="filter", version="x", status="ok", result=Result(
                geojson={"type": "FeatureCollection", "features": fs}, metrics={}),
                sources=[Source(name="USGS 3DEP lidar", url="u", date="2016", licence="PD")])
        return super().filter(gj, where)


def test_denominator_integrity_unknown_stays_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(I, "coverage", lambda e, a: {"flood maps": {"covered": True, "why": ""}})
    inv = I.start("homes with pools in Lake Norman less than 1 m above the flood elevation", {}, lambda y: ElevEngine(),
                  lambda t: None, background=False)
    ev = next(e for e in inv.evidence if e.measurement.get("passing") is not None)
    # 5 homes: h0 0.5, h1 2.0, h2 0.5 known; h3, h4 unknown → 2 pass, 1 fails, 2 unknown
    assert ev.measurement == {"total": 5, "known": 3, "passing": 2, "failing": 1, "unknown": 2, "kept": 2, "of": 5}
    assert "2 of the 3 with a known" in ev.statement and "2 of 5 have no" in ev.statement


def test_step_timeout_blocks_dependents(tmp_path, monkeypatch):
    import time as _t

    class Slow(FakeEngine):
        def find_derived(self, *a, **k):
            _t.sleep(2)
            return super().find_derived(*a, **k)

    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(I, "coverage", lambda e, a: {})
    c = I.conf()
    monkeypatch.setattr(I, "conf", lambda: {**c, "limits": {**c["limits"], "step_timeout_s": 0.3}})
    inv = I.start("homes with pools in Lake Norman in the 100-year flood zone", {}, lambda y: Slow(), lambda t: None, background=False)
    st = {s.op: s for s in inv.plan.steps}
    assert st["find_derived"].status == "failed" and "timed out" in st["find_derived"].error
    assert st["filter"].status == "blocked" and inv.status in ("partial", "failed")


# ---- geography in plans (needs data/boundaries/v1; skipped without it)
def _geo_ready():
    try:
        from groundtruth import geography as G
        return G.resolve("Mecklenburg County").resolved
    except Exception:
        return False


@pytest.mark.skipif(not _geo_ready(), reason="boundary data not built")
def test_county_never_falls_back_to_the_map_area():
    plan, _ = I.compile_plan("restaurants in Mecklenburg County", {"area": {"place": "Myers Park", "label": "Myers Park"}})
    assert plan.scope["kind"] == "county" and plan.scope["label"] == "Mecklenburg County" and plan.scope.get("geojson")


@pytest.mark.skipif(not _geo_ready(), reason="boundary data not built")
def test_city_zip_radius_and_outside():
    assert I.compile_plan("buildings taller than 30 m in Charlotte", {})[0].scope["kind"] == "city"
    assert I.compile_plan("restaurants in 28202", {})[0].scope["kind"] == "zip"
    assert I.compile_plan("restaurants within 5 miles of Lake Norman", {})[0].scope["kind"] == "radius"
    plan, info = I.compile_plan("restaurants in Gastonia", {})
    assert plan is None and "outside" in info["question"].lower()
    # the derived Lake Norman set keeps its place scope (homes are on land, not in the water polygon)
    plan, _ = I.compile_plan("homes with pools in Lake Norman in the 100-year flood zone", {})
    assert plan.scope.get("place") == "Lake Norman" and "geojson" not in plan.scope


@pytest.mark.skipif(not _geo_ready(), reason="boundary data not built")
def test_shore_distance_is_a_condition_not_an_area():
    plan, info = I.compile_plan("homes with pools within 30 m of the shore in Lake Norman", {})
    assert plan is not None, info
    assert plan.scope.get("place") == "Lake Norman"
    assert any(s.op == "filter" and "within_m_of_shore" in s.args["where"] for s in plan.steps)


def test_yelp_never_shown_while_display_gated(tmp_path, monkeypatch):
    """Yelp is enabled (key present) but its display requirements aren't met: it must not reach the investigation."""
    import groundtruth.places as GP

    class Prov:
        def __init__(self, name, display):
            self.pconf = {"display_enabled": display, "display_blocker": "logo + §5(g) pending"}
            self.name = name

        def status(self):
            return {"enabled": True}

    asked = {}

    def fake_search(query, area, providers=None, investigation_id=None, **kw):
        asked["providers"] = providers
        return Envelope(tool="search_places", version="x", status="ok", result=Result(
            geojson={"type": "FeatureCollection", "features": []}, metrics={}))
    monkeypatch.setattr(GP, "build_providers", lambda: {"foursquare": Prov("foursquare", True), "yelp": Prov("yelp", False)})
    monkeypatch.setattr(GP, "search_places", fake_search)
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    plan = I.InvestigationPlan(objective="restaurants", scope={"place": "Myers Park"}, steps=[
        I.PlanStep(id="s1", op="search_providers", args={"query": "restaurant", "place": "Myers Park"})])
    inv = I.new("restaurants", {})
    inv.plan = plan
    I.run(inv, FakeEngine())
    assert asked["providers"] == ["foursquare"]
    assert any("Yelp listings are not shown" in x for x in inv.plan.limitations)


def test_headline_quality_and_consistency(tmp_path, monkeypatch):
    """The headline states scope, candidate status (no implied human verification), the count with its denominator,
    the ranking rule and the limits; every number in it is in the evidence."""
    import re as _re
    inv = _flagship(tmp_path, monkeypatch, FakeEngine())
    s = inv.summary
    assert "Lake Norman" in s and "AI-detected candidates" in s and "none human-verified" in s
    assert "3 of 5" in s
    assert "Ordered by" in s and "not a risk score" in s
    assert "Limits:" in s
    assert "verified by" not in s.replace("human-verified", "")  # never claims verification
    blob = " ".join(e.statement + " " + json.dumps(e.measurement) for e in inv.evidence)
    for n in _re.findall(r"\b\d+\b", s):
        if n in ("100", "2016", "19", "2022"):  # part of named things: "100-year", lidar/imagery years
            continue
        assert n in blob, f"{n} in the headline is not in the evidence"


def test_headline_mentions_human_reviews_when_present(tmp_path, monkeypatch):
    inv = _flagship(tmp_path, monkeypatch, FakeEngine())
    inv.evidence.append(I.Evidence(id="e99", step_id="human", kind="human", statement="A reviewer confirmed result h0.",
                                   feature_ids=["h0"], strength="strong"))
    assert "1 reviewed by a person" in I._summary(inv)


def test_cooperative_cancellation_stops_between_steps(tmp_path, monkeypatch):
    """Cancel is cooperative: the running step finishes, every later step is skipped, the status is 'cancelled'."""
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)
    inv = I.new("x", {})
    inv.plan = I.InvestigationPlan(objective="x", steps=[
        I.PlanStep(id="s1", op="resolve_area", args={"place": "Myers Park"}),
        I.PlanStep(id="s2", op="resolve_area", args={"place": "Myers Park"}, depends_on=["s1"]),
        I.PlanStep(id="s3", op="resolve_area", args={"place": "Myers Park"}, depends_on=["s2"])])
    real = I._exec
    ran = []

    def exec_and_cancel(s, *a, **k):
        ran.append(s.id)
        if s.id == "s1":
            I.cancel(inv.id)  # the user presses Stop while s1 runs
        return real(s, *a, **k)
    monkeypatch.setattr(I, "_exec", exec_and_cancel)
    I.run(inv, FakeEngine())
    assert ran == ["s1"]
    assert [s.status for s in inv.plan.steps][1:] == ["skipped", "skipped"]
    assert inv.status == "cancelled" and inv.id not in I._CANCEL


def test_headline_never_repeats_the_unknowns(tmp_path, monkeypatch):
    inv = _flagship(tmp_path, monkeypatch, FakeEngine())
    inv.findings[0].statement = "12 of the 13 sit low; 7 of 20 stay unknown."
    inv.evidence.append(I.Evidence(id="e98", step_id="s9", kind="measurement", statement="x",
                                   measurement={"unknown": 7, "of": 20}, strength="moderate"))
    s = I._summary(inv)
    assert s.count("7 of 20") == 1, s
