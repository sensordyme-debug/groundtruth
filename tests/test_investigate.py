"""Investigations: plan compilation + validation, dependency-aware execution, evidence, findings, report, policy."""
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
