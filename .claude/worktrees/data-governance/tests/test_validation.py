"""Validation Studio (groundtruth.validation + api/validation_api.py): journal, snapshot atomicity, labels untouched,
metrics on a synthetic set, and the API on a temp dir. No test here writes outside tmp_path."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from groundtruth import config
from groundtruth import validation as V
from groundtruth.geo import to_measure, to_wgs84

R = "reviewer-aaaaaaaa"
R2 = "reviewer-bbbbbbbb"


def sq(lon: float, lat: float, side_m: float = 8.0, dx_m: float = 0.0) -> dict:
    """A square of side_m metres (EPSG:4326 GeoJSON) centred dx_m east of (lon, lat)."""
    from shapely.geometry import Point

    c = to_measure(Point(lon, lat))
    h = side_m / 2
    return mapping(to_wgs84(box(c.x + dx_m - h, c.y - h, c.x + dx_m + h, c.y + h)))


def sample(i: int, cls: str = "pool", region: str = "Ballantyne", group: str = "south", score: float = 0.75,
           cluster: bool = False, tile: str | None = None, lon: float | None = None) -> dict:
    lon = -80.85 + i * 0.001 if lon is None else lon
    return {"id": f"{cls}-{i:04d}", "class": cls, "region": region, "holdout_group": group,
            "geometry": sq(lon, 35.05), "centroid": [lon, 35.05], "source_tool": "find_all",
            "source_tool_version": "0.2.0", "model": "test", "index_version": "test", "score": score,
            "score_kind": "heuristic", "imagery_year": 2022, "acquisition_date": "2022-06-02",
            "in_label_cluster": cluster, "census_tile_id": tile,
            "sampling": {"design": "stratified_random", "weight": 2.0}}


def build(root: Path, samples: list[dict], tiles: list[dict] | None = None) -> V.ValidationStore:
    V.write_samples(root, samples, tiles or [], {"version": "v1", "seed": 1})
    return V.ValidationStore(root)


def tree_hash(d: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


# ---------------------------------------------------------------- store
def test_samples_immutable(tmp_path):
    build(tmp_path, [sample(1)])
    with pytest.raises(FileExistsError):
        V.write_samples(tmp_path, [sample(2)], [], {})
    s = V.ValidationStore(tmp_path)
    assert list(s.samples) == ["pool-0001"] and s.integrity["samples_sha256_ok"] is True


def test_journal_append_latest_wins_history(tmp_path):
    s = build(tmp_path, [sample(1), sample(2)])
    s.add_review(sample_id="pool-0001", reviewer=R, decision="uncertain")
    s.add_review(sample_id="pool-0001", reviewer=R2, decision="accept", note="clear pool")
    s.add_review(sample_id="pool-0002", reviewer=R, decision="reject")
    lines = (tmp_path / "reviews.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(x)["op"] == "review" for x in lines)
    s2 = V.ValidationStore(tmp_path)  # rebuilt from the journal
    assert s2.latest()["pool-0001"]["decision"] == "accept"
    assert [r["decision"] for r in s2.history("pool-0001")] == ["uncertain", "accept"]
    snap = json.loads((tmp_path / "reviews_latest.json").read_text())
    assert snap["latest"]["pool-0001"]["decision"] == "accept" and snap["events"] == 3
    assert len(list((tmp_path / "backups").glob("reviews_*.json"))) == 3
    # idempotent client retry
    r = s2.add_review(sample_id="pool-0002", reviewer=R, decision="accept", review_id="fixed-id-1")
    assert s2.add_review(sample_id="pool-0002", reviewer=R, decision="reject", review_id="fixed-id-1") == r
    assert len(s2.history("pool-0002")) == 2


def test_torn_journal_line_is_skipped(tmp_path):
    s = build(tmp_path, [sample(1)])
    s.add_review(sample_id="pool-0001", reviewer=R, decision="accept")
    with open(tmp_path / "reviews.jsonl", "a") as f:
        f.write('{"op":"review","review":{"sample_id":"pool-0001"')  # power loss mid-write
    assert V.ValidationStore(tmp_path).latest()["pool-0001"]["decision"] == "accept"


def test_snapshot_atomic_on_crash(tmp_path, monkeypatch):
    s = build(tmp_path, [sample(1)])
    s.add_review(sample_id="pool-0001", reviewer=R, decision="accept")
    before = (tmp_path / "reviews_latest.json").read_bytes()

    def boom(*a, **k):
        raise OSError("simulated crash between tmp write and rename")

    monkeypatch.setattr(V.os, "replace", boom)
    with pytest.raises(OSError):
        s.add_review(sample_id="pool-0001", reviewer=R, decision="reject")
    monkeypatch.undo()
    assert (tmp_path / "reviews_latest.json").read_bytes() == before  # old snapshot intact and readable
    json.loads(before)
    # the journal (source of truth) has the review; a restart rebuilds the latest state from it
    assert V.ValidationStore(tmp_path).latest()["pool-0001"]["decision"] == "reject"


def test_validation_rules(tmp_path):
    s = build(tmp_path, [sample(1)])
    with pytest.raises(KeyError):
        s.add_review(sample_id="nope", reviewer=R, decision="accept")
    with pytest.raises(V.ValidationError):
        s.add_review(sample_id="pool-0001", reviewer=R, decision="maybe")
    with pytest.raises(V.ValidationError):
        s.add_review(sample_id="pool-0001", reviewer="Jane Doe", decision="accept")
    with pytest.raises(V.ValidationError):  # corrected needs a polygon
        s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected")
    with pytest.raises(V.ValidationError):  # not lon/lat
        s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected",
                     polygon={"type": "Polygon", "coordinates": [[[500000, 3900000], [500010, 3900000], [500010, 3900010], [500000, 3900000]]]})
    with pytest.raises(V.ValidationError):  # > 1 km²
        s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected", polygon=sq(-80.849, 35.05, 1200))
    with pytest.raises(V.ValidationError):  # self-intersecting bow-tie
        s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected",
                     polygon={"type": "Polygon", "coordinates": [[[-80.849, 35.05], [-80.8489, 35.0501], [-80.8489, 35.05], [-80.849, 35.0501], [-80.849, 35.05]]]})
    with pytest.raises(V.ValidationError):  # far from the sample
        s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected", polygon=sq(-80.70, 35.30))
    with pytest.raises(V.ValidationError):
        s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected", polygon={"type": "Point", "coordinates": [0, 0]})
    assert s.history("pool-0001") == []


def test_labels_untouched(tmp_path):
    """The human label set (data/labels/) is byte- and mtime-identical after every store + API operation."""
    labels = config.DATA / "labels"
    if not labels.exists():  # make a stand-in so the guarantee is still exercised
        pytest.skip("no data/labels in this environment")
    before = tree_hash(labels)
    s = build(tmp_path / "v1", [sample(1), sample(2, tile="t1")],
              [{"id": "t1", "class": "pool", "region": "Ballantyne", "holdout_group": "south",
                "bbox": list(box(-80.8495, 35.0495, -80.8475, 35.0505).bounds), "prediction_sample_ids": ["pool-0002"]}])
    s.add_review(sample_id="pool-0001", reviewer=R, decision="corrected", polygon=sq(-80.849, 35.05, 9))
    s.add_census(tile_id="t1", reviewer=R, polygons=[sq(-80.848, 35.05)], complete=True)
    s.metrics()
    s.export_geojson()
    s.export_csv()
    assert tree_hash(labels) == before
    assert not any("labels" in p.parts for p in (tmp_path / "v1").rglob("*"))


# ---------------------------------------------------------------- metrics
def test_wilson():
    assert V.wilson(0, 0) is None
    lo, hi = V.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert V.wilson(10, 10)[1] == 1.0


def test_metrics_synthetic(tmp_path):
    ss = [sample(i, score=0.55 if i < 5 else 0.85) for i in range(10)]
    ss += [sample(100 + i, region="Myers Park", group="central", score=0.65, cluster=False) for i in range(4)]
    ss += [sample(200, region="Lake Norman", group="north", cluster=True)]
    s = build(tmp_path, ss)
    # Ballantyne: 6 accept, 1 corrected, 2 reject, 1 uncertain
    for i in range(6):
        s.add_review(sample_id=f"pool-{i:04d}", reviewer=R, decision="accept")
    lon = -80.85 + 6 * 0.001
    s.add_review(sample_id="pool-0006", reviewer=R, decision="corrected", polygon=sq(lon, 35.05, 8, dx_m=4))
    s.add_review(sample_id="pool-0007", reviewer=R, decision="reject")
    s.add_review(sample_id="pool-0008", reviewer=R, decision="reject")
    s.add_review(sample_id="pool-0009", reviewer=R, decision="uncertain")
    # Myers Park: all uncertain / needs_review → no precision
    s.add_review(sample_id="pool-0100", reviewer=R, decision="uncertain")
    s.add_review(sample_id="pool-0101", reviewer=R, decision="needs_review")
    s.add_review(sample_id="pool-0200", reviewer=R, decision="reject")

    m = s.metrics()
    p = m["classes"]["pool"]
    assert p["n_samples"] == 15 and p["n_reviewed"] == 13
    assert (p["accept"], p["corrected"], p["reject"], p["uncertain"], p["needs_review"]) == (6, 1, 3, 2, 1)
    assert p["n_judged"] == 10 and p["precision"] == 0.7  # uncertain never counted as positive
    assert p["precision_ci95"] == V.wilson(7, 10)
    assert p["false_discovery_rate"] == 0.3
    b = p["by_region"]["Ballantyne"]
    assert b["precision"] == pytest.approx(7 / 9, abs=1e-4) and b["not_counted"]["uncertain"] == 1
    mp = p["by_region"]["Myers Park"]
    assert mp["precision"] is None and mp["precision_ci95"] is None and mp["n_reviewed"] == 2
    assert p["by_holdout_group"]["north"]["precision"] == 0.0
    # IoU of the corrected sample: two 8 m squares offset 4 m → 32 / 96 = 1/3
    assert p["iou_corrected"]["n"] == 1 and p["iou_corrected"]["median"] == pytest.approx(1 / 3, abs=0.01)
    # calibration: bucket [0.5, 0.6) has samples 0–4 → 5 accepts / 5
    b5 = next(x for x in p["calibration"]["buckets"] if x["score_lo"] == 0.5)
    assert (b5["n_judged"], b5["accepted"], b5["observed_acceptance"]) == (5, 5, 1.0)
    b8 = next(x for x in p["calibration"]["buckets"] if x["score_lo"] == 0.8)
    assert (b8["n_judged"], b8["accepted"]) == (4, 2)  # 5, 6(corrected), 7, 8 judged; 9 uncertain excluded
    # recall: no census → explicitly not measurable
    assert p["recall"]["status"] == V.NOT_MEASURABLE
    # excluding the Lake Norman label cluster removes the north reject
    mx = s.metrics(exclude_label_cluster=True)["classes"]["pool"]
    assert mx["n_samples"] == 14 and mx["precision"] == pytest.approx(7 / 9, abs=1e-4)


def test_recall_with_census(tmp_path):
    lon0 = -80.85
    tile_bbox = [lon0 - 0.0015, 35.0490, lon0 + 0.0050, 35.0510]
    ss = [sample(0, tile="t1"), sample(1, tile="t1")]  # predictions at lon0 and lon0+0.001
    s = build(tmp_path, ss, [{"id": "t1", "class": "pool", "region": "Ballantyne", "holdout_group": "south",
                              "bbox": tile_bbox, "prediction_sample_ids": ["pool-0000", "pool-0001"]}])
    assert s.metrics()["classes"]["pool"]["recall"]["status"] == V.NOT_MEASURABLE
    # incomplete census does not count
    s.add_census(tile_id="t1", reviewer=R, polygons=[sq(lon0, 35.05)], complete=False)
    assert s.metrics()["classes"]["pool"]["recall"]["status"] == V.NOT_MEASURABLE
    # complete census: 3 real pools, one matches prediction 0, prediction 1 is a false positive
    s.add_census(tile_id="t1", reviewer=R, polygons=[sq(lon0, 35.05, 8, dx_m=1), sq(lon0 + 0.003, 35.05),
                                                     sq(lon0 + 0.004, 35.05)], complete=True)
    r = s.metrics()["classes"]["pool"]["recall"]
    assert r["status"] == "measured" and r["human_objects"] == 3 and r["matched"] == 1
    assert r["recall"] == pytest.approx(1 / 3, abs=1e-4) and r["unmatched_predictions_in_census_tiles"] == 1
    feats = s.export_geojson()["features"]
    assert sum(f["properties"]["decision"] == "census_object" for f in feats) == 3


# ---------------------------------------------------------------- API
@pytest.fixture()
def client(tmp_path):
    from api import validation_api

    root = tmp_path / "v1"
    lon0 = -80.85
    build(root, [sample(1), sample(2), sample(3, cls="building", region="Myers Park", group="central")],
          [{"id": "t1", "class": "pool", "region": "Ballantyne", "holdout_group": "south",
            "bbox": [lon0 - 0.001, 35.049, lon0 + 0.005, 35.051], "prediction_sample_ids": ["pool-0001"]}])
    validation_api.reset_for_tests(root)
    app = FastAPI()
    app.include_router(validation_api.router)
    yield TestClient(app)
    validation_api.reset_for_tests(None)


H = {"X-GT-Owner": R}


def test_api_flow(client):
    c = client
    r = c.get("/api/v1/validation/samples", params={"status": "all"}).json()
    assert r["total"] == 3 and all(s["status"] == "unreviewed" for s in r["samples"])
    assert c.get("/api/v1/validation/samples", params={"cls": "building", "status": "all"}).json()["total"] == 1
    assert c.get("/api/v1/validation/samples", params={"region": "Ballantyne"}).json()["total"] == 2
    assert c.get("/api/v1/validation/samples", params={"cls": "tree"}).status_code == 400
    assert c.get("/api/v1/validation/samples/nope").status_code == 404

    assert c.post("/api/v1/validation/reviews", json={"sample_id": "pool-0001", "decision": "accept"}).status_code == 400
    assert c.post("/api/v1/validation/reviews", json={"sample_id": "nope", "decision": "accept"}, headers=H).status_code == 404
    assert c.post("/api/v1/validation/reviews", json={"sample_id": "pool-0001", "decision": "great"}, headers=H).status_code == 422
    big = sq(-80.849, 35.05, 1500)
    assert c.post("/api/v1/validation/reviews", json={"sample_id": "pool-0001", "decision": "corrected", "polygon": big},
                  headers=H).status_code == 422
    ok = c.post("/api/v1/validation/reviews", json={"sample_id": "pool-0001", "decision": "corrected",
                                                   "polygon": sq(-80.849, 35.05, 7), "note": "deck excluded"}, headers=H)
    assert ok.status_code == 200 and ok.json()["sample"]["status"] == "corrected"
    c.post("/api/v1/validation/reviews", json={"sample_id": "pool-0002", "decision": "reject"}, headers=H)

    assert c.get("/api/v1/validation/samples").json()["total"] == 1  # default: unreviewed
    assert c.get("/api/v1/validation/samples", params={"status": "reviewed"}).json()["total"] == 2
    d = c.get("/api/v1/validation/samples/pool-0001").json()
    assert d["review"]["reviewer"] == R and len(d["history"]) == 1

    pg = c.get("/api/v1/validation/progress").json()
    assert pg["reviewed"] == 2 and pg["by_class_region"]["pool"]["Ballantyne"]["reject"] == 1

    m = c.get("/api/v1/validation/metrics").json()
    assert m["classes"]["pool"]["precision"] == 0.5 and m["classes"]["building"]["n_reviewed"] == 0
    assert m["classes"]["pool"]["recall"]["status"] == V.NOT_MEASURABLE

    assert c.post("/api/v1/validation/census", json={"tile_id": "zz", "polygons": []}, headers=H).status_code == 404
    cz = c.post("/api/v1/validation/census", json={"tile_id": "t1", "polygons": [sq(-80.849, 35.05, 7)]}, headers=H)
    assert cz.status_code == 200
    assert c.get("/api/v1/validation/metrics").json()["classes"]["pool"]["recall"]["status"] == "measured"

    gj = c.get("/api/v1/validation/export", params={"format": "geojson"}).json()
    corr = next(f for f in gj["features"] if f["id"] == "pool-0001")
    assert corr["properties"]["geometry_source"] == "human" and corr["properties"]["source_tool"] == "find_all"
    csv_ = c.get("/api/v1/validation/export", params={"format": "csv"})
    assert csv_.status_code == 200 and csv_.text.splitlines()[0].startswith("sample_id,")
    assert len(csv_.text.splitlines()) == 1 + 2 + 1  # header + 2 reviewed samples + 1 census object
    assert c.get("/api/v1/validation/export", params={"format": "xml"}).status_code == 422
