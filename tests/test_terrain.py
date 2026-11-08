"""Terrain (engine/groundtruth/terrain.py, api/terrain_api.py, scripts/build_terrain.py outputs).

Real-data tests use the Lake Norman lidar (data/lidar/<v>/Lake_Norman) and terrain products (data/terrain/<v>/Lake_Norman)
and are skipped when those are not present. Unit tests of the measured-mask rule use synthetic arrays."""

from __future__ import annotations

import math

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from groundtruth import config, risk
from groundtruth import terrain as T
from groundtruth.geo import to_measure, transformer

PLACE = "Lake Norman"
LIDAR = config.data_dir("lidar") / "Lake_Norman"
TERRAIN = config.data_dir("terrain") / "Lake_Norman"
real_lidar = pytest.mark.skipif(not (LIDAR / "dtm.tif").exists(), reason=f"{LIDAR} not present")
real_terrain = pytest.mark.skipif(not (TERRAIN / "meta.json").exists(), reason=f"{TERRAIN} not built")

LAND_LINE = [(-80.8700, 35.4450), (-80.8640, 35.4470)]  # SE of the bbox: residential land, ~590 m
LAKE_LINE = [(-80.9450, 35.5000), (-80.9400, 35.5000)]  # open water in the NW of the bbox
OUT_LINE = [(-80.8700, 35.4450), (-80.8300, 35.4450)]   # runs east, out of the lidar raster


@pytest.fixture(autouse=True)
def _fresh_caches():
    risk._lidar_places.cache_clear()
    T._lidar_meta.cache_clear()
    yield
    risk._lidar_places.cache_clear()
    T._lidar_meta.cache_clear()


# ------------------------------------------------------------------ measured rule (synthetic)
def test_measured_mask_rejects_fill_and_keeps_ground():
    rng = np.random.default_rng(0)
    n = 80
    dtm = (200 + rng.normal(0, 0.05, (n, n))).round(2).astype(np.float32)  # textured ground at cm resolution
    dsm = dtm + np.where(rng.random((n, n)) < 0.7, rng.random((n, n)) * 0.3, 0).astype(np.float32)
    dtm[:, 40:] = 230.15  # filled lake: constant DTM ...
    dsm[:, 40:] = 230.2 + rng.normal(0, 0.03, (n, 40)).astype(np.float32)  # ... with water-surface returns
    ret = T.return_mask(dsm, dtm, 5)
    assert ret[:, 45:].all(), "water returns satisfy the return rule alone"
    m = T.measured_mask(dsm, dtm, 5)
    assert m[:, :30].mean() > 0.95
    assert not m[:, 50:].any(), "filled DTM must never count as measured"


def test_measured_mask_no_returns():
    dtm = np.full((30, 30), 230.0, np.float32)
    assert not T.measured_mask(dtm.copy(), dtm, 5).any()


# ------------------------------------------------------------------ profile (real lidar)
@real_lidar
def test_profile_land_line():
    p = T.profile(LAND_LINE, n=120)
    d = p["dist_m"]
    assert p["place"] == PLACE and len(d) == 120 and len(p["elev_m"]) == 120
    assert all(b > a for a, b in zip(d, d[1:])), "dist_m strictly increasing"
    expect = to_measure(LineString(LAND_LINE)).length
    assert abs(p["line_length_m"] - expect) < 0.01 and abs(d[-1] - expect) < 0.01
    have = [e for e in p["elev_m"] if e is not None]
    assert p["measured_share"] >= 0.8 and len(have) == round(p["measured_share"] * 120)
    assert all(200 <= e <= 260 for e in have)
    assert p["source"]["date"].startswith("2016") and "3DEP" in p["source"]["name"]
    for e, b in zip(p["elev_m"], p["basis"]):
        assert (e is None) == (b in ("no_return", "no_ground", "outside"))


@real_lidar
def test_profile_matches_dtm_at_measured_station():
    import rasterio

    p = T.profile(LAND_LINE, n=50)
    k = next(i for i, b in enumerate(p["basis"]) if b == "measured")
    xy = to_measure(LineString(LAND_LINE)).interpolate(p["dist_m"][k]).coords[0]
    with rasterio.open(LIDAR / "dtm.tif") as s:
        v = float(next(s.sample([xy]))[0])
    assert abs(p["elev_m"][k] - v) < 0.01


