"""Community-infrastructure layers (scripts/build_community.py → data/community/<version>/).

Synthetic tests build the layers from a tiny hand-written .osm fixture (no real data needed) and exercise the
provider layer names + Engine.filter(within_m_of). Real-data tests run only when data/community/<version> exists."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import box, shape

from groundtruth import config
from groundtruth.providers.public import PublicDataProvider

ROOT = Path(__file__).resolve().parents[1]
FACILITY_KINDS = {"hospital", "clinic", "fire_station", "police", "ambulance_station", "school", "college",
                  "university", "shelter", "townhall", "community_centre", "library"}
WATER_KINDS = {"river", "stream", "canal", "drain", "ditch", "dam", "weir"}
ALLOWED_KEYS = {
    "facilities.geojson": {"osm_id", "kind", "name", "ref", "in_county", "area_m2", "has_emergency", "source",
                           "osm_timestamp"},
    "bridges.geojson": {"osm_id", "kind", "highway", "railway", "name", "ref", "length_m", "source", "osm_timestamp"},
    "waterways.geojson": {"osm_id", "kind", "name", "length_m", "area_m2", "source", "osm_timestamp"},
}
PEOPLE = ("phone", "email", "contact", "person", "owner", "resident", "demograph", "rating", "crime", "fax")
LAYERS = {"community_facilities", "community_hospitals", "community_fire_stations", "community_ems",
          "community_police", "community_schools", "community_shelters", "community_bridges", "community_waterways"}

FIRE = (-80.900, 35.470)  # synthetic fire station inside the Lake Norman demo bbox


def _bc():
    spec = importlib.util.spec_from_file_location("build_community", ROOT / "scripts" / "build_community.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _osm_fixture(p: Path) -> Path:
    def node(i, lon, lat, **tags):
        t = "".join(f'<tag k="{k.replace("__", ":")}" v="{v}"/>' for k, v in tags.items())
        return f'<node id="{i}" version="1" lat="{lat}" lon="{lon}">{t}</node>'

    def way(i, refs, **tags):
        nd = "".join(f'<nd ref="{r}"/>' for r in refs)
        t = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        return f'<way id="{i}" version="1">{nd}{t}</way>'

    nodes = [
        node(1, *FIRE, amenity="fire_station", name="Station 1", phone="555-0100", operator__person="Someone"),
        node(2, -80.880, 35.460, amenity="school", name="Lakeside School"),
        node(3, -80.870, 35.450, amenity="shelter", shelter_type="picnic_shelter"),  # NOT a community shelter
        node(4, -80.860, 35.455, power="substation"),  # critical utility: never extracted
        node(5, -80.890, 35.480, emergency="ambulance_station", name="EMS 9"),
        node(6, -80.500, 35.000, amenity="police", name="Far Police"),  # outside county + 10 km ring
        node(7, -80.885, 35.465, waterway="weir"),
        # hospital outline (closed way 100)
        node(10, -80.910, 35.490), node(11, -80.908, 35.490), node(12, -80.908, 35.492), node(13, -80.910, 35.492),
        # bridge (way 101), bridge=no (way 102), stream (way 103), far stream (way 104)
        node(20, -80.920, 35.440), node(21, -80.918, 35.441), node(22, -80.916, 35.442),
        node(30, -80.930, 35.450), node(31, -80.940, 35.455), node(32, -80.950, 35.460),
        node(40, -81.500, 35.900), node(41, -81.510, 35.910),
        # college multipolygon relation (outer way 105)
        node(50, -80.850, 35.500), node(51, -80.848, 35.500), node(52, -80.848, 35.502), node(53, -80.850, 35.502),
    ]
    ways = [
        way(100, [10, 11, 12, 13, 10], amenity="hospital", name="Lake Hospital", emergency="yes", email="x@y.z"),
        way(101, [20, 21], highway="primary", bridge="yes", name="Lake Bridge", ref="NC 73"),
        way(102, [21, 22], highway="primary", bridge="no"),
        way(103, [30, 31, 32], waterway="stream", name="Test Creek"),
        way(104, [40, 41], waterway="river", name="Far River"),
        way(105, [50, 51, 52, 53, 50]),
    ]
    rel = ('<relation id="200" version="1"><member type="way" ref="105" role="outer"/>'
           '<tag k="type" v="multipolygon"/><tag k="amenity" v="college"/><tag k="name" v="Lake College"/></relation>')
    p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">'
                 + "".join(nodes) + "".join(ways) + rel + "</osm>")
    return p


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("gtdata")
    (d / "naip").mkdir()
    (d / "naip" / "coverage.json").write_text(json.dumps({"full_years": [], "years": {}}))
    out = d / "community" / config.data_version("community")
    county = box(-81.0, 35.3, -80.7, 35.6)
    meta = _bc().build(_osm_fixture(d / "fixture.osm"), county, out, log=lambda *_: None)
    return d, out, meta


def _fc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_files(out: Path):
    for name, allowed in ALLOWED_KEYS.items():
        fc = _fc(out / name)
        assert fc["type"] == "FeatureCollection"
        ids = [f["properties"]["osm_id"] for f in fc["features"]]
        key = lambda s: ({"node": 0, "way": 1, "relation": 2}[s.split("/")[0]], int(s.split("/")[1]))  # noqa: E731
        assert ids == sorted(ids, key=key) and len(set(ids)) == len(ids)
        for f in fc["features"]:
            p = f["properties"]
            assert p["osm_id"].split("/")[0] in ("node", "way", "relation") and p["kind"]
            assert set(p) <= allowed, set(p) - allowed
            assert not [k for k in p if any(w in k.lower() for w in PEOPLE)]
            assert p["source"] == "OpenStreetMap"
            g = shape(f["geometry"])
            x0, y0, x1, y1 = g.bounds
            assert -180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90
            if name == "facilities.geojson":
                assert p["kind"] in FACILITY_KINDS and f["geometry"]["type"] == "Point"
            elif name == "bridges.geojson":
                assert p["kind"] == "bridge" and (p.get("highway") or p.get("railway")) and p["length_m"] > 0
            else:
                assert p["kind"] in WATER_KINDS
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"]["licence"] == "ODbL 1.0" and "OpenStreetMap" in meta["source"]["attribution"]


# ---------------------------------------------------------------- synthetic
def test_build_synthetic_contents(built):
    _d, out, meta = built
    _check_files(out)
    fac = {f["properties"]["osm_id"]: f["properties"] for f in _fc(out / "facilities.geojson")["features"]}
    assert set(fac) == {"node/1", "node/2", "node/5", "way/100", "relation/200"}
    assert fac["node/1"]["kind"] == "fire_station" and fac["node/1"]["name"] == "Station 1"
    assert fac["way/100"]["kind"] == "hospital" and fac["way/100"]["has_emergency"] is True
    assert 30000 < fac["way/100"]["area_m2"] < 50000  # ~181 m x 222 m
    assert fac["relation/200"]["kind"] == "college" and fac["relation/200"]["area_m2"] > 0
    assert fac["node/5"]["kind"] == "ambulance_station" and all(p["in_county"] for p in fac.values())
    br = _fc(out / "bridges.geojson")["features"]
    assert [f["properties"]["osm_id"] for f in br] == ["way/101"]
    assert br[0]["properties"]["ref"] == "NC 73" and 150 < br[0]["properties"]["length_m"] < 250
    wa = {f["properties"]["osm_id"]: f["properties"]["kind"] for f in _fc(out / "waterways.geojson")["features"]}
    assert wa == {"node/7": "weir", "way/103": "stream"}
    assert meta["files"]["facilities.geojson"]["counts_by_kind_in_county"]["fire_station"] == 1


def test_build_is_deterministic(built, tmp_path):
    d, out, _ = built
    out2 = tmp_path / "again"
    _bc().build(d / "fixture.osm", box(-81.0, 35.3, -80.7, 35.6), out2, log=lambda *_: None)
    for name in ALLOWED_KEYS:
        assert (out / name).read_bytes() == (out2 / name).read_bytes()


def test_layer_names_resolve(built):
    d, _out, _ = built
    prov = PublicDataProvider(d)
    assert LAYERS <= set(prov.layer_names())
    kinds = {"community_hospitals": {"hospital"}, "community_fire_stations": {"fire_station"},
             "community_ems": {"ambulance_station"}, "community_schools": {"school"}}
    for name, want in kinds.items():
        fc = prov.get_layer(name)
        assert {f["properties"]["kind"] for f in fc["features"]} == want
        assert prov.get_layer(name, (-81.0, 35.4, -80.8, 35.55))["features"]
    assert prov.get_layer("community_police")["features"] == []  # the only police node is outside the ring
    assert prov.get_layer("community_fire_stations", (-80.0, 35.0, -79.9, 35.1))["features"] == []
    assert len(prov.get_layer("community_facilities")["features"]) == 5
    assert prov.get_layer("community_bridges")["features"] and prov.get_layer("community_waterways")["features"]
    assert any("community" in s.name for s in prov.source_for("community"))
    with pytest.raises(KeyError):
        prov.get_layer("community_nope")


def test_existing_layer_names_unchanged(built):
    """A data/layers file with a community_* name still wins (existing resolution is untouched)."""
    d, _out, _ = built
    lay = d / "layers"
    lay.mkdir(exist_ok=True)
    (lay / "osm_test.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    prov = PublicDataProvider(d)
    assert prov.get_layer("osm_test")["features"] == []
    assert "osm_test" in prov.layer_names()


def test_within_m_of_community_layer(built):
    from groundtruth.tools import Engine

    d, _out, _ = built
    eng = Engine(PublicDataProvider(d), year=2022, index=object())
    dlat = 100 / 111_320  # ~100 m north of the fire station
    near = {"type": "Feature", "id": "near", "properties": {},
            "geometry": {"type": "Point", "coordinates": [FIRE[0], FIRE[1] + dlat]}}
    far = {"type": "Feature", "id": "far", "properties": {},
           "geometry": {"type": "Point", "coordinates": [FIRE[0] + 5000 / (111_320 * math.cos(math.radians(35.47))),
                                                         FIRE[1]]}}
    fc = {"type": "FeatureCollection", "features": [near, far]}
    env = eng.filter(fc, {"within_m_of": {"layer": "community_fire_stations", "m": 500}})
    assert env.status == "ok"
    assert [f["id"] for f in env.result.geojson["features"]] == ["near"]
    env = eng.filter(fc, {"within_m_of": {"layer": "community_ems", "m": 500}})
    assert env.result.geojson["features"] == []  # EMS 9 is ~1.2 km from both
    assert eng.filter(fc, {"within_m_of": {"layer": "community_nope", "m": 500}}).status == "declined"


# ---------------------------------------------------------------- real data (only when built)
REAL = config.data_dir("community")
real = pytest.mark.skipif(not (REAL / "facilities.geojson").exists(), reason=f"{REAL} not built")


@real
def test_real_files_valid():
    _check_files(REAL)
    meta = json.loads((REAL / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"]["osm_timestamp"].startswith("20")
    for name in ALLOWED_KEYS:
        assert all(f["properties"]["osm_timestamp"] == meta["source"]["osm_timestamp"]
                   for f in _fc(REAL / name)["features"])


@real
def test_real_layers_resolve():
    prov = PublicDataProvider(config.DATA)
    assert LAYERS <= set(prov.layer_names())
    assert prov.get_layer("community_fire_stations")["features"]
