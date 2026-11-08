"""Display mosaic (scripts/heavy/build_mosaic.py + PublicDataProvider.render_tile_mosaic) on small synthetic rasters.

Two scenes of the SAME ground overlap by 300 m; scene B is 16 DN brighter (a flight-to-flight brightness step). The
raw composite shows the step at B's edge; the mosaic must remove it at low frequency only, leave pixels far from the
seam untouched, keep the CRS, introduce no nodata and be deterministic, and record per-block provenance."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bm():
    spec = importlib.util.spec_from_file_location("build_mosaic", ROOT / "scripts" / "heavy" / "build_mosaic.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_mosaic"] = mod
    spec.loader.exec_module(mod)
    return mod


GSD = 1.2
BLOCK_PX = 512
BM = BLOCK_PX * GSD            # 614.4 m
X0, Y0 = 800 * BM, 6400 * BM   # block-aligned origin inside UTM 17N
H_M = BM                       # scenes are one block tall
A_X = (X0, X0 + 2400.0)
B_X = (X0 + 2100.0, X0 + 4500.0)
STEP = 16.0
SA, SB = "nc_m_test_a_17_060_20220602", "nc_m_test_b_17_060_20220927"
PRM = {"gsd": GSD, "block_px": BLOCK_PX, "win": 256, "fit_gsd": 4.8, "feather_px": 24, "edge_m": 20.0,
       "min_pair_px": 200, "global_colour": "none", "dodge": True, "sig_l_m": 60.0, "sig_e_m": 120.0,
       "tau_m": 300.0, "cmax": 30.0, "diff_clip": 30.0, "field_f": 2, "edge_blend_m": 0.0}


def ground(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Shared ground (3, h, w) on the global 1.2 m grid: smooth texture + bright 'roofs'. Seeded."""
    W = int(round((B_X[1] - A_X[0]) / GSD)); H = int(round(H_M / GSD))
    rng = np.random.default_rng(42)
    base = ndimage.gaussian_filter(rng.normal(0, 25, (3, H, W)), (0, 2, 2)) + np.array([90, 100, 80])[:, None, None]
    for k in range(40):  # roofs / pools: 12 x 12 m squares
        r, c = rng.integers(0, H - 10), rng.integers(0, W - 10)
        base[:, r:r + 10, c:c + 10] = rng.integers(150, 220, 3)[:, None, None]
    c0 = np.rint((xs[0] - A_X[0]) / GSD - 0.5).astype(int); r0 = np.rint((Y0 + H_M - ys[0]) / GSD - 0.5).astype(int)
    return np.clip(base[:, r0:r0 + len(ys), c0:c0 + len(xs)], 1, 230)


def write_scene(path: Path, x0: float, x1: float, add: float) -> None:
    w = int(round((x1 - x0) / GSD)); h = int(round(H_M / GSD))
    xs = x0 + (np.arange(w) + 0.5) * GSD; ys = Y0 + H_M - (np.arange(h) + 0.5) * GSD
    rgb = np.clip(np.rint(ground(xs, ys) + add), 1, 255).astype(np.uint8)
    nir = np.full((1, h, w), 120, np.uint8)
    prof = {"driver": "GTiff", "width": w, "height": h, "count": 4, "dtype": "uint8", "crs": "EPSG:32617",
            "transform": from_origin(x0, Y0 + H_M, GSD, GSD), "nodata": 0, "tiled": True,
            "blockxsize": 256, "blockysize": 256}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(np.concatenate([rgb, nir]))


@pytest.fixture(scope="module")
def setup(bm, tmp_path_factory):
    d = tmp_path_factory.mktemp("mosaic")
    paths = {SA: d / f"{SA}.tif", SB: d / f"{SB}.tif"}
    write_scene(paths[SA], *A_X, 0.0)
    write_scene(paths[SB], *B_X, STEP)
    bounds = {SA: (A_X[0], Y0, A_X[1], Y0 + H_M), SB: (B_X[0], Y0, B_X[1], Y0 + H_M)}
    scenes = [SB, SA]  # provider priority: newest first (B is drawn over A in the raw composite)
    lr = {s: bm.read_lowres(paths[s], bounds[s], PRM["fit_gsd"]) for s in scenes}
    f = bm.fit(lr, sorted(scenes), SA, PRM)
    fields, stats = bm.dodge_fields(lr, f["luts"], sorted(scenes), PRM)
    blocks = []
    for i in range(800, 808):
        bx0, bx1 = i * BM, (i + 1) * BM
        sids = [s for s in scenes if bounds[s][0] < bx1 and bounds[s][2] > bx0]
        if sids:
            blocks.append({"i": i, "j": 6400, "bounds_m": [bx0, Y0, bx1, Y0 + BM], "scenes": sids})
    dates = {s: bm.scene_date(s) for s in scenes}
    return {"dir": d, "paths": paths, "lr": lr, "fit": f, "fields": fields, "stats": stats, "blocks": blocks,
            "dates": dates}


