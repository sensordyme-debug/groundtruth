"""Canopy + slope rasters (scripts/build_canopy.py) and their use in risk.canopy_facts / slope_facts. Synthetic data only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box, mapping

from groundtruth import config, risk
from groundtruth.geo import to_wgs84

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bc():
    spec = importlib.util.spec_from_file_location("build_canopy", ROOT / "scripts" / "build_canopy.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _scene(n=60):
    """60×60 m at 1 m: a 10 m tree blob (disk r=5 at (15, 30)) and a 10 m building (10×10 at cols 40-49)."""
    yy, xx = np.mgrid[0:n, 0:n]
    dtm = np.full((n, n), 200.0, np.float32) + 0.001 * xx  # tiny tilt so ground cells differ from fill
    tree = (xx - 15) ** 2 + (yy - 30) ** 2 <= 25
    bld = (xx >= 40) & (xx < 50) & (yy >= 25) & (yy < 35)
    ndsm = np.zeros((n, n), np.float32)
    ndsm[tree] = 10.0
    ndsm[bld] = 10.0
    dsm = dtm + ndsm + 0.05  # every cell has a real return
    return dsm, dtm, ndsm, tree, bld


def test_tree_is_canopy_building_is_not(bc):
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    dsm, dtm, ndsm, tree, bld = _scene()
    cov = bc.coverage_mask(dsm, dtm, 5)
    poly = box(40, -35, 50, -25).buffer(1.0)  # building footprint in grid metres (y down → negative), +1 m
    bmask = rasterize([poly], out_shape=ndsm.shape, transform=from_origin(0, 0, 1, 1)) > 0
    can = bc.canopy_cells(ndsm, cov, bmask, 3.0)
    assert (can[tree] == 1).all()
    assert (can[bld] == 0).all()
    assert (can[~tree] != 1).all()


def test_ndvi_second_signal_removes_tall_non_vegetation(bc):
    dsm, dtm, ndsm, tree, bld = _scene()
    cov = bc.coverage_mask(dsm, dtm, 5)
    red = np.full(ndsm.shape, 100, np.uint8)
    nir = np.where(tree, 200, 100).astype(np.uint8)  # NDVI 0.33 on the tree, 0 elsewhere
    valid = np.ones(ndsm.shape, bool)
    veg = bc.vegetation_near(bc.ndvi(red, nir), valid, 0.2, 1)
    can = bc.canopy_cells(ndsm, cov, np.zeros(ndsm.shape, bool), 3.0, veg, valid)  # building NOT masked by footprint
    assert (can[tree] == 1).all()
    assert (can[bld] == 0).all()  # tall but not green → not canopy
    # where NAIP is invalid the NDVI test is skipped (lidar only), never turned into canopy=0 silently
    can2 = bc.canopy_cells(ndsm, cov, np.zeros(ndsm.shape, bool), 3.0, veg, np.zeros(ndsm.shape, bool))
    assert (can2[bld] == 1).all()


def test_nodata_preserved(bc):
    dsm, dtm, ndsm, tree, _ = _scene()
    dsm[:, :20] = dtm[:, :20]  # a 20 m wide no-return strip (filled DSM := DTM), like open water
    dsm[0, 59] = np.nan
    cov = bc.coverage_mask(dsm, dtm, 5)
    can = bc.canopy_cells(ndsm, cov, np.zeros(ndsm.shape, bool), 3.0)
    assert (can[:, :14] == 255).all()  # farther than 5 m from any return → nodata
    assert can[0, 59] == 255
    assert (can[:, 25:40] != 255).all()  # measured land stays 0/1
    assert set(np.unique(can)) <= {0, 1, 255}


def test_slope_of_plane(bc):
    yy, xx = np.mgrid[0:50, 0:50].astype(np.float32)
    dtm = 0.1 * xx + 0.2 * yy  # 1 m grid
    s = bc.slope_deg(dtm, 1.0)
    want = np.degrees(np.arctan(np.hypot(0.1, 0.2)))
    assert np.allclose(s[1:-1, 1:-1], want, atol=1e-3)
    assert np.allclose(bc.slope_deg(np.full((10, 10), 5.0), 1.0), 0.0)


# ------------------------------------------------------------------ risk.py on a synthetic data dir
X0, Y1, N = 500000.0, 3900100.0, 100  # 100×100 m place in EPSG:32617


def _write(path: Path, arr: np.ndarray, dtype: str, nodata) -> None:
    import rasterio
    from rasterio.transform import from_origin

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype=dtype,
                       crs="EPSG:32617", transform=from_origin(X0, Y1, 1, 1), nodata=nodata) as d:
        d.write(arr.astype(dtype), 1)


@pytest.fixture()
def place(tmp_path, monkeypatch):
    lv, cv = config.data_version("lidar"), config.data_version("canopy")
    _write(tmp_path / "lidar" / lv / "Test_Place" / "ndsm.tif", np.zeros((N, N)), "float32", -9999.0)
    can = np.zeros((N, N), np.uint8)
    can[:, :50] = 1  # west half under canopy
    can[80:, :] = 255  # southern 20 m: no lidar returns
    _write(tmp_path / "canopy" / cv / "Test_Place" / "canopy.tif", can, "uint8", 255)
    slope = np.full((N, N), 7, np.uint8)
    slope[80:, :] = 255
    _write(tmp_path / "canopy" / cv / "Test_Place" / "slope.tif", slope, "uint8", 255)
    (tmp_path / "canopy" / cv / "Test_Place" / "meta.json").write_text(json.dumps({"place": "Test Place"}))
    monkeypatch.setattr(config, "DATA", tmp_path)
    risk._lidar_places.cache_clear()
    yield tmp_path
    risk._lidar_places.cache_clear()


def _feat(x0, y0, x1, y1, **props):
    return {"type": "Feature", "properties": props, "geometry": mapping(to_wgs84(box(x0, y0, x1, y1)))}


def test_canopy_over_pct_half_pool(place):
    # 10×6 m pool centred on the canopy edge (x = X0 + 50), inside measured rows
    f = _feat(X0 + 45, Y1 - 40, X0 + 55, Y1 - 34)
    (r,) = risk.canopy_facts([f], concept="test")  # no buffer
    assert r["canopy_over_pct"] == pytest.approx(50.0, abs=5)
    assert r["canopy_status"] == "measured"
    (rp,) = risk.canopy_facts([_feat(X0 + 45, Y1 - 40, X0 + 55, Y1 - 34, kind="pool")])  # +2 m pool buffer, symmetric
    assert rp["canopy_over_pct"] == pytest.approx(50.0, abs=5)


def test_outside_coverage_is_null_not_zero(place):
    far = _feat(X0 + 5000, Y1 - 40, X0 + 5010, Y1 - 30)  # no lidar place there
    gap = _feat(X0 + 60, Y1 - 95, X0 + 70, Y1 - 85)  # inside the place but every cell is nodata
    full = _feat(X0 + 60, Y1 - 40, X0 + 70, Y1 - 30)  # measured, no canopy → a real 0
    out = risk.canopy_facts([far, gap, full], concept="test")
    assert [o["canopy_over_pct"] for o in out] == [None, None, 0.0]
    assert [o["canopy_status"] for o in out] == ["unavailable", "unavailable", "measured"]
    sl = risk.slope_facts([far, gap, full])
    assert [s["slope_deg"] for s in sl] == [None, None, 7.0]


def test_raster_path_uses_canopy_version(place):
    p = risk._raster_path("Test Place", "canopy")
    assert p is not None and p.parent.parent.name == config.data_version("canopy") and p.parts[-4] == "canopy"