@real_lidar
def test_profile_lake_and_outside_are_none():
    lake = T.profile(LAKE_LINE, n=40)
    assert lake["place"] == PLACE
    assert all(e is None for e in lake["elev_m"]), "open water has no ground returns: never filled"
    assert set(lake["basis"]) <= {"no_return", "no_ground"} and lake["measured_share"] == 0.0
    out = T.profile(OUT_LINE, n=60)
    assert out["basis"][-1] == "outside" and out["elev_m"][-1] is None
    assert out["basis"][0] != "outside"
    assert 0 < out["measured_share"] < 1 and out["status"] == "partial"


def test_profile_no_place_and_bad_input():
    p = T.profile([(-79.0, 36.0), (-79.001, 36.001)], n=5)  # far outside every lidar place
    assert p["status"] == "unavailable" and all(e is None for e in p["elev_m"])
    with pytest.raises(ValueError):
        T.profile([(-80.9, 35.4)])
    with pytest.raises(ValueError):
        T.profile([(35.4, -200.0), (35.5, -80.9)])


# ------------------------------------------------------------------ terrain-RGB tiles (real products)
def _decode(px):
    r, g, b = (float(v) for v in px[:3])
    return -10000.0 + (r * 65536 + g * 256 + b) * 0.1


@real_lidar
@real_terrain
def test_terrain_rgb_matches_dtm_at_measured_cells():
    import rasterio
    from PIL import Image
    from rasterio.windows import Window

    meta = T.meta(PLACE)
    z = meta["products"]["terrain_rgb"]["maxzoom"]
    pz = meta["products"]["terrain_rgb"]["per_zoom"][str(z)]
    to_ll = transformer("EPSG:3857", "EPSG:4326")
    to_utm = transformer("EPSG:4326", "EPSG:32617")
    span = 2 * math.pi * 6378137.0 / 2 ** z
    px = span / 256
    R = int(round(T.coverage_radius_m()))
    P = T.pad_px(R) + 3
    hits = 0
    with rasterio.open(LIDAR / "dtm.tif") as s_dtm, rasterio.open(LIDAR / "dsm.tif") as s_dsm:
        tr = s_dtm.transform
        tx, ty = (pz["x"][0] + pz["x"][1]) // 2, (pz["y"][0] + pz["y"][1]) // 2 + 1  # an interior (land) tile
        img = np.asarray(Image.open(TERRAIN / "terrain_rgb" / str(z) / str(tx) / f"{ty}.png"))
        x0, y1 = -math.pi * 6378137.0 + tx * span, math.pi * 6378137.0 - ty * span
        for r in range(8, 256, 24):
            for c in range(8, 256, 24):
                mx, my = x0 + (c + 0.5) * px, y1 - (r + 0.5) * px
                lon, lat = to_ll.transform(mx, my)
                ux, uy = to_utm.transform(lon, lat)
                half = px * math.cos(math.radians(lat)) / 2  # ground half-size of the tile pixel
                col, row = (ux - tr.c) / tr.a, (uy - tr.f) / tr.e
                c0, r0 = int(math.floor(col - half)), int(math.floor(row - half))
                c1, r1 = int(math.ceil(col + half)), int(math.ceil(row + half))
                if c0 - P < 0 or r0 - P < 0 or c1 + P > s_dtm.width or r1 + P > s_dtm.height:
                    continue
                win = Window(c0 - P, r0 - P, c1 - c0 + 2 * P, r1 - r0 + 2 * P)
                dtm, dsm = s_dtm.read(1, window=win), s_dsm.read(1, window=win)
                m = T.measured_mask(dsm, dtm, R)[P:-P, P:-P]
                cells = dtm[P:-P, P:-P]
                if not m.all() or float(cells.std()) > 0.15:  # need a fully measured, gentle footprint
                    continue
                assert abs(_decode(img[r, c]) - float(cells.mean())) <= 0.2, (tx, ty, r, c)
                hits += 1
    assert hits >= 10, f"only {hits} measured flat pixels sampled"