def init(bm, s, out: Path, **over):
    prm = {**PRM, **over}
    bm.init_globals(year=2022, out_dir=out, tmp_dir=s["dir"], paths={k: str(v) for k, v in s["paths"].items()},
                    lr=s["lr"], luts=s["fit"]["luts"], fields=s["fields"], dates=s["dates"], prm=prm,
                    cells=over.get("cells"))


def strip(bm, s, mode: str) -> np.ndarray:
    """Whole synthetic strip (3, H, W) float rendered in `mode`, without JPEG (iter_block output)."""
    cols = []
    for blk in s["blocks"]:
        arr = np.zeros((3, BLOCK_PX, BLOCK_PX), np.float32)
        for win, px, valid, _ in bm.iter_block(blk, mode):
            arr[:, win.row_off:win.row_off + win.height, win.col_off:win.col_off + win.width] = px
        cols.append(arr)
    return np.concatenate(cols, axis=2)


def offset_profile(img: np.ndarray) -> np.ndarray:
    """Per-column mean brightness offset vs scene A's ground (A = ground, B = ground + STEP)."""
    W = img.shape[2]
    xs = X0 + (np.arange(W) + 0.5) * GSD
    ys = Y0 + H_M - (np.arange(BLOCK_PX) + 0.5) * GSD
    n = int(round((B_X[1] - A_X[0]) / GSD))
    g = ground(xs[:n], ys)
    return (img[:, :, :n] - g).mean((0, 1))


def max_step(prof: np.ndarray, base_px: int = 25) -> float:
    """Largest change of the (5 px smoothed) offset profile over a 30 m baseline."""
    p = ndimage.uniform_filter1d(prof, 5)
    return float(np.abs(p[base_px:] - p[:-base_px]).max())


def test_seam_step_reduced_and_interior_unchanged(bm, setup, tmp_path):
    init(bm, setup, tmp_path)
    raw, full = strip(bm, setup, "raw"), strip(bm, setup, "full")
    pr, pf = offset_profile(raw), offset_profile(full)
    assert max_step(pr) > 0.8 * STEP                 # the raw composite has the hard step
    assert max_step(pf) < 0.15 * STEP                # the mosaic spreads it into a slow ramp
    # pixels far (> 1.2 km = 4 tau) from the overlap are untouched (no global gain, dodge tapered away)
    xs = X0 + (np.arange(raw.shape[2]) + 0.5) * GSD
    far = (xs < B_X[0] - 1200) | ((xs > A_X[1] + 1200) & (xs < B_X[1]))
    assert far.sum() > 1000
    d = np.abs(full[:, :, far] - raw[:, :, far])
    assert d.max() <= 1.0 and (d == 0).mean() > 0.99
    # the correction is low-frequency: roofs keep their contrast against their surroundings near the seam
    near = (xs > B_X[0] - 300) & (xs < A_X[1] + 300)
    hp = lambda a: a - ndimage.uniform_filter(a, (1, 15, 15))  # noqa: E731
    assert np.abs(hp(full)[:, 20:-20, near] - hp(raw)[:, 20:-20, near]).mean() < 1.5
    # the dodge never moves a pixel by more than its clamp
    assert np.abs(full - raw).max() <= PRM["cmax"] + STEP + 1


def test_no_nodata_in_overlap_and_crs(bm, setup, tmp_path):
    init(bm, setup, tmp_path)
    recs = [bm.render_block(b) for b in setup["blocks"]]
    for r in recs:
        assert r["file"]
        with rasterio.open(tmp_path / r["file"]) as src:
            assert src.crs.to_epsg() == 32617
            assert src.transform.a == pytest.approx(GSD) and src.transform.c == pytest.approx(r["i"] * BM)
            m = src.dataset_mask()
            arr = src.read()
            xs = r["i"] * BM + (np.arange(BLOCK_PX) + 0.5) * GSD
            inside = (xs > A_X[0]) & (xs < B_X[1])
            assert (m[:, inside] == 255).all()                 # nothing masked where any scene has data
            assert (arr[:, :, inside].max(0) > 0).all()        # no black holes in the overlap or anywhere inside
            ov = (xs > B_X[0]) & (xs < A_X[1])
            if ov.any():
                assert (m[:, ov] == 255).all()


