"""Canopy (tree cover) + slope rasters per lidar place → data/canopy/<version>/<Place>/ (a NEW versioned folder).

  python scripts/build_canopy.py --out data/canopy/v1 [--lidar-dir data/lidar/v2] [--places "Myers Park" ...]

Per place, aligned to the 1 m lidar nDSM grid (EPSG:32617), written strip-wise (no whole-raster arrays in RAM):

  canopy.tif   uint8  1 = canopy, 0 = not canopy, 255 = nodata (no lidar returns here: outside coverage / open water)
               canopy = measured nDSM ≥ conf canopy.min_height_m
                        AND not under an Overture building footprint (grown by conf canopy.building_buffer_m)
                        AND NAIP NDVI ≥ conf canopy.ndvi.min somewhere in a (2·ndvi.tolerance_px+1)² window
                            (the second signal: removes towers, cranes, bridges, power lines, buildings missing
                            from Overture; the window tolerates NAIP-vs-lidar misregistration / tree lean)
               where NAIP has no valid pixel the NDVI test is skipped (lidar-only) and counted in meta.json
  slope.tif    uint8  degrees from the bare-earth DTM (np.gradient at the grid spacing), 255 = nodata (same mask)
  overview.png RGBA canopy overview at conf canopy.overview_m per pixel (green = canopy, grey = no data), bounds in meta
  meta.json    inputs with dates, thresholds, resolution, CRS, coverage, counts, build date

Coverage: the lidar build (scripts/build_lidar.py) fills cells without returns (DSM := DTM, DTM := nearest ground),
so a filled cell looks like flat ground. A cell counts as MEASURED only when it is within conf
canopy.coverage_radius_m of a cell whose DSM ≠ DTM (a real non-fill return). Large no-return areas (open water,
outside the flight) become 255, never 0. Nothing is filled or interpolated here.

Canopy cover estimate from lidar height; not individual-tree detection.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402

NODATA = 255
LIDAR_DATES = {"flown": "2016", "published": "2019", "project": "USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019",
               "licence": "Public domain (USGS 3DEP)",
               "url": "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019/ept.json"}
OVERTURE_RELEASE = "2026-10-21.0"
STATEMENT = "canopy cover estimate from lidar height; not individual-tree detection"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def canopy_conf() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "risk.json").read_text())["canopy"]


# ------------------------------------------------------------------ pure functions (unit-tested in tests/test_canopy.py)
def _disk(r: int) -> np.ndarray:
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def coverage_mask(dsm: np.ndarray, dtm: np.ndarray, radius_px: int) -> np.ndarray:
    """True where the lidar measured something: within ``radius_px`` of a cell with a real return (DSM ≠ DTM)."""
    from scipy import ndimage

    ok = np.isfinite(dsm) & np.isfinite(dtm) & (dsm > -9000) & (dtm > -9000)
    hit = ok & (dsm != dtm)
    if radius_px > 0:
        hit = ndimage.binary_dilation(hit, structure=_disk(radius_px))
    return hit & ok


def slope_deg(dtm: np.ndarray, res_m: float) -> np.ndarray:
    """Slope in degrees (float32) from a DTM by central differences at the grid spacing."""
    gy, gx = np.gradient(dtm.astype(np.float64), res_m)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)


def ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    r, n = red.astype(np.float32), nir.astype(np.float32)
    return (n - r) / np.maximum(n + r, 1.0)


def vegetation_near(ndvi_arr: np.ndarray, valid: np.ndarray, ndvi_min: float, tolerance_px: int) -> np.ndarray:
    """True where some valid NAIP pixel within ±tolerance_px has NDVI ≥ ndvi_min."""
    from scipy import ndimage

    veg = valid & (ndvi_arr >= ndvi_min)
    if tolerance_px > 0:
        veg = ndimage.maximum_filter(veg, size=2 * tolerance_px + 1)
    return veg


def canopy_cells(ndsm: np.ndarray, covered: np.ndarray, building: np.ndarray, min_height_m: float,
                 veg: np.ndarray | None = None, naip_valid: np.ndarray | None = None) -> np.ndarray:
    """uint8: 1 canopy, 0 not, 255 nodata. veg (NDVI test) applies only where naip_valid; elsewhere lidar-only."""
    tall = np.isfinite(ndsm) & (ndsm >= min_height_m) & ~building
    if veg is not None:
        nv = naip_valid if naip_valid is not None else np.ones_like(veg, bool)
        tall &= veg | ~nv
    out = tall.astype(np.uint8)
    out[~covered] = NODATA
    return out


# ------------------------------------------------------------------ build
def _buildings_m(bounds_m: tuple[float, ...], buffer_m: float):
    from shapely.geometry import box, shape
    from shapely.strtree import STRtree

    from groundtruth.geo import to_measure, to_wgs84
    from groundtruth.vector import store

    fc, _ = store().buildings(to_wgs84(box(*bounds_m)).bounds)
    geoms = []
    for f in fc["features"]:
        g = to_measure(shape(f["geometry"]))
        if not g.is_empty:
            geoms.append(g.buffer(buffer_m) if buffer_m else g)
    return geoms, (STRtree(geoms) if geoms else None)


def build_place(pl: str, lidar_dir: Path, out: Path, cc: dict[str, Any], strip: int = 1024,
                window: tuple[int, int, int, int] | None = None) -> dict[str, Any] | None:
    """``window`` = (col, row, width, height) builds only a sub-window (probe); otherwise the whole grid."""
    import rasterio
    from rasterio.features import rasterize
    from rasterio.windows import Window
    from shapely.geometry import box

    from groundtruth.geo import to_wgs84
    from groundtruth.providers.public import PublicDataProvider

    src = lidar_dir / pl.replace(" ", "_")
    if not (src / "ndsm.tif").exists():
        log(f"{pl}: no lidar in {src}, skipped")
        return None
    t0 = time.time()
    dst = out / pl.replace(" ", "_")
    dst.mkdir(parents=True, exist_ok=True)
    prov = PublicDataProvider()
    nd_c = cc["ndvi"]
    R = int(round(cc["coverage_radius_m"]))
    year = int(cc["naip_year"])
    with rasterio.open(src / "dsm.tif") as s_dsm, rasterio.open(src / "dtm.tif") as s_dtm, \
            rasterio.open(src / "ndsm.tif") as s_nd:
        res = abs(s_nd.transform.a)
        c0, r0w, W, H = window or (0, 0, s_nd.width, s_nd.height)
        x0 = s_nd.transform.c + c0 * res
        y1 = s_nd.transform.f - r0w * res
        bounds_m = (x0, y1 - H * res, x0 + W * res, y1)
        tr = rasterio.transform.from_origin(x0, y1, res, res)
        geoms, tree = _buildings_m(bounds_m, cc["building_buffer_m"])
        log(f"{pl}: {W}×{H} cells at {res} m, {len(geoms)} Overture buildings, NAIP {year}, strips of {strip} rows")
        prof = {"driver": "GTiff", "width": W, "height": H, "count": 1, "dtype": "uint8", "nodata": NODATA,
                "crs": "EPSG:32617", "transform": tr, "compress": "deflate", "tiled": True,
                "blockxsize": 256, "blockysize": 256}
        ov = int(cc["overview_m"] / res)
        ovw, ovh = -(-W // ov), -(-H // ov)
        ov_can = np.zeros((ovh, ovw), np.float32)
        ov_val = np.zeros((ovh, ovw), np.float32)
        n = {"cells": W * H, "covered": 0, "canopy": 0, "tall": 0, "tall_building": 0, "tall_not_veg": 0,
             "naip_invalid_covered": 0}
        scenes: set[str] = set()
        pad = max(R, 1)
        with rasterio.open(dst / "canopy.tif", "w", **prof) as oc, rasterio.open(dst / "slope.tif", "w", **prof) as os_:
            for a in range(0, H, strip):
                b = min(H, a + strip)
                a0, b0 = max(0, a - pad), min(H, b + pad)
                win = Window(c0, r0w + a0, W, b0 - a0)
                dsm = s_dsm.read(1, window=win)
                dtm = s_dtm.read(1, window=win)
                cov = coverage_mask(dsm, dtm, R)
                sl = slope_deg(dtm, res)
                del dsm
                inner = slice(a - a0, a - a0 + (b - a))
                cov, sl = cov[inner], sl[inner]
                ndsm = s_nd.read(1, window=Window(c0, r0w + a, W, b - a)).astype(np.float32)
                sb = (x0, y1 - b * res, x0 + W * res, y1 - a * res)
                s_tr = rasterio.transform.from_origin(sb[0], sb[3], res, res)
                hits = [geoms[int(i)] for i in tree.query(box(*sb))] if tree is not None else []
                bld = (rasterize(hits, out_shape=ndsm.shape, transform=s_tr, fill=0, default_value=1,
                                 dtype="uint8") > 0) if hits else np.zeros(ndsm.shape, bool)
                img = prov.read_grid(sb, year, gsd_m=res, bands=(1, 4))
                scenes |= set(img.scene_ids)
                nv = img.meta["valid"]
                veg = vegetation_near(ndvi(img.rgb[..., 0], img.rgb[..., 1]), nv, nd_c["min"], nd_c["tolerance_px"])
                can = canopy_cells(ndsm, cov, bld, cc["min_height_m"], veg, nv)
                sl8 = np.clip(np.round(sl), 0, 89).astype(np.uint8)
                sl8[~cov] = NODATA
                oc.write(can, 1, window=Window(0, a, W, b - a))
                os_.write(sl8, 1, window=Window(0, a, W, b - a))
                tall = cov & np.isfinite(ndsm) & (ndsm >= cc["min_height_m"])
                n["covered"] += int(cov.sum())
                n["canopy"] += int((can == 1).sum())
                n["tall"] += int(tall.sum())
                n["tall_building"] += int((tall & bld).sum())
                n["tall_not_veg"] += int((tall & ~bld & nv & ~veg).sum())
                n["naip_invalid_covered"] += int((cov & ~nv).sum())
                # overview accumulation (block means of canopy over measured cells)
                hh = b - a
                rows = (np.arange(a, b) // ov)
                for rr in np.unique(rows):
                    sel = rows == rr
                    c_ = (can[sel] == 1).sum(axis=0).astype(np.float32)
                    v_ = (can[sel] != NODATA).sum(axis=0).astype(np.float32)
                    pw = ovw * ov - W
                    c_ = np.pad(c_, (0, pw)).reshape(ovw, ov).sum(axis=1)
                    v_ = np.pad(v_, (0, pw)).reshape(ovw, ov).sum(axis=1)
                    ov_can[rr] += c_
                    ov_val[rr] += v_
                del ndsm, dtm, cov, sl, sl8, bld, img, veg, can, tall, hh
    _write_overview(dst / "overview.png", ov_can, ov_val, ov * ov)
    cov_pct = 100 * n["covered"] / max(n["cells"], 1)
    meta = {
        "place": pl, "product": "canopy", "version": out.name, "statement": STATEMENT,
        "values": {"canopy.tif": {"1": "canopy", "0": "not canopy", str(NODATA): "nodata (no lidar returns)"},
                   "slope.tif": {"0-89": "slope in whole degrees", str(NODATA): "nodata (same mask)"}},
        "rule": {"min_height_m": cc["min_height_m"], "building_buffer_m": cc["building_buffer_m"],
                 "ndvi_min": nd_c["min"], "ndvi_tolerance_px": nd_c["tolerance_px"],
                 "coverage_radius_m": cc["coverage_radius_m"],
                 "text": f"nDSM ≥ {cc['min_height_m']} m AND not in an Overture building (+{cc['building_buffer_m']} m) "
                         f"AND NAIP {year} NDVI ≥ {nd_c['min']} within ±{nd_c['tolerance_px']} px; NDVI skipped where NAIP "
                         "has no valid pixel"},
        "inputs": {
            "lidar": {**LIDAR_DATES, "folder": str(src), "nDSM_method": "build_lidar.py: DSM max Z − DTM min ground Z"},
            "naip": {"year": year, "scenes": sorted(scenes),
                     "flight_dates": sorted({s.rsplit("_", 1)[-1] for s in scenes}), "bands_used": "1 (red), 4 (NIR)",
                     "licence": "Public domain (USDA NAIP)", "resampling": "bilinear 0.6 m → 1 m (provider read_grid)"},
            "buildings": {"name": "Overture Maps buildings", "release": OVERTURE_RELEASE, "count": len(geoms),
                          "licence": "ODbL 1.0 (© OpenStreetMap contributors, Overture Maps Foundation)",
                          "use": "exclusion mask only (rasterised, buffered); no building attributes are kept"},
        },
        "res_m": res, "crs": "EPSG:32617", "grid": [W, H], "window": list(window) if window else None,
        "bounds_m": list(bounds_m), "bbox_wgs84": [round(v, 6) for v in to_wgs84(box(*bounds_m)).bounds],
        "coverage_pct": round(cov_pct, 1),
        "canopy_pct_of_measured": round(100 * n["canopy"] / max(n["covered"], 1), 1),
        "counts": n,
        "overview": {"file": "overview.png", "m_per_px": cc["overview_m"], "bounds_m": list(bounds_m),
                     "bbox_wgs84": [round(v, 6) for v in to_wgs84(box(*bounds_m)).bounds], "crs": "EPSG:32617"},
        "processing_date": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.time() - t0),
        "script": "scripts/build_canopy.py",
    }
    (dst / "meta.json").write_text(json.dumps(meta, indent=1))
    log(f"{pl}: done in {meta['seconds']} s · coverage {meta['coverage_pct']}% · canopy {meta['canopy_pct_of_measured']}% "
        f"of measured · tall {n['tall']:,} (building {n['tall_building']:,}, not-veg {n['tall_not_veg']:,})")
    return meta


def _write_overview(path: Path, can: np.ndarray, val: np.ndarray, block: int) -> None:
    from PIL import Image

    frac = np.where(val > 0, can / np.maximum(val, 1), 0)
    rgba = np.zeros(can.shape + (4,), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 34, 139, 34
    rgba[..., 3] = np.clip(np.round(frac * 220), 0, 220).astype(np.uint8)
    nod = val < 0.5 * block
    rgba[nod] = (128, 128, 128, 90)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--lidar-dir", default="data/lidar/v2")
    ap.add_argument("--places", nargs="*")
    ap.add_argument("--window", nargs=4, type=int, help="col row width height: a sub-window probe")
    ap.add_argument("--strip", type=int, default=1024)
    a = ap.parse_args()

    def data_path(s: str) -> Path:  # "data/..." → under GROUNDTRUTH_DATA
        p = Path(s)
        return p if p.is_absolute() or p.parts[0] != "data" else config.DATA / Path(*p.parts[1:])

    out, ld = data_path(a.out), data_path(a.lidar_dir)
    pls = a.places or sorted(p.name.replace("_", " ") for p in ld.iterdir() if (p / "ndsm.tif").exists())
    cc = canopy_conf()
    for pl in pls:
        build_place(pl, ld, out, cc, strip=a.strip, window=tuple(a.window) if a.window else None)


if __name__ == "__main__":
    main()
