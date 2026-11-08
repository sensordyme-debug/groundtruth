"""Colour-balanced, feathered NAIP display mosaic for the demo places (track F0.2). Heavy: runs on the laptop.

Why: adjacent NAIP quarter-quads come from different flights (e.g. 2022-06-02 vs 2022-09-27), so the provider's
"first valid pixel wins" tile composite shows hard colour/brightness seams (Lake Norman water shows a vertical band).
This builds DISPLAY imagery only. The engine's index and crops keep reading the raw scenes (read_grid / get_imagery).

  python scripts/heavy/build_mosaic.py --year 2022 --version v1 [--workers 6] [--fetch]

Steps (per year, deterministic, stable orders):
  1. plan    areas = every place in conf/places.json, bbox padded PAD_M (EPSG:32617). Output grid: square blocks of
             BLOCK_PX x 0.6 m aligned to multiples of BLOCK_M in EPSG:32617; a block is built when it touches an area.
             Scenes = every scene whose footprint touches a block (provider priority order).
  2. fetch   (--fetch) the scene COGs into the local COG cache (PublicDataProvider.ensure_local; public data, no key).
  3. fit     each scene is read once at FIT_GSD (overviews). For every overlapping pair, per-band quantiles of the shared
             pixels (away from edges) give robust (x_i, x_j) correspondences. One least-squares solve per band fits a
             gain/offset per scene (reference scene fixed at gain 1, offset 0; weak prior to identity). The reference is
             the scene with the largest area inside the padded areas (ties: scene id). Per-scene 8-bit LUTs apply the
             gain/offset with soft knees at both ends, so nothing clips hard to 0 or 255.
  4. blend   seams follow the medial line of each overlap: every pixel weights scene i by a smoothstep of
             (d_i - max_{j!=i} d_j), d = distance to the scene's own valid-data edge, over FEATHER_PX pixels.
  5. write   one COG per block (3-band RGB, JPEG YCbCr q90, 512 px tiles, internal mask, overviews) to
             data/tiles/mosaic/<version>/<year>/blocks/b_<i>_<j>.tif, plus meta.json (grid, blocks, scenes, reference,
             transforms, fit residuals, areas, build time).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import socket
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

import rasterio  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.shutil import copy as rio_copy  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.vrt import WarpedVRT  # noqa: E402
from rasterio.windows import Window  # noqa: E402
from scipy import ndimage  # noqa: E402
from shapely.geometry import box, mapping, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from groundtruth import config  # noqa: E402
from groundtruth.geo import MEASURE_CRS, to_measure, to_wgs84  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402

BUILD_VERSION = "mosaic-1.0"
GSD = 0.6
BLOCK_PX = 8192
BLOCK_M = BLOCK_PX * GSD          # 4915.2 m
WIN = 2048                        # processing window inside a block
FIT_GSD = 4.8                     # fit + distance grid (BLOCK_M / FIT_GSD = 1024, so grids nest)
PAD_M = 1000.0
FEATHER_PX = 96                   # seam transition width (57.6 m)
EDGE_M = 30.0                     # fit pixels must be this far inside both scenes
MIN_PAIR_PX = 1500                # min shared fit pixels (~0.035 km²) to use a pair
QUANTILES = np.linspace(2, 98, 49)
GAIN_RANGE = (0.75, 1.33)
OFFSET_RANGE = (-40.0, 40.0)
KNEE_LO, KNEE_HI = 12.0, 232.0    # soft knees of the LUT
# low-frequency seam correction ("dodging"): overlap low-pass mismatch, spread smoothly into each scene
SIG_L_M = 150.0                   # low-pass of each scene (what counts as "brightness", not texture)
SIG_S_M = 300.0                   # smoothing of the mismatch along the seam
TAU_M = 1500.0                    # Gaussian taper of the correction with distance from the overlap
CMAX = 30.0                       # |correction| clamp, DN
FIELD_F = 4                       # correction field grid = FIT_GSD * FIELD_F (19.2 m)
JPEG_QUALITY = 90

GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_CACHEMAX": 256, "GDAL_NUM_THREADS": "1",
            "GDAL_TIFF_INTERNAL_MASK": "YES"}


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# ---------------------------------------------------------------- plan
def load_areas() -> dict[str, list[float]]:
    pl = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
    return {k: list(pl[k]["bbox"]) for k in sorted(pl)}


def plan(p: PublicDataProvider, year: int, areas: dict[str, list[float]]) -> dict:
    padded = {k: to_measure(box(*b)).buffer(PAD_M, join_style=2) for k, b in areas.items()}
    region = unary_union(list(padded.values()))
    x0, y0, x1, y1 = region.bounds
    cells = [(i, j) for i in range(math.floor(x0 / BLOCK_M), math.ceil(x1 / BLOCK_M))
             for j in range(math.floor(y0 / BLOCK_M), math.ceil(y1 / BLOCK_M))
             if box(i * BLOCK_M, j * BLOCK_M, (i + 1) * BLOCK_M, (j + 1) * BLOCK_M).intersects(region)]
    foot: dict[str, object] = {}
    blocks = []
    for i, j in sorted(cells):
        cb = box(i * BLOCK_M, j * BLOCK_M, (i + 1) * BLOCK_M, (j + 1) * BLOCK_M)
        sids = []
        for s in p.list_scenes(to_wgs84(cb).bounds, year):
            if s.scene_id not in foot:
                foot[s.scene_id] = to_measure(shape(s.geometry) if s.geometry else box(*s.bbox))
            if foot[s.scene_id].intersects(cb):
                sids.append(s.scene_id)
        blocks.append({"i": i, "j": j, "bounds_m": [round(v, 3) for v in cb.bounds], "scenes": sids})
    scenes = sorted({s for b in blocks for s in b["scenes"]})
    inside = {s: foot[s].intersection(region).area for s in scenes}
    ref = sorted(scenes, key=lambda s: (-round(inside[s], 1), s))[0]
    return {"region": region, "padded": padded, "blocks": blocks, "scenes": scenes,
            "footprints": {s: foot[s] for s in scenes}, "inside_m2": inside, "reference": ref}


# ---------------------------------------------------------------- fit (low resolution)
def read_lowres(path: Path, fp) -> dict:
    bx0, by0, bx1, by1 = fp.buffer(300).bounds
    c0, r0 = math.floor(bx0 / FIT_GSD), math.floor(-by1 / FIT_GSD)
    c1, r1 = math.ceil(bx1 / FIT_GSD), math.ceil(-by0 / FIT_GSD)
    w, h = c1 - c0, r1 - r0
    tr = from_origin(c0 * FIT_GSD, -r0 * FIT_GSD, FIT_GSD, FIT_GSD)
    with rasterio.Env(**GDAL_ENV), open_level(path, FIT_GSD) as src, \
            WarpedVRT(src, crs=MEASURE_CRS, transform=tr, width=w, height=h, resampling=Resampling.bilinear,
                      nodata=0) as vrt:
        rgb = vrt.read(indexes=[1, 2, 3])
        m = vrt.read_masks(1) > 0
    m = ndimage.binary_erosion(m, iterations=1)
    d = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1].astype(np.float32) * FIT_GSD
    return {"c0": c0, "r0": r0, "rgb": rgb, "mask": m, "d": d}


def open_level(path, gsd: float):
    """Open a NAIP COG at the coarsest internal overview that is still at least as fine as `gsd`."""
    with rasterio.open(path) as s:
        res, ovs = s.res[0], s.overviews(1)
    lvl = None
    for k, f in enumerate(ovs):
        if res * f <= gsd + 1e-6:
            lvl = k
    return rasterio.open(path, overview_level=lvl) if lvl is not None else rasterio.open(path)


def overlap(a: dict, b: dict):
    c0, r0 = max(a["c0"], b["c0"]), max(a["r0"], b["r0"])
    c1 = min(a["c0"] + a["mask"].shape[1], b["c0"] + b["mask"].shape[1])
    r1 = min(a["r0"] + a["mask"].shape[0], b["r0"] + b["mask"].shape[0])
    if c1 <= c0 or r1 <= r0:
        return None
    sa = (slice(r0 - a["r0"], r1 - a["r0"]), slice(c0 - a["c0"], c1 - a["c0"]))
    sb = (slice(r0 - b["r0"], r1 - b["r0"]), slice(c0 - b["c0"], c1 - b["c0"]))
    return sa, sb


def make_lut(gain: float, offset: float) -> np.ndarray:
    """uint8 -> float32 LUT: gain/offset with soft knees (slope 1 at the knee, asymptotic to 1 / 255)."""
    y = gain * np.arange(256, dtype=np.float64) + offset
    hi, lo = y > KNEE_HI, y < KNEE_LO
    y[hi] = KNEE_HI + (255 - KNEE_HI) * (1 - np.exp(-(y[hi] - KNEE_HI) / (255 - KNEE_HI)))
    y[lo] = KNEE_LO - (KNEE_LO - 1) * (1 - np.exp(-(KNEE_LO - y[lo]) / (KNEE_LO - 1)))
    y[0] = 0  # value 0 is never valid (nodata), keep it 0
    return y.astype(np.float32)


def fit(lr: dict[str, dict], scenes: list[str], ref: str) -> dict:
    idx = {s: k for k, s in enumerate(scenes)}
    pairs = []
    for a in range(len(scenes)):
        for b in range(a + 1, len(scenes)):
            A, B = lr[scenes[a]], lr[scenes[b]]
            ov = overlap(A, B)
            if ov is None:
                continue
            sa, sb = ov
            m = A["mask"][sa] & B["mask"][sb] & (A["d"][sa] > EDGE_M) & (B["d"][sb] > EDGE_M)
            n = int(m.sum())
            if n < MIN_PAIR_PX:
                continue
            qa = np.stack([np.percentile(A["rgb"][k][sa][m], QUANTILES) for k in range(3)])
            qb = np.stack([np.percentile(B["rgb"][k][sb][m], QUANTILES) for k in range(3)])
            pairs.append({"a": scenes[a], "b": scenes[b], "n": n, "qa": qa, "qb": qb})
    N = len(scenes)
    gains, offsets = np.ones((N, 3)), np.zeros((N, 3))
    for band in range(3):
        rows, rhs = [], []
        for pr in pairs:
            w = math.sqrt(pr["n"] / 1000.0)
            ia, ib = idx[pr["a"]], idx[pr["b"]]
            for xa, xb in zip(pr["qa"][band], pr["qb"][band]):
                r = np.zeros(2 * N)
                r[2 * ia], r[2 * ia + 1], r[2 * ib], r[2 * ib + 1] = w * xa, w, -w * xb, -w
                rows.append(r); rhs.append(0.0)
        for k in range(N):  # weak prior to identity; hard anchor on the reference
            lam_a, lam_b = (1e6, 1e4) if scenes[k] == ref else (20.0, 0.2)
            r = np.zeros(2 * N); r[2 * k] = lam_a; rows.append(r); rhs.append(lam_a)
            r = np.zeros(2 * N); r[2 * k + 1] = lam_b; rows.append(r); rhs.append(0.0)
        sol = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
        gains[:, band] = np.clip(sol[0::2], *GAIN_RANGE)
        offsets[:, band] = np.clip(sol[1::2], *OFFSET_RANGE)
    luts = {s: np.stack([make_lut(gains[idx[s], k], offsets[idx[s], k]) for k in range(3)]) for s in scenes}
    pair_stats = []
    for pr in pairs:
        before = float(np.sqrt(np.mean((pr["qa"] - pr["qb"]) ** 2)))
        la, lb = luts[pr["a"]], luts[pr["b"]]
        ta = np.stack([np.interp(pr["qa"][k], np.arange(256), la[k]) for k in range(3)])
        tb = np.stack([np.interp(pr["qb"][k], np.arange(256), lb[k]) for k in range(3)])
        after = float(np.sqrt(np.mean((ta - tb) ** 2)))
        pair_stats.append({"a": pr["a"], "b": pr["b"], "n_px": pr["n"], "rms_before": round(before, 2),
                           "rms_after": round(after, 2)})
    sat = {}
    for s in scenes:
        A = lr[s]
        v = A["rgb"][:, A["mask"]]
        out = np.stack([luts[s][k][v[k]] for k in range(3)])
        sat[s] = {"in_ge254": round(float((v >= 254).mean()), 6), "out_ge254": round(float((out >= 253.5).mean()), 6)}
    return {"gains": {s: [round(float(g), 5) for g in gains[idx[s]]] for s in scenes},
            "offsets": {s: [round(float(o), 4) for o in offsets[idx[s]]] for s in scenes},
            "luts": luts, "pairs": pair_stats, "saturation": sat}


def _lowpass(rgb: np.ndarray, m: np.ndarray, sigma_px: float) -> np.ndarray:
    """Masked (normalised-convolution) Gaussian low-pass of (3,h,w) float."""
    mf = m.astype(np.float32)
    g = ndimage.gaussian_filter(mf, sigma_px, mode="constant")
    out = np.zeros(rgb.shape, np.float32)
    for k in range(3):
        n = ndimage.gaussian_filter(rgb[k] * mf, sigma_px, mode="constant")
        out[k] = np.where(g > 1e-3, n / np.maximum(g, 1e-6), 0)
    return out


def dodge_fields(lr: dict[str, dict], luts: dict[str, np.ndarray], scenes: list[str]) -> tuple[dict, dict]:
    """Per-scene additive low-frequency correction (3, H/F, W/F) at FIT_GSD*FIELD_F.

    In the overlap cores (>= EDGE_M inside every scene involved) each scene's low-pass (sigma SIG_L_M) is pulled to
    the mean low-pass of all scenes there; the mismatch is smoothed along the seam (SIG_S_M), extended by nearest
    fill, and tapered with a Gaussian of the distance to the overlap (TAU_M), so after correction neighbouring scenes
    agree at low frequency on the seam and the change fades out over ~TAU_M inside each scene."""
    F, cg = FIELD_F, FIT_GSD * FIELD_F
    low = {}
    for s in scenes:
        A = lr[s]
        rgb = np.stack([luts[s][k][A["rgb"][k]] for k in range(3)])
        low[s] = _lowpass(rgb, A["mask"], SIG_L_M / FIT_GSD)
    fields, stats = {}, {}
    for s in scenes:
        A = lr[s]
        core = A["mask"] & (A["d"] > EDGE_M)
        acc = low[s] * core
        cnt = core.astype(np.float32)
        for t in scenes:
            if t == s:
                continue
            ov = overlap(A, lr[t])
            if ov is None:
                continue
            sa, sb = ov
            ct = core[sa] & lr[t]["mask"][sb] & (lr[t]["d"][sb] > EDGE_M)
            if not ct.any():
                continue
            acc[(slice(None),) + sa] += np.where(ct, low[t][(slice(None),) + sb], 0)
            cnt[sa] += ct
        O = cnt >= 2
        if O.sum() < MIN_PAIR_PX:
            continue
        mis = np.where(O, acc / np.maximum(cnt, 1) - low[s], 0).astype(np.float32)
        h, w = O.shape
        H, W = -(-h // F), -(-w // F)
        Op = np.zeros((H * F, W * F), np.float32); Op[:h, :w] = O
        oc = Op.reshape(H, F, W, F).mean((1, 3))
        sig = SIG_S_M / cg
        g = ndimage.gaussian_filter(oc, sig, mode="constant")
        ok = g > 0.02
        C = np.zeros((3, H, W), np.float32)
        dist = ndimage.distance_transform_edt(oc <= 0) * cg
        taper = np.exp(-0.5 * (dist / TAU_M) ** 2)
        idx = ndimage.distance_transform_edt(~ok, return_distances=False, return_indices=True)
        for k in range(3):
            mp_ = np.zeros((H * F, W * F), np.float32); mp_[:h, :w] = mis[k]
            msum = mp_.reshape(H, F, W, F).mean((1, 3))
            nc = np.where(ok, ndimage.gaussian_filter(msum, sig, mode="constant") / np.maximum(g, 1e-6), 0)
            nc = nc[idx[0], idx[1]]                       # nearest fill outside the seam neighbourhood
            nc = ndimage.gaussian_filter(nc, sig, mode="nearest")
            C[k] = np.clip(nc * taper, -CMAX, CMAX)
        fields[s] = {"x0": A["c0"] * FIT_GSD, "y1": -A["r0"] * FIT_GSD, "res": cg, "arr": C}
        mis_abs = np.abs(mis[:, O])
        stats[s] = {"overlap_lowpass_mismatch_dn_p50": round(float(np.median(mis_abs)), 2),
                    "overlap_lowpass_mismatch_dn_p95": round(float(np.percentile(mis_abs, 95)), 2),
                    "correction_dn_max": round(float(np.abs(C).max()), 2)}
    return fields, stats


# ---------------------------------------------------------------- blocks (full resolution)
_G: dict = {}  # filled in the parent before forking workers: year, paths, lowres d grids, luts, out dir


def _d_full(s: str, x0: float, y1: float, w: int, h: int) -> np.ndarray:
    """Bilinear sample of scene s's low-res edge distance at the full-res pixel centres of a window."""
    A = _G["lr_d"][s]
    xs = x0 + (np.arange(w) + 0.5) * GSD
    ys = y1 - (np.arange(h) + 0.5) * GSD
    cc = xs / FIT_GSD - A["c0"] - 0.5
    rr = -ys / FIT_GSD - A["r0"] - 0.5
    R, C = np.meshgrid(rr.astype(np.float32), cc.astype(np.float32), indexing="ij")
    return ndimage.map_coordinates(A["d"], [R, C], order=1, mode="constant", cval=0.0)


