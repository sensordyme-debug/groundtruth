"""Scenario exposure investigation: planner → simulate → exposure → cluster → findings labelled SIMULATED; a missing
layer is UNKNOWN (skipped + a gap), never zero. Runs on small synthetic fixtures; no lidar build needed."""
import pytest

from groundtruth import exposure as X
from groundtruth import investigate as I

SIGNATURE = ("Investigate which areas around this part of Lake Norman could face the greatest infrastructure exposure "
             "under a hypothetical elevated-water scenario. Identify potentially affected buildings, roads, bridges, and "
             "nearby emergency facilities. Explain the evidence and uncertainty.")


def sq(x, y, d=0.0002, **p):
    return {"type": "Feature", "properties": p, "geometry": {"type": "Polygon", "coordinates": [[
        [x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}}


def test_signature_question_plans_the_scenario_steps():
    p, info = I.compile_plan(SIGNATURE, {})
    assert p is not None, info
    ops = [(s.op, s.args.get("target")) for s in p.steps]
    assert ops[:3] == [("resolve_area", None), ("check_coverage", None), ("simulate", None)]
    assert [t for o, t in ops if o == "exposure"] == ["buildings", "roads", "bridges", "facilities"]
    assert ops[-2:] == [("cluster", None), ("conclude", None)]
    assert p.steps[2].args == {"scenario": "ln_water_level", "offset_m": 1.0}
    assert any("used FEMA's base flood elevation + 1 m" in x for x in p.limitations)  # the assumed level is stated
    assert any(x.startswith("SIMULATED scenario") for x in p.limitations)
    assert not p.unapplied


def test_explicit_level_and_no_scenario_outside_its_place():
    p, _ = I.compile_plan("hypothetical scenario: Lake Norman water 2 m above flood level, which buildings are exposed", {})
    assert p.steps[2].args["offset_m"] == 2.0
    p, info = I.compile_plan("hypothetical scenario: which buildings in Ballantyne are exposed", {})
    assert p is None or not any(s.op == "simulate" for s in p.steps)  # no scenario defined there


def test_touching_near_and_clusters():
    x0, y0 = -80.90, 35.47  # Lake Norman: distances are measured in UTM 17N
    ext = X.union_extent([sq(x0, y0, 0.01)])
    blds = [sq(x0 + 0.001 * i, y0 + 0.001, id=f"b{i}") for i in range(5)] + [sq(x0 + 0.5, y0 + 0.5, id="far")]
    hit = X.touching(ext, blds)
    assert [f["properties"]["id"] for f in hit] == ["b0", "b1", "b2", "b3", "b4"]
    fac = [{"type": "Feature", "properties": {"osm_id": "n1", "kind": "fire_station"},
            "geometry": {"type": "Point", "coordinates": [x0 + 0.0105, y0 + 0.005]}}]  # ~45 m east of the extent
    nr = X.near(ext, fac, 500)
    assert nr and 0 < nr[0]["properties"]["dist_to_extent_m"] < 100 and "not impact" in nr[0]["properties"]["scenario_relation"]
    cl = X.clusters(hit, link_m=120, min_size=3, facilities=fac)
    assert len(cl) == 1 and cl[0]["properties"]["n_buildings"] == 5 and cl[0]["properties"]["cluster_id"] == "C1"
    assert "not a risk score" in cl[0]["properties"]["rank_reason"]
    assert cl[0]["properties"]["nearest_facility"]["kind"] == "fire_station"


@pytest.fixture()
def fake_scenario(tmp_path, monkeypatch):
    from groundtruth import risk as R
    from groundtruth import scenario as S
    d = next(x for x in S.conf()["scenarios"] if x["id"] == "ln_water_level")
    ext = [sq(-80.90, 35.47, 0.01)]
    monkeypatch.setattr(S, "meta", lambda sid: {"definition": d, "grid_m": 2, "unmeasured_land_m2": 1000.0, "levels": [
        {"offset_m": 1.0, "level_m": 232.648, "land_area_m2": 123456.0}]})
    monkeypatch.setattr(S, "extent", lambda sid, off: {"type": "FeatureCollection", "features": ext})
    from shapely.geometry import shape
    blds = [{"id": f"b{i}", "class": "house", "geom": shape(sq(-80.899 + 0.0006 * i, 35.471)["geometry"])} for i in range(4)]
    monkeypatch.setattr(S, "_buildings", lambda bb: blds)
    monkeypatch.setattr(R, "flood_facts", lambda fs, boundary=None: [{"flood_kind": "100yr" if i % 2 else None} for i, _ in enumerate(fs)])
    monkeypatch.setattr(I, "store_dir", lambda: tmp_path)


class Eng:
    class provider:
        @staticmethod
        def get_layer(name, bbox=None):
            raise FileNotFoundError(name)  # community layer not built

    def place_bbox(self, place):
        return [-80.958699, 35.427332, -80.844396, 35.514785]


def test_end_to_end_simulated_findings_and_unknown_layers(fake_scenario, monkeypatch):
    monkeypatch.setattr(I, "coverage", lambda eng, a: {})
    inv = I.new(SIGNATURE, {})
    inv.plan, _ = I.compile_plan(SIGNATURE, {})
    I.run(inv, Eng())
    st = {s.op + (":" + s.args.get("target", "") if s.op == "exposure" else ""): s.status for s in inv.plan.steps}
    assert st["simulate"] == "done" and st["exposure:buildings"] == "done" and st["cluster"] == "done"
    assert st["exposure:bridges"] == "skipped" and st["exposure:facilities"] == "skipped"  # unknown, not zero
    head = inv.findings[0]
    assert head.nature == "simulated"
    assert all(f.nature in ("simulated", "gap", "computed") for f in inv.findings)
    assert any(f.nature == "gap" and "unknown, not zero" in f.statement for f in inv.findings)
    ev = {e.kind for e in inv.evidence}
    assert "simulation" in ev
    b = next(e for e in inv.evidence if e.step_id == next(s.id for s in inv.plan.steps if s.args.get("target") == "buildings"))
    assert b.measurement["kept"] == 4 and b.measurement["in_fema_100yr"] == 2  # real FEMA cross-check, counted
    assert inv.summary.startswith("Investigated") or "Hypothetical" in inv.summary
    assert inv.status in ("done", "partial")
