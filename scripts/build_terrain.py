"""Terrain display products from a lidar place's bare-earth DTM → data/terrain/<version>/<Place>/ (a NEW versioned folder).

  python scripts/build_terrain.py --place "Lake Norman" --out data/terrain/v1 [--lidar-dir data/lidar/v2]
                                  [--zmin 10 --zmax 15] [--no-contours]

Inputs (READ-ONLY): <lidar-dir>/<Place>/{dtm,dsm}.tif (1 m, EPSG:32617) + meta.json (bbox, acquisition).
Outputs in <out>/<Place>/:
  hillshade.png        RGBA, EPSG:3857, ~2 m ground/px over the place bbox. Single light az 315°, alt 45°, z-factor
                       Z_FACTOR. Shadows black, lit slopes white; ALPHA encodes the departure from flat-ground shade
                       (flat = transparent). Unmeasured cells fully transparent. Corners (lon/lat) in meta for a MapLibre
                       ``image`` source (MapLibre stretches linearly between the corners in Web Mercator).
  elevation_tint.png   RGBA, EPSG:3857, ~4 m ground/px. Hypsometric tint with the viridis ramp (perceptually ordered,
                       CC0 colour map data) over [legend min, legend max] m; constant alpha TINT_ALPHA; unmeasured
                       transparent.
  terrain_rgb/{z}/{x}/{y}.png  XYZ (Google/OSM scheme), 256 px RGB, Mapbox Terrain-RGB encoding
                       height = -10000 + (R*65536 + G*256 + B) * 0.1, zooms zmin..zmax over the bbox. Pixels whose whole
                       footprint is measured carry the area-mean DTM; every other pixel (water, outside the lidar
                       project, outside the bbox in edge tiles) is FILLED for display only: pull-push pyramid fill,
                       feathered into a Gaussian-smoothed copy with distance. The filled share is in meta.json.
  contours_5m.geojson  5 m contours (EPSG:4326) of the measured DTM at 4 m (gdal_contour, system GDAL CLI), simplified
                       (2 m tolerance in EPSG:32617), property elev_m. No contour crosses unmeasured cells.
  meta.json            inputs + dates, vertical datum, method, every parameter, bounds/corners, filled shares, sizes,
                       elevation stats, shoreline ground elevation at large no-return areas, build time.

Measured vs filled: scripts/build_lidar.py fills all cells without ground returns (DTM := nearest ground + median;
DSM := DTM where no return at all). A 1 m DTM cell counts as MEASURED only within conf canopy.coverage_radius_m of a
cell with DSM != DTM (the docs/CANOPY.md rule) AND where the DTM is backed by ground returns rather than the fill
(groundtruth.terrain.measured_mask / ground_support): parts of Lake Norman have water-surface returns, so the first rule
alone would present the filled lake DTM as measured. Filled values only ever appear in the terrain-RGB tiles, which
are visual-only. The water-surface returns give the lake level (meta lake_surface_from_water_returns).

Memory: 1 m rasters are processed in row strips; the largest in-RAM arrays are the 2 m grids (~25 M cells).
Deterministic: no randomness; PNGs written by Pillow without timestamps; contours sorted.
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth import terrain as T  # noqa: E402

AZIMUTH_DEG, ALTITUDE_DEG, Z_FACTOR = 315.0, 45.0, 2.0
HILLSHADE_M, TINT_M, CONTOUR_GRID_M = 2.0, 4.0, 4.0
SHADE_MAX_ALPHA, LIGHT_MAX_ALPHA = 200, 90
TINT_ALPHA = 150
CONTOUR_INTERVAL_M, CONTOUR_SIMPLIFY_M = 5.0, 2.0
FEATHER_M = 60.0  # Gaussian sigma (metres) of the smoothed copy used for filled tile pixels
STRIP = 512
WATER_CLEAR_PX, WATER_MAX_DIFF_M = 10, 1.0  # water-surface cells: ≥ 10 m from ground support, |DSM - DTM| < 1 m
WATER_HIST_FROM_M, WATER_HIST_BINS = 150.0, 15000
GDAL_CACHE_MB = 128  # caps GDAL's block cache (default 5 % of RAM), which otherwise grows the RSS
# viridis (matplotlib _cm_listed, CC0) sampled at 9 stops
VIRIDIS = np.array([[68, 1, 84], [71, 44, 122], [59, 81, 139], [44, 113, 142], [33, 144, 141], [39, 173, 129],
                    [92, 200, 99], [170, 220, 50], [253, 231, 37]], np.float32)
WEB_MERC = "EPSG:3857"
R_EARTH = 6378137.0


def log(msg: str) -> None:
    print(f"[terrain] {time.strftime('%H:%M:%S')} {msg} (rss {rss_mb():.0f} MB)", flush=True)


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


# ------------------------------------------------------------------ pure helpers
def hillshade(dtm: np.ndarray, res_m: float, az_deg: float, alt_deg: float, z: float) -> np.ndarray:
    """Lambertian hillshade in [0, 1] (row 0 = north). ESRI convention: az clockwise from north, light FROM az."""
    gy, gx = np.gradient(dtm.astype(np.float32) * np.float32(z), np.float32(res_m))
    # rows increase southwards → dz/dnorth = -gy
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)  # direction the slope faces (downhill), clockwise from north
    del gx, gy
    zen, az = math.radians(90.0 - alt_deg), math.radians(az_deg)
    s = math.cos(zen) * np.cos(slope) + math.sin(zen) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(s, 0, 1).astype(np.float32)


def encode_terrain_rgb(h: np.ndarray) -> np.ndarray:
    v = np.clip(np.round((h.astype(np.float64) + 10000.0) * 10.0), 0, 2 ** 24 - 1).astype(np.uint32)
    return np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], axis=-1).astype(np.uint8)


def decode_terrain_rgb(rgb: np.ndarray) -> np.ndarray:
    r, g, b = (rgb[..., k].astype(np.float64) for k in range(3))
    return -10000.0 + (r * 65536 + g * 256 + b) * 0.1


def ramp(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1) * (len(VIRIDIS) - 1)
    i = np.minimum(np.floor(t).astype(np.int32), len(VIRIDIS) - 2)
    f = (t - i)[..., None]
    return np.round(VIRIDIS[i] * (1 - f) + VIRIDIS[i + 1] * f).astype(np.uint8)


def merc_xy(lon: float, lat: float) -> tuple[float, float]:
    return (R_EARTH * math.radians(lon), R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def tile_range(bbox: list[float], z: int) -> tuple[int, int, int, int]:
    n = 2 ** z

    def tx(lon: float) -> int:
        return int((lon + 180.0) / 360.0 * n)

    def ty(lat: float) -> int:
        la = math.radians(lat)
        return int((1.0 - math.asinh(math.tan(la)) / math.pi) / 2.0 * n)

    return tx(bbox[0]), ty(bbox[3]), tx(bbox[2]), ty(bbox[1])  # x0, y0 (north), x1, y1 (south), inclusive


def tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    span = 2 * math.pi * R_EARTH / 2 ** z
    x0 = -math.pi * R_EARTH + x * span
    y1 = math.pi * R_EARTH - y * span
    return x0, y1 - span, x0 + span, y1


def write_png(path: Path, arr: np.ndarray, mode: str) -> int:
    from PIL import Image

    Image.fromarray(arr, mode).save(path, format="PNG", optimize=True)
    return path.stat().st_size


# ------------------------------------------------------------------ stages
def pass_measured(src_dir: Path, tmp: Path, R: int) -> dict[str, Any]:
    """1 m strips → tmp/measured_dtm.tif (float32, NaN unmeasured), tmp/measured.tif (uint8 0/1) and the 2 m grids
    (raw DTM block mean, all-4-measured mask). Returns stats and the 2 m arrays."""
    import rasterio
    from rasterio.windows import Window
    from scipy import ndimage

    with rasterio.open(src_dir / "dtm.tif") as s_dtm, rasterio.open(src_dir / "dsm.tif") as s_dsm:
        W, H, tr, crs = s_dtm.width, s_dtm.height, s_dtm.transform, s_dtm.crs
        assert (s_dsm.width, s_dsm.height, s_dsm.transform) == (W, H, tr), "dsm/dtm grids differ"
        res = float(tr.a)
        k = int(round(HILLSHADE_M / res))
        W2, H2 = W // k, H // k
        dtm2 = np.zeros((H2, W2), np.float32)
        m2 = np.zeros((H2, W2), bool)
        prof = {"driver": "GTiff", "width": W, "height": H, "count": 1, "crs": crs, "transform": tr,
                "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate"}
        hist = np.zeros(4000, np.int64)  # 0.1 m bins from 0 m
        whist = np.zeros(WATER_HIST_BINS, np.int64)  # 1 cm bins from WATER_HIST_FROM_M: DSM of water-return cells
        n_meas, n_all, n_ret, vmin, vmax = 0, 0, 0, math.inf, -math.inf
        P = max(T.pad_px(R), WATER_CLEAR_PX + 1)
        strip = STRIP - STRIP % k
        with rasterio.open(tmp / "measured_dtm.tif", "w", dtype="float32", nodata=float("nan"), predictor=3,
                           **prof) as o_d, rasterio.open(tmp / "measured.tif", "w", dtype="uint8", nodata=None,
                                                         **prof) as o_m:
            for a in range(0, H, strip):
                b = min(H, a + strip)
                a0, b0 = max(0, a - P), min(H, b + P)
                win = Window(0, a0, W, b0 - a0)
                dtm = s_dtm.read(1, window=win)
                dsm = s_dsm.read(1, window=win)
                inner = slice(a - a0, a - a0 + (b - a))
                ret = T.return_mask(dsm, dtm, R)
                sup = T.ground_support(dtm)
                ok = (ret & sup)[inner]
                n_ret += int(ret[inner].sum())
                # water-surface returns: a real return (DSM != DTM) near the fill level, far from ground support
                far = ~ndimage.binary_dilation(sup, iterations=WATER_CLEAR_PX)
                wat = (far & (dsm != dtm) & (np.abs(dsm - dtm) < WATER_MAX_DIFF_M) & (dsm > -9000))[inner]
                wv = np.clip(np.round((dsm[inner][wat] - WATER_HIST_FROM_M) * 100).astype(np.int64), 0,
                             WATER_HIST_BINS - 1)
                whist += np.bincount(wv, minlength=WATER_HIST_BINS)
                del dsm, ret, sup, far, wat
                dtm = dtm[inner]
                md = np.where(ok, dtm, np.nan).astype(np.float32)
                o_d.write(md, 1, window=Window(0, a, W, b - a))
                o_m.write(ok.astype(np.uint8), 1, window=Window(0, a, W, b - a))
                v = dtm[ok]
                n_meas += int(v.size)
                n_all += int(ok.size)
                if v.size:
                    vmin, vmax = min(vmin, float(v.min())), max(vmax, float(v.max()))
                    hist += np.bincount(np.clip((v * 10).astype(np.int64), 0, len(hist) - 1), minlength=len(hist))
                # 2 m block aggregates (rows a..b are a multiple of k except maybe the last strip)
                ra, rb = a // k, min(H2, b // k)
                if rb > ra:
                    hh, ww = (rb - ra) * k, W2 * k
                    dtm2[ra:rb] = dtm[:hh, :ww].reshape(rb - ra, k, W2, k).mean(axis=(1, 3))
                    m2[ra:rb] = ok[:hh, :ww].reshape(rb - ra, k, W2, k).all(axis=(1, 3))
                log(f"measured pass rows {a}-{b} / {H}")
    water = water_surface(whist)
    cum = np.cumsum(hist)
    pct = {p: round(float(np.searchsorted(cum, cum[-1] * p / 100.0)) / 10.0, 1) for p in (0.1, 1, 50, 99, 99.9)}
    tr2 = rasterio.Affine(tr.a * k, 0, tr.c, 0, tr.e * k, tr.f)
    return {"W": W, "H": H, "transform": tr, "crs": crs, "res_m": res, "dtm2": dtm2, "m2": m2, "tr2": tr2,
            "measured_cells": n_meas, "cells": n_all, "return_cells": n_ret, "min_m": round(vmin, 2),
            "max_m": round(vmax, 2), "pct_m": pct, "water": water}


def water_surface(whist: np.ndarray) -> dict[str, Any]:
    n = int(whist.sum())
    if n == 0:
        return {"status": "no water-surface returns found"}
    cum = np.cumsum(whist)

    def q(p: float) -> float:
        return round(WATER_HIST_FROM_M + float(np.searchsorted(cum, n * p)) / 100.0, 2)

    return {"status": "ok", "cells_1m": n, "median_m": q(0.5), "p25_m": q(0.25), "p75_m": q(0.75),
            "p5_m": q(0.05), "p95_m": q(0.95),
            "mode_m": round(WATER_HIST_FROM_M + float(np.argmax(whist)) / 100.0, 2),
            "method": (f"1 m cells with a real lidar return (DSM != DTM) lying more than {WATER_CLEAR_PX} m from any "
                       f"ground-supported DTM cell and within {WATER_MAX_DIFF_M:g} m of the fill level (|DSM - DTM| < "
                       f"{WATER_MAX_DIFF_M:g} m, which excludes roofs, bridges and boats standing higher). Their DSM "
                       "is the highest non-noise return in the cell, i.e. lidar returns from the water surface; "
                       "quantiles in 1 cm bins. Only part of the lake has such returns (others have none).")}


def shoreline_elevation(m2: np.ndarray, dtm2: np.ndarray, res2: float) -> dict[str, Any]:
    """Elevation of measured ground at the edge of LARGE no-return areas (open water / outside the project):
    the lowest measured ground next to the water approximates the water level at acquisition. Not a water return."""
    from scipy import ndimage

    void = ~m2
    lab, nl = ndimage.label(void)
    if nl == 0:
        return {"status": "no large no-return area"}
    sizes = ndimage.sum_labels(np.ones_like(lab, np.int32), lab, index=np.arange(1, nl + 1)) * res2 * res2
    big_ids = np.flatnonzero(sizes >= 1e6) + 1  # ≥ 1 km²
    if big_ids.size == 0:
        return {"status": "no no-return area ≥ 1 km²"}
    big = np.isin(lab, big_ids)
    del lab
    ring = ndimage.binary_dilation(big, iterations=2) & m2
    # exclude the raster frame: void touching the edge may be outside the project rather than water
    v = dtm2[ring]
    hist = np.bincount(np.clip((v * 10).astype(np.int64), 0, 39999))
    mode = float(np.argmax(hist)) / 10.0
    near = np.abs(v - mode) <= 0.5
    return {"status": "ok", "large_no_return_km2": round(float(sizes[big_ids - 1].sum()) / 1e6, 2),
            "ring_cells_2m": int(v.size), "mode_m": round(mode + 0.05, 2),
            "p10_m": round(float(np.percentile(v, 10)), 2), "median_m": round(float(np.median(v)), 2),
            "share_within_0_5m_of_mode": round(float(near.mean()), 3),
            "median_within_0_5m_of_mode_m": round(float(np.median(v[near])), 2),
            "method": ("Measured 2 m DTM cells within 4 m (2 px) of no-return areas ≥ 1 km²; mode of their elevations "
                       "in 0.1 m bins (the water's-edge ground). This is ground beside the water, not a water "
                       "return: an independent cross-check of lake_surface_from_water_returns. The ring also "
                       "includes the edge of the area outside the lidar project, which is land, so only the dominant "
                       "mode is reported.")}


def overlay_grid(bbox: list[float], ground_m: float) -> dict[str, Any]:
    x0, y0 = merc_xy(bbox[0], bbox[1])
    x1, y1 = merc_xy(bbox[2], bbox[3])
    scale = 1.0 / math.cos(math.radians((bbox[1] + bbox[3]) / 2))  # Web Mercator units per ground metre
    w = int(round((x1 - x0) / (ground_m * scale)))
    h = int(round((y1 - y0) / (ground_m * scale)))
    import rasterio

    tr = rasterio.transform.from_bounds(x0, y0, x1, y1, w, h)
    return {"width": w, "height": h, "transform": tr, "bounds_3857": [x0, y0, x1, y1],
            "px_3857": [(x1 - x0) / w, (y1 - y0) / h], "ground_m_per_px_at_center": ground_m}


def corners(bbox: list[float]) -> list[list[float]]:
    w, s, e, n = bbox
    return [[w, n], [e, n], [e, s], [w, s]]  # MapLibre image source order: TL, TR, BR, BL


def warp(src_path: Path, grid: dict[str, Any], resampling, nodata=float("nan")) -> np.ndarray:
    import rasterio
    from rasterio.warp import reproject

    out = np.full((grid["height"], grid["width"]), np.nan, np.float32)
    with rasterio.open(src_path) as s:
        reproject(source=rasterio.band(s, 1), destination=out, dst_transform=grid["transform"], dst_crs=WEB_MERC,
                  resampling=resampling, src_nodata=nodata, dst_nodata=float("nan"), warp_mem_limit=128)
    return out


def build_hillshade(st: dict[str, Any], tmp: Path, out: Path, bbox: list[float]) -> dict[str, Any]:
    import rasterio
    from rasterio.enums import Resampling
    from scipy import ndimage

    hs = hillshade(st["dtm2"], HILLSHADE_M, AZIMUTH_DEG, ALTITUDE_DEG, Z_FACTOR)
    valid = ndimage.binary_erosion(st["m2"], iterations=1)  # the 3×3 gradient touches filled cells at the edge
    hs[~valid] = np.nan
    prof = {"driver": "GTiff", "width": hs.shape[1], "height": hs.shape[0], "count": 1, "crs": st["crs"],
            "transform": st["tr2"], "dtype": "float32", "nodata": float("nan"), "tiled": True, "compress": "deflate"}
    with rasterio.open(tmp / "hs_utm.tif", "w", **prof) as o:
        o.write(hs, 1)
    del hs
    grid = overlay_grid(bbox, HILLSHADE_M)
    s = warp(tmp / "hs_utm.tif", grid, Resampling.bilinear)
    flat = math.sin(math.radians(ALTITUDE_DEG))
    rgba = np.zeros(s.shape + (4,), np.uint8)
    ok = np.isfinite(s)
    dark = ok & (s < flat)
    lit = ok & (s >= flat)
    rgba[lit, :3] = 255
    a = np.zeros(s.shape, np.float32)
    a[dark] = (flat - s[dark]) / flat * SHADE_MAX_ALPHA
    a[lit] = (s[lit] - flat) / (1 - flat) * LIGHT_MAX_ALPHA
    rgba[..., 3] = np.round(a).astype(np.uint8)
    del a, s
    size = write_png(out / "hillshade.png", rgba, "RGBA")
    return {"file": "hillshade.png", "bytes": size, "width": grid["width"], "height": grid["height"],
            "crs": WEB_MERC, "bounds_3857": [round(v, 3) for v in grid["bounds_3857"]],
            "bounds_lonlat": bbox, "corners_lonlat": corners(bbox),
            "ground_m_per_px": HILLSHADE_M, "transparent_share": round(float((~ok).mean()), 4),
            "params": {"azimuth_deg": AZIMUTH_DEG, "altitude_deg": ALTITUDE_DEG, "z_factor": Z_FACTOR,
                       "computed_on": f"{HILLSHADE_M:g} m EPSG:32617 grid (2×2 block mean of the 1 m DTM), then "
                                      "bilinear-warped to EPSG:3857",
                       "encoding": (f"flat ground (shade = sin(altitude) = {flat:.3f}) transparent; darker → black with "
                                    f"alpha up to {SHADE_MAX_ALPHA}; brighter → white with alpha up to "
                                    f"{LIGHT_MAX_ALPHA}; unmeasured (and a 1 px edge) alpha 0")}}


def build_tint(tmp: Path, out: Path, bbox: list[float], vmin: float, vmax: float) -> dict[str, Any]:
    from rasterio.enums import Resampling

    grid = overlay_grid(bbox, TINT_M)
    d = warp(tmp / "measured_dtm.tif", grid, Resampling.average)
    f = warp(tmp / "measured.tif", grid, Resampling.average, nodata=None)
    ok = np.isfinite(d) & (f >= 0.999)
    lo, hi = math.floor(vmin), math.ceil(vmax)
    rgba = np.zeros(d.shape + (4,), np.uint8)
    rgba[ok, :3] = ramp((d[ok] - lo) / max(hi - lo, 1e-6))
    rgba[ok, 3] = TINT_ALPHA
    size = write_png(out / "elevation_tint.png", rgba, "RGBA")
    return {"file": "elevation_tint.png", "bytes": size, "width": grid["width"], "height": grid["height"],
            "crs": WEB_MERC, "bounds_3857": [round(v, 3) for v in grid["bounds_3857"]], "bounds_lonlat": bbox,
            "corners_lonlat": corners(bbox), "ground_m_per_px": TINT_M,
            "legend": {"min_m": lo, "max_m": hi, "ramp": "viridis (low = dark purple, high = yellow)",
                       "stops_rgb": VIRIDIS.astype(int).tolist(), "alpha": TINT_ALPHA,
                       "basis": "measured DTM min/max over the lidar raster, rounded outward to whole metres"},
            "transparent_share": round(float((~ok).mean()), 4),
            "params": {"resampling": "area mean of measured 1 m cells; a pixel is drawn only if its whole footprint "
                                     "is measured"}}


def pushpull(d: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """Smooth hole filling (pull-push pyramid): unmeasured pixels take the measured values averaged at the coarsest
    level that has data, bilinearly upsampled. Measured pixels are returned unchanged."""
    from scipy import ndimage

    if ok.all() or not ok.any():
        return np.where(ok, d, d[ok].mean() if ok.any() else 0.0).astype(np.float32)
    h, w = d.shape
    if min(h, w) <= 2:
        return np.where(ok, d, d[ok].mean()).astype(np.float32)
    H2, W2 = -(-h // 2), -(-w // 2)
    dp = np.zeros((H2 * 2, W2 * 2), np.float64)
    wp = np.zeros((H2 * 2, W2 * 2), np.float64)
    dp[:h, :w] = np.where(ok, d, 0)
    wp[:h, :w] = ok
    sw = wp.reshape(H2, 2, W2, 2).sum(axis=(1, 3))
    sv = (dp * wp).reshape(H2, 2, W2, 2).sum(axis=(1, 3))
    cok = sw > 0
    coarse = pushpull(np.where(cok, sv / np.maximum(sw, 1), 0).astype(np.float32), cok)
    up = ndimage.zoom(coarse, 2, order=1, mode="nearest", grid_mode=True)[:h, :w]
    return np.where(ok, d, up).astype(np.float32)


def fill_visual(d: np.ndarray, ok: np.ndarray, px_m: float) -> np.ndarray:
    """Display-only fill of unmeasured pixels: pull-push pyramid fill, feathered with distance from the measured
    pixels into a Gaussian-smoothed copy (removes pyramid seams). Measured pixels unchanged."""
    from scipy import ndimage

    filled = pushpull(d, ok)
    dist = ndimage.distance_transform_edt(~ok)
    sigma = max(1.0, FEATHER_M / px_m)
    smooth = ndimage.gaussian_filter(filled, sigma=sigma, mode="nearest")
    w = np.clip(dist / (3 * sigma), 0, 1).astype(np.float32)
    return np.where(ok, d, (1 - w) * filled + w * smooth).astype(np.float32)


def build_tiles(tmp: Path, out: Path, bbox: list[float], zmin: int, zmax: int) -> dict[str, Any]:
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds

    root = out / "terrain_rgb"
    per_zoom, total_bytes, total_tiles = {}, 0, 0
    filled_px, all_px, filled_in_bbox, all_in_bbox = 0, 0, 0, 0
    bx0, by0 = merc_xy(bbox[0], bbox[1])
    bx1, by1 = merc_xy(bbox[2], bbox[3])
    for z in range(zmin, zmax + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        ntx, nty = x1 - x0 + 1, y1 - y0 + 1
        gb = (tile_bounds_3857(z, x0, y1)[0], tile_bounds_3857(z, x0, y1)[1],
              tile_bounds_3857(z, x1, y0)[2], tile_bounds_3857(z, x1, y0)[3])
        grid = {"width": ntx * 256, "height": nty * 256,
                "transform": from_bounds(*gb, ntx * 256, nty * 256)}
        px = (gb[2] - gb[0]) / grid["width"]
        d = warp(tmp / "measured_dtm.tif", grid, Resampling.average)
        f = warp(tmp / "measured.tif", grid, Resampling.average, nodata=None)
        ok = np.isfinite(d) & (f >= 0.999)
        del f
        ground_px = px * math.cos(math.radians((bbox[1] + bbox[3]) / 2))
        filled = fill_visual(np.where(ok, d, 0).astype(np.float32), ok, ground_px) if ok.any() else None
        del d
        if filled is None:
            continue
        # bbox share
        cx = gb[0] + (np.arange(grid["width"]) + 0.5) * px
        cy = gb[3] - (np.arange(grid["height"]) + 0.5) * px
        inb = ((cy >= by0) & (cy <= by1))[:, None] & ((cx >= bx0) & (cx <= bx1))[None, :]
        filled_px += int((~ok).sum())
        all_px += ok.size
        filled_in_bbox += int((~ok & inb).sum())
        all_in_bbox += int(inb.sum())
        zb, zt = 0, 0
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                r, c = (ty - y0) * 256, (tx - x0) * 256
                rgb = encode_terrain_rgb(filled[r:r + 256, c:c + 256])
                p = root / str(z) / str(tx)
                p.mkdir(parents=True, exist_ok=True)
                zb += write_png(p / f"{ty}.png", rgb, "RGB")
                zt += 1
        per_zoom[str(z)] = {"tiles": zt, "bytes": zb, "x": [x0, x1], "y": [y0, y1],
                            "px_3857": round(px, 4), "ground_m_per_px": round(ground_px, 3),
                            "filled_share": round(float((~ok).mean()), 4),
                            "filled_share_in_bbox": round(float((~ok & inb).sum() / max(inb.sum(), 1)), 4)}
        total_bytes += zb
        total_tiles += zt
        log(f"terrain-rgb z{z}: {zt} tiles, {zb / 1e6:.1f} MB, filled {per_zoom[str(z)]['filled_share']:.1%}")
        del filled, ok, inb
    return {"dir": "terrain_rgb", "url_template": "terrain_rgb/{z}/{x}/{y}.png", "scheme": "xyz", "tile_size": 256,
            "encoding": "mapbox", "decode": "height_m = -10000 + (R*256*256 + G*256 + B) * 0.1",
            "minzoom": zmin, "maxzoom": zmax, "bounds_lonlat": bbox, "tiles": total_tiles, "bytes": total_bytes,
            "per_zoom": per_zoom,
            "filled_share_all_tile_pixels": round(filled_px / max(all_px, 1), 4),
            "filled_share_in_bbox": round(filled_in_bbox / max(all_in_bbox, 1), 4),
            "measured_pixel_rule": "area mean of measured 1 m DTM cells; only pixels whose whole footprint is measured",
            "fill": {"visual_only": True,
                     "rule": (f"unmeasured pixels (open water, outside the lidar project, outside the bbox inside edge "
                              f"tiles) = pull-push pyramid fill from the measured pixels (2×2 weighted means, "
                              f"bilinear upsampling), blended with distance d from the measured pixels into a "
                              f"Gaussian-smoothed copy (sigma = {FEATHER_M:g} m, weight min(1, d / 3 sigma))"),
                     "warning": "Filled heights are for 3D display only and are NEVER measurements; measure with "
                                "POST /api/v1/terrain/profile (returns None where unmeasured)."},
            "maplibre_note": ("Use as a raster-dem source with encoding 'mapbox', tileSize 256, minzoom/maxzoom and "
                              "bounds as above; beyond the bounds MapLibre has no tiles (height 0), so keep the camera "
                              "within the bbox.")}


def build_contours(tmp: Path, out: Path, st: dict[str, Any]) -> dict[str, Any] | None:
    import rasterio
    import shapely
    from shapely.geometry import mapping

    from groundtruth.geo import to_wgs84

    if shutil.which("gdal_contour") is None:
        return None
    k = int(round(CONTOUR_GRID_M / HILLSHADE_M))
    d2, m2 = st["dtm2"], st["m2"]
    H4, W4 = d2.shape[0] // k, d2.shape[1] // k
    d4 = d2[:H4 * k, :W4 * k].reshape(H4, k, W4, k).mean(axis=(1, 3))
    m4 = m2[:H4 * k, :W4 * k].reshape(H4, k, W4, k).all(axis=(1, 3))
    d4 = np.where(m4, d4, -9999.0).astype(np.float32)
    tr2 = st["tr2"]
    tr4 = rasterio.Affine(tr2.a * k, 0, tr2.c, 0, tr2.e * k, tr2.f)
    with rasterio.open(tmp / "dtm4.tif", "w", driver="GTiff", width=W4, height=H4, count=1, dtype="float32",
                       crs=st["crs"], transform=tr4, nodata=-9999.0) as o:
        o.write(d4, 1)
    del d4, m4
    raw = tmp / "contours_utm.geojson"
    subprocess.run(["gdal_contour", "-q", "-a", "elev_m", "-i", str(CONTOUR_INTERVAL_M), "-snodata", "-9999",
                    "-f", "GeoJSON", str(tmp / "dtm4.tif"), str(raw)], check=True)
    fc = json.loads(raw.read_text())
    feats = []
    for f in fc["features"]:
        g = shapely.geometry.shape(f["geometry"]).simplify(CONTOUR_SIMPLIFY_M, preserve_topology=False)
        if g.is_empty or g.length < 3 * CONTOUR_GRID_M:
            continue
        g = shapely.set_precision(to_wgs84(g), 1e-6)
        feats.append({"type": "Feature", "properties": {"elev_m": round(float(f["properties"]["elev_m"]), 1)},
                      "geometry": mapping(g)})
    feats.sort(key=lambda f: (f["properties"]["elev_m"], json.dumps(f["geometry"]["coordinates"])[:80]))
    p = out / "contours_5m.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return {"file": "contours_5m.geojson", "bytes": p.stat().st_size, "features": len(feats),
            "interval_m": CONTOUR_INTERVAL_M,
            "levels_m": sorted({f["properties"]["elev_m"] for f in feats}),
            "params": {"grid": f"{CONTOUR_GRID_M:g} m EPSG:32617 block mean of the DTM, only fully measured cells "
                               "(contours stop at unmeasured cells)",
                       "tool": "gdal_contour (GDAL CLI, MIT)",
                       "simplify_tolerance_m": CONTOUR_SIMPLIFY_M, "min_length_m": 3 * CONTOUR_GRID_M,
                       "precision_deg": 1e-6}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--place", default="Lake Norman")
    ap.add_argument("--lidar-dir", default=None, help="default: config.data_dir('lidar')")
    ap.add_argument("--out", required=True, help="a NEW versioned folder, e.g. data/terrain/v1")
    ap.add_argument("--zmin", type=int, default=10)
    ap.add_argument("--zmax", type=int, default=15)
    ap.add_argument("--no-contours", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing <out>/<Place> (never the demo's)")
    a = ap.parse_args()

    def resolve(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() or q.parts[0] != "data" else config.DATA / Path(*q.parts[1:])

    folder = a.place.replace(" ", "_")
    src = (resolve(a.lidar_dir) if a.lidar_dir else config.data_dir("lidar")) / folder
    out = resolve(a.out) / folder
    if (out / "meta.json").exists() and not a.overwrite:
        sys.exit(f"{out} already built; pass --overwrite or use a new version folder")
    if out.exists() and a.overwrite:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    lmeta = json.loads((src / "meta.json").read_text())
    bbox = [float(v) for v in lmeta["bbox"]]
    R = int(round(T.coverage_radius_m()))
    t0 = time.time()
    import rasterio

    with tempfile.TemporaryDirectory(prefix="gt_terrain_") as td, rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB):
        tmp = Path(td)
        log(f"measured pass over {src}")
        st = pass_measured(src, tmp, R)
        log(f"measured {st['measured_cells'] / st['cells']:.1%}, {st['min_m']}..{st['max_m']} m")
        shore = shoreline_elevation(st["m2"], st["dtm2"], HILLSHADE_M)
        log(f"shoreline: {shore}")
        hs = build_hillshade(st, tmp, out, bbox)
        log(f"hillshade {hs['bytes'] / 1e6:.1f} MB")
        contours = None if a.no_contours else build_contours(tmp, out, st)
        if contours:
            log(f"contours {contours['features']} features, {contours['bytes'] / 1e6:.1f} MB")
        del st["dtm2"], st["m2"]
        tint = build_tint(tmp, out, bbox, st["min_m"], st["max_m"])
        log(f"tint {tint['bytes'] / 1e6:.1f} MB")
        tiles = build_tiles(tmp, out, bbox, a.zmin, a.zmax)
    meta = {
        "status": "ok", "place": a.place, "version": resolve(a.out).name, "script": "scripts/build_terrain.py",
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "build_s": round(time.time() - t0, 1),
        "peak_rss_mb": round(rss_mb(), 0),
        "inputs": {"dtm": str(src / "dtm.tif"), "dsm": str(src / "dsm.tif"), "lidar_meta": str(src / "meta.json"),
                   "lidar_version": src.parent.name, "project": lmeta.get("project"), "source": lmeta.get("source"),
                   "acquired": lmeta.get("acquired"), "lidar_built": lmeta.get("built"),
                   "licence": lmeta.get("licence"), "lidar_method": lmeta.get("method")},
        "crs": {"input": str(st["crs"]), "overlays": WEB_MERC, "tiles": WEB_MERC + " (XYZ)",
                "contours": "EPSG:4326", "measure": "EPSG:32617"},
        "vertical_datum": T.VERTICAL_DATUM,
        "units": "metres",
        "bbox_lonlat": bbox,
        "measured": {"rule": (f"a 1 m DTM cell is measured if (1) it lies within {R} m of a cell with DSM != DTM (a "
                              "real lidar return; the docs/CANOPY.md rule) AND (2) the DTM there is backed by ground "
                              f"returns: at least {T.SUPPORT_MIN_SHARE:g} of the cells in its "
                              f"{2 * T.SUPPORT_PX + 1}x{2 * T.SUPPORT_PX + 1} window differ from a vertical and a "
                              "horizontal neighbour (the lidar build's nearest-ground fill repeats values exactly). "
                              "groundtruth.terrain.measured_mask. Rule (2) is needed because parts of Lake Norman "
                              "have water-surface returns, which satisfy rule (1) over a filled DTM; it also leaves "
                              "the interior of large roofs unmeasured."),
                     "support_px": T.SUPPORT_PX, "support_min_share": T.SUPPORT_MIN_SHARE,
                     "return_share": round(st["return_cells"] / st["cells"], 4),
                     "coverage_radius_m": R, "cells": st["cells"], "measured_cells": st["measured_cells"],
                     "measured_share": round(st["measured_cells"] / st["cells"], 4),
                     "unmeasured_share": round(1 - st["measured_cells"] / st["cells"], 4)},
        "elevation_m": {"min": st["min_m"], "max": st["max_m"], "percentiles": st["pct_m"],
                        "basis": "measured 1 m DTM cells only"},
        "shoreline_ground_elevation": shore,
        "lake_surface_from_water_returns": st["water"],
        "products": {"hillshade": hs, "elevation_tint": tint, "terrain_rgb": tiles, "contours": contours},
        "sources": [{"name": T.SOURCE["name"], "url": T.SOURCE["url"], "date": lmeta.get("acquired", ""),
                     "licence": T.SOURCE["licence"]}],
        "licence": "Derived from USGS 3DEP lidar (public domain); viridis colour map data CC0",
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    log(f"done in {meta['build_s']} s → {out}")


if __name__ == "__main__":
    main()