def blend_window(stack: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """stack: [(rgb float32 (3,h,w), mask bool (h,w), d float32 (h,w))] -> (uint8 (3,h,w), valid (h,w))."""
    T = FEATHER_PX * GSD
    D = np.stack([np.where(m, d, -np.inf) for _, m, d in stack])  # (S,h,w)
    if len(stack) == 1:
        other = np.full(D.shape, -np.inf, dtype=np.float32)
    else:
        order = np.argsort(-D, axis=0, kind="stable")
        top1 = np.take_along_axis(D, order[:1], 0)[0]
        top2 = np.take_along_axis(D, order[1:2], 0)[0]
        other = np.where(D >= top1[None], top2[None], top1[None])
    with np.errstate(invalid="ignore"):
        t = np.clip(0.5 + (D - other) / (2 * T), 0.0, 1.0)
    t = np.where(np.isfinite(D), np.nan_to_num(t, nan=1.0, posinf=1.0), 0.0)
    W = (t * t * (3 - 2 * t)).astype(np.float32)
    tot = W.sum(0)
    acc = np.zeros(stack[0][0].shape, dtype=np.float32)
    for k, (rgb, _, _) in enumerate(stack):
        acc += rgb * W[k][None]
    valid = tot > 1e-6
    out = np.zeros(acc.shape, dtype=np.uint8)
    out[:, valid] = np.clip(np.rint(acc[:, valid] / tot[valid]), 1, 255).astype(np.uint8)
    return out, valid


def render_block(blk: dict) -> dict:
    t0 = time.time()
    i, j, sids = blk["i"], blk["j"], blk["scenes"]
    out_dir: Path = _G["out_dir"] / "blocks"
    dst = out_dir / f"b_{i}_{j}.tif"
    tmp = Path(_G["tmp_dir"]) / f"b_{_G['year']}_{i}_{j}.tif"
    x0, y1 = i * BLOCK_M, (j + 1) * BLOCK_M
    tr = from_origin(x0, y1, GSD, GSD)
    any_valid = False
    with rasterio.Env(**GDAL_ENV):
        srcs = [rasterio.open(_G["paths"][s]) for s in sids]
        vrts = [WarpedVRT(s, crs=MEASURE_CRS, transform=tr, width=BLOCK_PX, height=BLOCK_PX,
                          resampling=Resampling.bilinear, nodata=0, warp_mem_limit=256) for s in srcs]
        prof = {"driver": "GTiff", "width": BLOCK_PX, "height": BLOCK_PX, "count": 3, "dtype": "uint8",
                "crs": MEASURE_CRS, "transform": tr, "tiled": True, "blockxsize": 512, "blockysize": 512,
                "compress": "none", "photometric": "rgb"}
        with rasterio.open(tmp, "w", **prof) as out:
            for r in range(0, BLOCK_PX, WIN):
                for c in range(0, BLOCK_PX, WIN):
                    win = Window(c, r, WIN, WIN)
                    wx0, wy1 = x0 + c * GSD, y1 - r * GSD
                    stack = []
                    for s, v in zip(sids, vrts):
                        m = v.read_masks(1, window=win) > 0
                        if not m.any():
                            continue
                        raw = v.read(indexes=[1, 2, 3], window=win)
                        lut = _G["luts"][s]
                        rgb = np.stack([lut[k][raw[k]] for k in range(3)])
                        stack.append((rgb, m, _d_full(s, wx0, wy1, WIN, WIN)))
                    if stack:
                        px, valid = blend_window(stack)
                    else:
                        px, valid = np.zeros((3, WIN, WIN), np.uint8), np.zeros((WIN, WIN), bool)
                    any_valid |= bool(valid.any())
                    out.write(px, window=win)
                    out.write_mask(np.where(valid, 255, 0).astype(np.uint8), window=win)
        for v in vrts:
            v.close()
        for s in srcs:
            s.close()
        res = {"i": i, "j": j, "scenes": sids, "bounds_m": blk["bounds_m"], "seconds": round(time.time() - t0, 1)}
        if not any_valid:
            tmp.unlink(missing_ok=True)
            return {**res, "file": None}
        part = dst.with_suffix(".part.tif")
        rio_copy(str(tmp), str(part), driver="COG", COMPRESS="JPEG", QUALITY=str(JPEG_QUALITY), BLOCKSIZE="512",
                 OVERVIEW_RESAMPLING="AVERAGE", NUM_THREADS="1", BIGTIFF="IF_SAFER")
        os.replace(part, dst)
        tmp.unlink(missing_ok=True)
    return {**res, "file": f"blocks/{dst.name}", "bytes": dst.stat().st_size, "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--version", required=True, help="output folder data/tiles/mosaic/<version>/ (never an existing one)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--fetch", action="store_true", help="download missing scene COGs (public, no key)")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--out-root", default="", help="default: data/tiles/mosaic (debug builds: a scratch folder)")
    ap.add_argument("--areas", default="", help="debug: comma-separated place names (default: every place)")
    ap.add_argument("--limit-blocks", type=int, default=0, help="debug: build only the first N blocks")
    ap.add_argument("--near", default="", help="debug: 'lon,lat': build only the block containing this point (+ fit)")
    a = ap.parse_args()
    t_start = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    p = PublicDataProvider(download_scenes=a.fetch)
    areas = load_areas()
    if a.areas:
        areas = {k: v for k, v in areas.items() if k in a.areas.split(",")}
    pl = plan(p, a.year, areas)
    log(f"{a.year}: {len(areas)} areas, region {pl['region'].area / 1e6:.0f} km², {len(pl['blocks'])} blocks, "
        f"{len(pl['scenes'])} scenes, reference {pl['reference']}")
    missing = [s for s in pl["scenes"] if not p.local_scene_path(s, a.year).exists()]
    if a.plan_only:
        print(json.dumps({"blocks": len(pl["blocks"]), "scenes": pl["scenes"], "missing": missing}, indent=1))
        return
    if missing:
        if not a.fetch:
            raise SystemExit(f"{len(missing)} scenes not local (use --fetch): {missing[:5]} ...")
        for k, s in enumerate(missing, 1):
            p.ensure_local(s, a.year)
            log(f"fetched {k}/{len(missing)} {s}")
    if a.fetch_only:
        return
    out_root = Path(a.out_root or config.DATA / "tiles" / "mosaic") / a.version
    out_dir = out_root / str(a.year)
    if (out_dir / "meta.json").exists():
        raise SystemExit(f"{out_dir} already built; versions are never overwritten")
    (out_dir / "blocks").mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(os.environ.get("MOSAIC_TMP", Path.home() / ".cache" / "groundtruth" / "mosaic_tmp"))
    tmp_dir.mkdir(parents=True, exist_ok=True)

    t = time.time()
    lr = {s: read_lowres(p.local_scene_path(s, a.year), pl["footprints"][s]) for s in pl["scenes"]}
    log(f"low-res reads {time.time() - t:.0f} s")
    t = time.time()
    f = fit(lr, pl["scenes"], pl["reference"])
    rb = [x["rms_before"] for x in f["pairs"]]
    ra = [x["rms_after"] for x in f["pairs"]]
    log(f"fit {time.time() - t:.0f} s: {len(f['pairs'])} pairs, quantile RMS median {np.median(rb):.2f} -> "
        f"{np.median(ra):.2f} DN, max {max(rb):.2f} -> {max(ra):.2f}")

    _G.update({"year": a.year, "out_dir": out_dir, "tmp_dir": str(tmp_dir), "luts": f["luts"],
               "paths": {s: str(p.local_scene_path(s, a.year)) for s in pl["scenes"]},
               "lr_d": {s: {"c0": lr[s]["c0"], "r0": lr[s]["r0"], "d": lr[s]["d"]} for s in pl["scenes"]}})
    del lr
    blocks = pl["blocks"][: a.limit_blocks] if a.limit_blocks else pl["blocks"]
    if a.near:
        from shapely.geometry import Point
        pt = to_measure(Point(*map(float, a.near.split(","))))
        blocks = [b for b in pl["blocks"] if b["i"] == math.floor(pt.x / BLOCK_M) and b["j"] == math.floor(pt.y / BLOCK_M)]
    t = time.time()
    results = []
    with mp.get_context("fork").Pool(a.workers, maxtasksperchild=8) as pool:
        for k, r in enumerate(pool.imap(render_block, blocks), 1):
            results.append(r)
            if k % 5 == 0 or k == len(blocks):
                log(f"blocks {k}/{len(blocks)} ({time.time() - t:.0f} s)")
    results.sort(key=lambda r: (r["i"], r["j"]))
    built = [r for r in results if r["file"]]
    total_bytes = sum(r["bytes"] for r in built)
    meta = {
        "build_version": BUILD_VERSION, "version": a.version, "year": a.year, "crs": MEASURE_CRS, "gsd_m": GSD,
        "grid": {"block_px": BLOCK_PX, "block_m": BLOCK_M, "origin_m": [0.0, 0.0],
                 "rule": "block (i, j) covers x in [i*block_m, (i+1)*block_m), y in [j*block_m, (j+1)*block_m)"},
        "format": {"bands": "RGB", "compress": "JPEG", "photometric": "YCbCr", "quality": JPEG_QUALITY,
                   "tile_px": 512, "mask": "internal", "overviews": "AVERAGE"},
        "blend": {"method": "medial-line feather: smoothstep of (d_i - max_j d_j) over feather_px", "feather_px": FEATHER_PX,
                  "distance_grid_m": FIT_GSD},
        "colour": {"method": "per-band gain/offset, global least squares on per-pair overlap quantiles, reference fixed",
                   "quantiles": QUANTILES.tolist(), "fit_gsd_m": FIT_GSD, "edge_m": EDGE_M,
                   "gain_range": GAIN_RANGE, "offset_range": OFFSET_RANGE, "knees": [KNEE_LO, KNEE_HI],
                   "reference": pl["reference"], "reference_area_in_areas_km2": round(pl["inside_m2"][pl["reference"]] / 1e6, 3),
                   "gains": f["gains"], "offsets": f["offsets"], "pairs": f["pairs"], "saturation": f["saturation"],
                   "quantile_rms_dn": {"median_before": round(float(np.median(rb)), 2), "median_after": round(float(np.median(ra)), 2),
                                       "max_before": round(max(rb), 2), "max_after": round(max(ra), 2)}},
        "areas": {"source": "engine/groundtruth/conf/places.json", "pad_m": PAD_M, "bbox_wgs84": areas,
                  "region_km2": round(pl["region"].area / 1e6, 1)},
        "scenes": pl["scenes"],
        "blocks": [{k: r[k] for k in ("i", "j", "file", "bounds_m", "scenes")} for r in built],
        "empty_blocks": [[r["i"], r["j"]] for r in results if not r["file"]],
        "build": {"started": started, "seconds": round(time.time() - t_start, 1),
                  "block_seconds_total": round(sum(r["seconds"] for r in results), 1), "workers": a.workers,
                  "host": socket.gethostname(), "bytes": total_bytes,
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "gdal": rasterio.__gdal_version__, "rasterio": rasterio.__version__, "numpy": np.__version__},
    }
    if a.limit_blocks or a.near:
        meta["partial"] = f"debug build: {len(blocks)} block(s) only"
    tmpm = out_dir / "meta.json.tmp"
    tmpm.write_text(json.dumps(meta, indent=1))
    os.replace(tmpm, out_dir / "meta.json")
    log(f"done {a.year}: {len(built)} blocks, {total_bytes / 2**30:.2f} GB, {time.time() - t_start:.0f} s")


if __name__ == "__main__":
    main()