def test_deterministic(bm, setup, tmp_path):
    out = []
    for k in range(2):
        init(bm, setup, tmp_path / f"run{k}")
        blk = setup["blocks"][3]  # the overlap block
        r = bm.render_block(blk)
        with rasterio.open(tmp_path / f"run{k}" / r["file"]) as src:
            out.append((src.read(), src.dataset_mask(), r["sources"]))
    assert np.array_equal(out[0][0], out[1][0]) and np.array_equal(out[0][1], out[1][1])
    assert out[0][2] == out[1][2]
    # the low-res fit + dodge are deterministic too
    lr = {s: setup["lr"][s] for s in sorted(setup["lr"])}
    f2, _ = bm.dodge_fields(lr, setup["fit"]["luts"], sorted(lr), PRM)
    for s in f2:
        assert np.array_equal(f2[s]["arr"], setup["fields"][s]["arr"])


def test_provenance_per_block(bm, setup, tmp_path):
    init(bm, setup, tmp_path)
    recs = {r["i"]: r for r in (bm.render_block(b) for b in setup["blocks"])}
    ov_i = int(B_X[0] // BM)
    srcs = recs[ov_i]["sources"]
    assert {x["scene_id"] for x in srcs} == {SA, SB}
    assert {x["scene_id"]: x["date"] for x in srcs} == {SA: "2022-06-02", SB: "2022-09-27"}
    assert abs(sum(x["px_share"] for x in srcs) - 1.0) < 0.2 and all(x["px_share"] > 0 for x in srcs)
    assert [x["scene_id"] for x in recs[800]["sources"]] == [SA]
    assert json.loads(json.dumps(srcs)) == srcs  # serialisable as written to meta.json


def test_coverage_edge_matches_raw(bm, setup, tmp_path):
    """At the outer edge of the mosaic coverage the output fades to the raw composite (no step to fallback tiles)."""
    cells = {(b["i"], b["j"]) for b in setup["blocks"]}
    init(bm, setup, tmp_path, edge_blend_m=100.0, cells=cells)
    blk = next(b for b in setup["blocks"] if len(b["scenes"]) == 2)
    raw = next(px for win, px, _, _ in bm.iter_block(blk, "raw") if win.row_off == 0 and win.col_off == 0)
    full = next(px for win, px, _, _ in bm.iter_block(blk, "full") if win.row_off == 0 and win.col_off == 0)
    assert np.array_equal(full[:, 0], raw[:, 0])     # top row = edge of coverage (no block north)


def test_provider_mosaic_coverage_and_fallback(bm, setup, tmp_path, monkeypatch):
    """render_tile_mosaic serves tiles fully inside the blocks and returns None (-> raw) outside or at holes."""
    import morecantile

    from groundtruth.geo import to_wgs84
    from groundtruth.providers.public import PublicDataProvider
    from shapely.geometry import Point
    data = tmp_path / "data"
    out = data / "tiles" / "mosaic" / "vtest" / "2022"
    init(bm, setup, out)
    recs = [bm.render_block(b) for b in setup["blocks"]]
    meta = {"partial": False, "grid": {"block_m": BM}, "blocks": [{"i": r["i"], "j": r["j"], "file": r["file"]}
                                                                    for r in recs]}
    (out / "meta.json").write_text(json.dumps(meta))
    monkeypatch.setenv("GROUNDTRUTH_DATA_MOSAIC", "vtest")
    p = PublicDataProvider(data_dir=data, cog_cache=tmp_path / "cog")
    tms = morecantile.tms.get("WebMercatorQuad")
    lon, lat = to_wgs84(Point(X0 + 2250.0, Y0 + H_M / 2)).coords[0]   # inside the overlap
    t = tms.tile(lon, lat, 18)
    png = p.render_tile_mosaic(2022, 18, t.x, t.y)
    assert png and png[:4] == b"\x89PNG"
    lon, lat = to_wgs84(Point(X0 + 2250.0, Y0 + H_M + 500)).coords[0]  # north of every block
    t = tms.tile(lon, lat, 18)
    assert p.render_tile_mosaic(2022, 18, t.x, t.y) is None
    lon, lat = to_wgs84(Point(B_X[1] + 2.0, Y0 + H_M / 2)).coords[0]   # block exists but has no data there
    t = tms.tile(lon, lat, 18)
    assert p.render_tile_mosaic(2022, 18, t.x, t.y) is None
    monkeypatch.setenv("GROUNDTRUTH_DATA_MOSAIC", "vnone")               # no mosaic -> None
    assert PublicDataProvider(data_dir=data, cog_cache=tmp_path / "cog").render_tile_mosaic(2022, 18, t.x, t.y) is None


# ---------------------------------------------------------------- water seamlines inside one scene
WX = (X0 + 150.0, X0 + 1050.0)      # lake, full strip height
SEAM_X = X0 + 600.0                 # orthomosaic seamline: east half of the lake is WSTEP brighter
WSTEP = 6.0
SW = "nc_m_test_w_17_060_20220602"


def write_water_scene(path: Path) -> None:
    x0, x1 = X0, X0 + 1228.8
    w = int(round((x1 - x0) / GSD)); h = int(round(H_M / GSD))
    xs = x0 + (np.arange(w) + 0.5) * GSD
    rng = np.random.default_rng(3)
    lake = (xs > WX[0]) & (xs < WX[1])
    land = ground(xs, Y0 + H_M - (np.arange(h) + 0.5) * GSD)
    water = 60 + ndimage.gaussian_filter(rng.normal(0, 2, (3, h, w)), (0, 1, 1)) + WSTEP * (xs > SEAM_X)
    rgb = np.where(lake[None, None, :], water, land)
    nir = np.where(lake[None, :], 10, 120) * np.ones((h, 1))
    for k in range(6):  # docks: bright, high NIR, inside the lake on both sides of the seam
        r, c = 60 + 70 * k, int((SEAM_X - 100 + 40 * k - x0) / GSD)
        rgb[:, r:r + 8, c:c + 30] = 210
        nir[r:r + 8, c:c + 30] = 190
    prof = {"driver": "GTiff", "width": w, "height": h, "count": 4, "dtype": "uint8", "crs": "EPSG:32617",
            "transform": from_origin(x0, Y0 + H_M, GSD, GSD), "nodata": 0, "tiled": True,
            "blockxsize": 256, "blockysize": 256}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(np.concatenate([np.clip(np.rint(rgb), 1, 255), nir[None]]).astype(np.uint8))


def test_water_seamline_removed_land_and_docks_untouched(bm, tmp_path):
    path = tmp_path / f"{SW}.tif"
    write_water_scene(path)
    prm = {**bm.PARAMS, **PRM, "water": True, "dodge": False}
    bounds = (X0, Y0, X0 + 1228.8, Y0 + H_M)
    lr = {SW: bm.read_lowres(path, bounds, prm["fit_gsd"])}
    f = bm.fit(lr, [SW], SW, prm)
    wf = bm.water_field(lr, [SW], bounds, prm)
    assert wf is not None and wf["stats"]["seam_lines"] >= 1
    seg = max(wf["segments"], key=lambda s: s["length_m"])
    assert abs(abs(seg["step_dn"]) - WSTEP) < 1.5
    assert abs((seg["xy0_m"][0] + seg["xy1_m"][0]) / 2 - SEAM_X) < 10      # found where it is
    bm.init_globals(year=2022, out_dir=tmp_path, tmp_dir=tmp_path, paths={SW: str(path)}, lr=lr, luts=f["luts"],
                    fields={}, dates={SW: "2022-06-02"}, prm=prm, water=wf)
    blocks = [{"i": i, "j": 6400, "bounds_m": [i * BM, Y0, (i + 1) * BM, Y0 + BM], "scenes": [SW]} for i in (800, 801)]
    raw = np.concatenate([_block(bm, b, "raw") for b in blocks], axis=2).astype(np.float32)
    full = np.concatenate([_block(bm, b, "full") for b in blocks], axis=2).astype(np.float32)
    with rasterio.open(path) as src:
        nir = src.read(4)
    W = min(raw.shape[2], nir.shape[1])
    raw, full, nir = raw[:, :, :W], full[:, :, :W], nir[:, :W]
    xs = X0 + (np.arange(W) + 0.5) * GSD
    water_rows = slice(40, -40)
    prof_r = raw[:, water_rows].mean((0, 1)); prof_f = full[:, water_rows].mean((0, 1))
    i0 = int((SEAM_X - X0) / GSD)
    step = lambda p: abs(p[i0 + 25] - p[i0 - 25])  # noqa: E731  (30 m each side)
    assert step(prof_r) > 0.8 * WSTEP * 0.9           # docks in some rows lower the mean step a little
    assert step(prof_f) < 0.15 * WSTEP              # sharp seam -> narrow ramp: step essentially gone
    # land and docks: bit-identical
    land_or_dock = nir >= 120
    assert land_or_dock.sum() > 10000
    assert np.array_equal(full[:, land_or_dock], raw[:, land_or_dock])
    # the correction is a smooth ramp: no water pixel changes by more than half the step + rounding
    assert np.abs(full - raw).max() <= WSTEP / 2 + 1.5


def _block(bm, blk, mode):
    arr = np.zeros((3, BLOCK_PX, BLOCK_PX), np.uint8)
    for win, px, _, _ in bm.iter_block(blk, mode):
        arr[:, win.row_off:win.row_off + win.height, win.col_off:win.col_off + win.width] = px
    return arr