@real_terrain
def test_meta_records_fill_and_datum():
    m = T.meta(PLACE)
    t = m["products"]["terrain_rgb"]
    assert m["status"] == "ok" and "NAVD88" in m["vertical_datum"]
    assert t["fill"]["visual_only"] is True and 0 < t["filled_share_in_bbox"] < 1
    assert len(m["products"]["hillshade"]["corners_lonlat"]) == 4
    assert m["products"]["elevation_tint"]["legend"]["min_m"] < m["products"]["elevation_tint"]["legend"]["max_m"]
    assert T.meta("Nowhere")["status"] == "unavailable"


# ------------------------------------------------------------------ API
@pytest.fixture()
def client(monkeypatch):
    from api import auth, terrain_api

    monkeypatch.setenv("GROUNDTRUTH_AUTH", "off")
    app = FastAPI()
    app.middleware("http")(auth.middleware)
    app.include_router(auth.router)
    app.include_router(terrain_api.router)
    return TestClient(app)


@real_terrain
def test_api_routes(client):
    assert client.get("/api/v1/terrain/Lake_Norman/meta").json()["status"] == "ok"
    assert client.get("/api/v1/terrain/Lake%20Norman/meta").json()["status"] == "ok"
    assert client.get("/api/v1/terrain/Atlantis/meta").json()["status"] == "unavailable"
    for name in ("hillshade", "elevation_tint"):
        r = client.get(f"/api/v1/terrain/Lake_Norman/{name}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert client.get("/api/v1/terrain/Lake_Norman/meta.png").status_code == 404
    pz = T.meta(PLACE)["products"]["terrain_rgb"]["per_zoom"]["15"]
    r = client.get(f"/api/v1/terrain/Lake_Norman/rgb/15/{pz['x'][0]}/{pz['y'][0]}.png")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    assert client.get("/api/v1/terrain/Lake_Norman/rgb/15/0/0.png").status_code == 404
    assert client.get("/api/v1/terrain/Lake_Norman/rgb/3/99/0.png").status_code == 404


def test_api_rejects_path_traversal(client):
    for url in ("/api/v1/terrain/..%2F..%2Flidar/meta", "/api/v1/terrain/%2e%2e/meta",
                "/api/v1/terrain/%2e%2e/hillshade.png", "/api/v1/terrain/Lake_Norman/..%2Fmeta.png",
                "/api/v1/terrain/Lake_Norman/..%2F..%2Fv1%2FLake_Norman%2Fhillshade.png",
                "/api/v1/terrain/..%2Flidar%2Fv2%2FLake_Norman/rgb/15/9014/12921.png",
                "/api/v1/terrain/..%5C..%5Clabels/meta"):
        r = client.get(url)
        assert r.status_code == 404, url


@real_lidar
def test_api_profile(client):
    r = client.post("/api/v1/terrain/profile", json={"line": [list(p) for p in LAND_LINE], "n": 30})
    assert r.status_code == 200 and len(r.json()["elev_m"]) == 30
    assert client.post("/api/v1/terrain/profile", json={"line": [[-80.9, 35.4]]}).status_code == 422
    assert client.post("/api/v1/terrain/profile", json={"line": [[1, 2, 3], [4, 5, 6]]}).status_code == 422
    assert client.post("/api/v1/terrain/profile",
                       json={"line": [list(p) for p in LAND_LINE], "n": 999999}).status_code == 422


def test_api_requires_auth_when_on(monkeypatch):
    from api import auth, terrain_api

    monkeypatch.setenv("GROUNDTRUTH_AUTH", "on")
    monkeypatch.delenv("GROUNDTRUTH_API_TOKEN", raising=False)
    app = FastAPI()
    app.middleware("http")(auth.middleware)
    app.include_router(terrain_api.router)
    c = TestClient(app)
    assert c.get("/api/v1/terrain/Lake_Norman/rgb/15/9014/12921.png").status_code == 401
    assert c.post("/api/v1/terrain/profile", json={"line": [[0, 0], [0, 1]]}).status_code == 401
