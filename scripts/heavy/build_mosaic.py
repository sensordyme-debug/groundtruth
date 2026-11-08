"""Seam-free NAIP display mosaic for the demo places (track F0.2). Heavy: runs on the laptop.

Why: adjacent NAIP quarter-quads come from different flights (e.g. 2022-06-02 vs 2022-09-27), so the provider's
"first valid pixel wins" tile composite shows hard brightness seams (Lake Norman water shows a vertical band). The
overlap colour DISTRIBUTIONS of neighbouring scenes agree to ~0.5 grey levels, so the band is not a per-scene colour
offset: it is a slow within-scene brightness gradient that differs between flights. The fix is low-frequency dodging
near seams plus feathering, never a blur of content.

This builds DISPLAY imagery only. The engine's index and crops keep reading the raw scenes (read_grid / get_imagery).

  python scripts/heavy/build_mosaic.py --year 2022 --version v1 --areas "Lake Norman,Myers Park,Matthews" [--fetch]
  python scripts/heavy/build_mosaic.py --year 2022 --areas "Lake Norman" --preview=-80.875,35.50 --out-root ~/mprev

Steps (per year, deterministic, stable orders, no randomness):
  1. plan    areas = places in conf/places.json (or --areas), bbox padded PAD_M (EPSG:32617). Output grid: square blocks
             of BLOCK_PX x 0.6 m aligned to multiples of BLOCK_M in EPSG:32617; a block is built when it touches an
             area. Scenes = every scene whose footprint touches a block (provider priority order).
  2. fetch   (--fetch) the scene COGs into the local COG cache (PublicDataProvider.ensure_local; public data, no key).
  3. lowres  each scene is read once at FIT_GSD (4.8 m, COG overviews): RGB, valid mask, distance to its data edge.
  4. colour  (optional, --set global_colour=gainoffset; v1 default "none") one gain/offset per scene and band from a
             least-squares fit of overlap quantiles. Off by default: it barely changes the overlap mismatch (median
             quantile RMS 0.54 -> 0.57 DN on Lake Norman) and it would shift every interior pixel of a scene.
  5. dodge   per scene, an additive LOW-FREQUENCY correction field (3 bands, 19.2 m grid). In the overlap cores
             (>= edge_m inside every scene involved) the per-pixel difference "mean of all covering scenes - this scene"
             is taken on the SAME ground (content cancels), clipped (boats, shadows), low-passed with a Gaussian of
             sigma sig_l_m (250 m, i.e. a kernel of ~1.5 km: no texture or object survives it), extended smoothly
             (normalised convolution, sigma sig_e_m), and tapered with exp(-0.5 (dist_to_overlap / tau_m)^2). After
             correction neighbouring scenes agree at low frequency across the seam and the change fades to zero
             within ~3 tau_m (4.5 km) inside each scene. Objects keep their colour relative to their surroundings.
  6. blend   seams follow the medial line of each overlap: every pixel weights scene i by a smoothstep of
             (d_i - max_{j!=i} d_j), d = distance to the scene's own valid-data edge, over feather_px pixels.
             Within edge_blend_m of the outer edge of the mosaic coverage, weights and correction fade back to the raw
             composite (provider order, first valid wins), so mosaic tiles meet raw-fallback tiles without a step.
  7. write   one COG per block (3-band RGB, JPEG YCbCr q90, 512 px tiles, internal mask, overviews) to
             data/tiles/mosaic/<version>/<year>/blocks/b_<i>_<j>.tif, the dodge fields to fields/<scene>.npz, and
             meta.json (grid, per-block provenance: contributing scenes + acquisition dates + pixel shares, reference,
             transforms, residuals, build info, partial: false only when every planned block was built).

--preview lon,lat renders ONLY the block containing the point three ways (raw composite / feather only / dodge +
feather) and writes before/after PNGs + metrics.json (seam step on open water, colour change of non-water objects,
high-frequency detail change) into <out-root>/preview_<year>_<i>_<j>/. Nothing is written to data/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
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

MEASURE_CRS = "EPSG:32617"
BUILD_VERSION = "mosaic-1.1"
PAD_M = 1000.0
QUANTILES = np.linspace(2, 98, 49)
GAIN_RANGE = (0.75, 1.33)
OFFSET_RANGE = (-40.0, 40.0)
KNEE_LO, KNEE_HI = 12.0, 232.0    # soft knees of the LUT
JPEG_QUALITY = 90
# Every tunable (tests pass scaled-down values). Distances in metres.
PARAMS = {
    "gsd": 0.6, "block_px": 8192, "win": 2048,
    "fit_gsd": 4.8,               # low-res grid for fit, distances, dodge (block_m / fit_gsd = 1024: grids nest)
    "feather_px": 96,             # seam transition width (57.6 m)
    "edge_m": 30.0,               # fit / dodge pixels must be this far inside every scene involved
    "min_pair_px": 1500,          # min shared low-res pixels (~0.035 km²) for a pair / a dodge field
    "global_colour": "none",      # "none" | "gainoffset"
    "dodge": True,
    "sig_l_m": 250.0,             # low-pass of the overlap difference: only periods >~ 1.5 km pass
    "sig_e_m": 600.0,             # smoothing of the extension of the field away from the overlap
    "tau_m": 1500.0,              # Gaussian taper of the correction with distance from the overlap
    "cmax": 30.0,                 # |correction| clamp, DN
    "diff_clip": 30.0,            # per-pixel overlap difference clip, DN (boats, shadows, changed content)
    "field_f": 4,                 # correction field grid = fit_gsd * field_f (19.2 m)
    "edge_blend_m": 600.0,        # fade to the raw composite at the outer edge of the mosaic coverage
    # water seamlines: the NAIP orthomosaic's flight-line seamlines run THROUGH scenes and show on open water only
    "water": True,
    "w_res_m": 9.6,               # grid of the correction field (fit_gsd * 2)
    "w_min_ha": 1.0,              # water bodies smaller than this (pools, ponds) are never touched
    "w_shore_m": 20.0,            # open water = this far from shore (shallow, sediment water is not used to detect)
    "nir_lo": 40.0, "nir_hi": 70.0,  # water: NIR < nir_lo; full-res weight 1 below nir_lo, 0 above nir_hi
    "ndwi_min": 0.15, "ndwi_lo": 0.05,  # ... or NDWI > ndwi_min (sun-glint water); weight 0 at ndwi_lo
    "w_det_m": 30.0,              # step detection half-baseline
    "w_meas_m": 60.0,             # step measurement half-baseline
    "w_step_min": 2.0,            # min step, DN (luminance)
    "w_sharp": 0.65,              # |D(w_det)| / |D(w_meas)|: ~1 for a step, ~0.5 for a smooth gradient
    "w_min_len_m": 300.0,         # min straight seam length
    "w_gap_m": 400.0,             # max gap inside one seam run (land or an undetected stretch)
    "w_density": 0.25,            # min candidate pixels per pixel of seam length
    "w_max_lines": 60,
    "w_ramp_m": 60.0,             # the correction changes sign over +-w_ramp_m across the seam ...
    "w_lambda_m": 300.0,          # ... and decays as exp(-(|u| - w_ramp_m) / w_lambda_m) away from it
    "w_end_m": 150.0,             # fade beyond the ends of a detected seam
    "w_cmax": 15.0,               # |water correction| clamp, DN
}
BLOCK_M = PARAMS["block_px"] * PARAMS["gsd"]  # 4915.2 m

GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_CACHEMAX": 256, "GDAL_NUM_THREADS": "1",
            "GDAL_TIFF_INTERNAL_MASK": "YES"}


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def scene_date(sid: str) -> str:
    """Acquisition date from a NAIP scene id (…_YYYYMMDD) as YYYY-MM-DD; '' when the id carries none."""
    m = re.search(r"_(\d{4})(\d{2})(\d{2})$", sid)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


# ---------------------------------------------------------------- plan
def load_areas() -> dict[str, list[float]]:
    from groundtruth import config
    pl = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
    return {k: list(pl[k]["bbox"]) for k in sorted(pl)}


def plan(p, year: int, areas: dict[str, list[float]]) -> dict:
    from shapely.geometry import box, shape
    from shapely.ops import unary_union

    from groundtruth.geo import to_measure, to_wgs84
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
    items = {it["scene_id"]: it for it in p._items(year)}
    dates = {s: str(items.get(s, {}).get("datetime") or "")[:10] or scene_date(s) for s in scenes}
    inside = {s: foot[s].intersection(region).area for s in scenes}
    ref = sorted(scenes, key=lambda s: (-round(inside[s], 1), s))[0]
    return {"region": region, "padded": padded, "blocks": blocks, "scenes": scenes, "dates": dates,
            "footprints": {s: foot[s] for s in scenes}, "inside_m2": inside, "reference": ref}


# ---------------------------------------------------------------- low resolution
def open_level(path, gsd: float):
    """Open a NAIP COG at the coarsest internal overview that is still at least as fine as `gsd`."""
    with rasterio.open(path) as s:
        res, ovs = s.res[0], s.overviews(1)
    lvl = None
    for k, f in enumerate(ovs):
        if res * f <= gsd + 1e-6:
            lvl = k
    return rasterio.open(path, overview_level=lvl) if lvl is not None else rasterio.open(path)


def read_lowres(path, bounds_m, fit_gsd: float) -> dict:
    """Scene RGB / valid mask / distance-to-own-data-edge (m) on the fit_gsd grid, over bounds_m + 300 m."""
    bx0, by0, bx1, by1 = bounds_m
    bx0, by0, bx1, by1 = bx0 - 300, by0 - 300, bx1 + 300, by1 + 300
    c0, r0 = math.floor(bx0 / fit_gsd), math.floor(-by1 / fit_gsd)
    c1, r1 = math.ceil(bx1 / fit_gsd), math.ceil(-by0 / fit_gsd)
    w, h = c1 - c0, r1 - r0
    tr = from_origin(c0 * fit_gsd, -r0 * fit_gsd, fit_gsd, fit_gsd)
    with rasterio.Env(**GDAL_ENV), open_level(path, fit_gsd) as src, \
            WarpedVRT(src, crs=MEASURE_CRS, transform=tr, width=w, height=h, resampling=Resampling.bilinear,
                      nodata=0) as vrt:
        rgb = vrt.read(indexes=[1, 2, 3])
        nir = vrt.read(4) if vrt.count >= 4 else None
        m = vrt.read_masks(1) > 0
    m = ndimage.binary_erosion(m, iterations=1)
    d = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1].astype(np.float32) * fit_gsd
    return {"c0": c0, "r0": r0, "rgb": rgb, "nir": nir, "mask": m, "d": d}


def _read_lowres_job(args):
    sid, path, bounds, fit_gsd = args
    return sid, read_lowres(path, bounds, fit_gsd)


def overlap(a: dict, b: dict):
    c0, r0 = max(a["c0"], b["c0"]), max(a["r0"], b["r0"])
    c1 = min(a["c0"] + a["mask"].shape[1], b["c0"] + b["mask"].shape[1])
    r1 = min(a["r0"] + a["mask"].shape[0], b["r0"] + b["mask"].shape[0])
    if c1 <= c0 or r1 <= r0:
        return None
    sa = (slice(r0 - a["r0"], r1 - a["r0"]), slice(c0 - a["c0"], c1 - a["c0"]))
    sb = (slice(r0 - b["r0"], r1 - b["r0"]), slice(c0 - b["c0"], c1 - b["c0"]))
    return sa, sb


# ---------------------------------------------------------------- global colour (optional)
def make_lut(gain: float, offset: float) -> np.ndarray:
    """uint8 -> float32 LUT: gain/offset with soft knees (slope 1 at the knee, asymptotic to 1 / 255).
    The identity (gain 1, offset 0) is exactly the identity."""
    y = gain * np.arange(256, dtype=np.float64) + offset
    if gain != 1.0 or offset != 0.0:
        hi, lo = y > KNEE_HI, y < KNEE_LO
        y[hi] = KNEE_HI + (255 - KNEE_HI) * (1 - np.exp(-(y[hi] - KNEE_HI) / (255 - KNEE_HI)))
        y[lo] = KNEE_LO - (KNEE_LO - 1) * (1 - np.exp(-(KNEE_LO - y[lo]) / (KNEE_LO - 1)))
    y[0] = 0  # value 0 is never valid (nodata), keep it 0
    return y.astype(np.float32)


def fit(lr: dict[str, dict], scenes: list[str], ref: str, prm: dict = PARAMS) -> dict:
    """Per-pair overlap quantile residuals; with global_colour == 'gainoffset' also a per-scene gain/offset solve."""
    idx = {s: k for k, s in enumerate(scenes)}
    pairs = []
    for a in range(len(scenes)):
        for b in range(a + 1, len(scenes)):
            A, B = lr[scenes[a]], lr[scenes[b]]
            ov = overlap(A, B)
            if ov is None:
                continue
            sa, sb = ov
            m = A["mask"][sa] & B["mask"][sb] & (A["d"][sa] > prm["edge_m"]) & (B["d"][sb] > prm["edge_m"])
            n = int(m.sum())
            if n < prm["min_pair_px"]:
                continue
            qa = np.stack([np.percentile(A["rgb"][k][sa][m], QUANTILES) for k in range(3)])
            qb = np.stack([np.percentile(B["rgb"][k][sb][m], QUANTILES) for k in range(3)])
            pairs.append({"a": scenes[a], "b": scenes[b], "n": n, "qa": qa, "qb": qb})
    N = len(scenes)
    gains, offsets = np.ones((N, 3)), np.zeros((N, 3))
    if prm["global_colour"] == "gainoffset" and pairs:
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
        pair_stats.append({"a": pr["a"], "b": pr["b"], "n_px": pr["n"], "quantile_rms_dn": round(before, 2),
                           "quantile_rms_after_global_dn": round(after, 2)})
    return {"gains": {s: [round(float(g), 5) for g in gains[idx[s]]] for s in scenes},
            "offsets": {s: [round(float(o), 4) for o in offsets[idx[s]]] for s in scenes},
            "luts": luts, "pairs": pair_stats}


# ---------------------------------------------------------------- dodge (low-frequency seam correction)
def _blockmean(a: np.ndarray, F: int) -> np.ndarray:
    h, w = a.shape
    H, W = -(-h // F), -(-w // F)
    p = np.zeros((H * F, W * F), np.float32)
    p[:h, :w] = a
    return p.reshape(H, F, W, F).mean((1, 3))


def dodge_fields(lr: dict[str, dict], luts: dict[str, np.ndarray], scenes: list[str],
                 prm: dict = PARAMS) -> tuple[dict, dict]:
    """Per-scene additive low-frequency correction, {sid: {"x0","y1","res","arr" (3,H,W) float32}} + stats.

    For scene s, on overlap-core pixels (>= edge_m inside s and at least one other scene) the target is the mean of
    all covering scenes; D = clip(target - s). Both terms see the same ground at the same pixel, so content cancels
    and D is radiometric mismatch (+ registration noise and real changes, which the clip and the low-pass suppress).
    D is low-passed (sigma sig_l_m, normalised by the overlap support), extended away from the overlap by a wider
    normalised convolution (sig_e_m), tapered by exp(-0.5 (dist/tau_m)^2) and clamped to +-cmax."""
    fg, F = prm["fit_gsd"], int(prm["field_f"])
    cg = fg * F
    edge, clip = prm["edge_m"], prm["diff_clip"]
    val = {s: np.stack([luts[s][k][lr[s]["rgb"][k]] for k in range(3)]) for s in scenes}
    core = {s: lr[s]["mask"] & (lr[s]["d"] > edge) for s in scenes}
    fields, stats = {}, {}
    for s in scenes:
        A = lr[s]
        acc = val[s] * core[s]
        cnt = core[s].astype(np.float32)
        for t in scenes:
            if t == s:
                continue
            ov = overlap(A, lr[t])
            if ov is None:
                continue
            sa, sb = ov
            ct = core[s][sa] & core[t][sb]
            if not ct.any():
                continue
            acc[(slice(None),) + sa] += np.where(ct, val[t][(slice(None),) + sb], 0)
            cnt[sa] += ct
        O = cnt >= 2
        if int(O.sum()) < prm["min_pair_px"]:
            continue
        D = np.where(O, np.clip(acc / np.maximum(cnt, 1) - val[s], -clip, clip), 0).astype(np.float32)
        Oc = _blockmean(O.astype(np.float32), F)
        sl, se = prm["sig_l_m"] / cg, prm["sig_e_m"] / cg
        g = ndimage.gaussian_filter(Oc, sl, mode="constant")
        ok = g > 0.02
        w1 = np.where(ok, g, 0).astype(np.float32)
        gw = ndimage.gaussian_filter(w1, se, mode="constant")
        okw = gw > 1e-4
        alpha = np.clip(g / 0.3, 0, 1)
        dist = ndimage.distance_transform_edt(Oc <= 0) * cg
        taper = np.exp(-0.5 * (dist / prm["tau_m"]) ** 2)
        fill_idx = ndimage.distance_transform_edt(~okw, return_distances=False, return_indices=True)
        C = np.zeros((3,) + Oc.shape, np.float32)
        for k in range(3):
            num = ndimage.gaussian_filter(_blockmean(D[k], F), sl, mode="constant")
            F1 = np.where(ok, num / np.maximum(g, 1e-6), 0)
            E = np.where(okw, ndimage.gaussian_filter(F1 * w1, se, mode="constant") / np.maximum(gw, 1e-9), 0)
            E = E[fill_idx[0], fill_idx[1]]
            Fm = alpha * F1 + (1 - alpha) * E
            C[k] = np.clip(Fm * taper, -prm["cmax"], prm["cmax"])
        fields[s] = {"x0": A["c0"] * fg, "y1": -A["r0"] * fg, "res": cg, "arr": C}
        mis = np.abs(D[:, O])
        cin = np.abs(C[:, Oc > 0.5])
        stats[s] = {"overlap_px": int(O.sum()),
                    "overlap_diff_dn_p50": round(float(np.median(mis)), 2),
                    "overlap_diff_dn_p95": round(float(np.percentile(mis, 95)), 2),
                    "correction_in_overlap_dn_p50": round(float(np.median(cin)), 2) if cin.size else 0.0,
                    "correction_dn_max": round(float(np.abs(C).max()), 2)}
    return fields, stats


# ---------------------------------------------------------------- open-water flattening
def _normconv(x: np.ndarray, m: np.ndarray, s: float) -> tuple[np.ndarray, np.ndarray]:
    mf = m.astype(np.float32)
    g = ndimage.gaussian_filter(mf, s, mode="constant")
    return np.where(g > 1e-4, ndimage.gaussian_filter(x * mf, s, mode="constant") / np.maximum(g, 1e-9), 0), g


def water_field(lr: dict[str, dict], scenes: list[str], bounds_m, prm: dict = PARAMS) -> dict | None:
    """Additive luminance correction for water seamlines, on a w_res_m grid over bounds_m (+ 600 m).

    NAIP 2022 quarter-quads are cut from one state-wide orthomosaic (neighbouring scenes carry IDENTICAL pixels in
    their overlap), so the visible seams are the orthomosaic's own seamlines between flight lines, which run through
    scenes, not along scene edges. On land, texture hides them; on open water (flat, glint-sensitive) they show as
    straight bands with a few-DN step. Each detected seam (detect_water_seams) is cancelled by a smooth ramp: the
    same offset on R, G and B, -step/2 .. +step/2 across +-w_ramp_m, decaying over w_lambda_m away from the line, so
    only a ~1 km wide corridor along each seam changes, by a low-frequency field. Applied to water pixels only (each
    pixel's own NIR decides at full resolution), so land, docks, boats and pools are never changed."""
    fg = prm["fit_gsd"]
    f = max(1, int(round(prm["w_res_m"] / fg)))
    res = fg * f
    margin = 600.0
    bx0, by0, bx1, by1 = bounds_m
    c0 = int(math.floor((bx0 - margin) / res)) * f
    r0 = int(math.floor(-(by1 + margin) / res)) * f
    W = (int(math.ceil((bx1 + margin) / res)) * f - c0)
    H = (int(math.ceil(-(by0 - margin) / res)) * f - r0)
    rgb = np.zeros((3, H, W), np.uint8)
    nir = np.zeros((H, W), np.uint8)
    valid = np.zeros((H, W), bool)
    have_nir = False
    for s in scenes:  # composite (overlaps carry the same pixels; order only matters at data edges)
        A = lr[s]
        if A.get("nir") is None:
            continue
        have_nir = True
        h, w = A["mask"].shape
        rc0, rr0 = max(A["c0"], c0), max(A["r0"], r0)
        rc1, rr1 = min(A["c0"] + w, c0 + W), min(A["r0"] + h, r0 + H)
        if rc1 <= rc0 or rr1 <= rr0:
            continue
        sa = (slice(rr0 - A["r0"], rr1 - A["r0"]), slice(rc0 - A["c0"], rc1 - A["c0"]))
        sg = (slice(rr0 - r0, rr1 - r0), slice(rc0 - c0, rc1 - c0))
        take = A["mask"][sa] & ~valid[sg]
        for k in range(3):
            rgb[k][sg][take] = A["rgb"][k][sa][take]
        nir[sg][take] = A["nir"][sa][take]
        valid[sg] |= take
    if not have_nir:
        return None
    water = valid & _is_water(rgb[1], nir, prm)
    water = ndimage.binary_opening(water, iterations=1)
    lab, n = ndimage.label(water)
    if n == 0:
        return None
    sizes = ndimage.sum(water, lab, index=np.arange(1, n + 1))
    big = np.isin(lab, np.flatnonzero(sizes >= prm["w_min_ha"] * 1e4 / fg ** 2) + 1)
    del lab, water
    if not big.any():
        return None
    apply_m = ndimage.binary_dilation(ndimage.binary_closing(big, iterations=2) | big, iterations=2)
    wo = ndimage.binary_erosion(big, iterations=max(1, int(round(prm["w_shore_m"] / fg))))
    lum = rgb.astype(np.float32).mean(0)
    del rgb, nir, valid
    segs = detect_water_seams(lum, wo, fg, prm)
    # correction on the coarse grid (cell centres in fine-pixel units)
    Hc, Wc = H // f, W // f
    C = np.zeros((1, Hc, Wc), np.float32)
    for sg in segs:
        ramp_seg(C[0], sg, f, fg, prm)
    np.clip(C, -prm["w_cmax"], prm["w_cmax"], out=C)
    Wc_m = apply_m[: Hc * f, : Wc * f].reshape(Hc, f, Wc, f).max((1, 3))
    C[0] *= Wc_m
    for sg in segs:  # georeference for meta.json (EPSG:32617 metres)
        sg["xy0_m"] = [round(c0 * fg + (sg["p0"][0] + 0.5) * fg, 1), round(-r0 * fg - (sg["p0"][1] + 0.5) * fg, 1)]
        sg["xy1_m"] = [round(c0 * fg + (sg["p1"][0] + 0.5) * fg, 1), round(-r0 * fg - (sg["p1"][1] + 0.5) * fg, 1)]
    cw = np.abs(C[0][C[0] != 0])
    return {"x0": c0 * fg, "y1": -r0 * fg, "res": res, "C": C, "W": Wc_m.astype(np.float32),
            "segments": [{k: v for k, v in sg.items() if k not in ("p0", "p1", "phi", "rho")} for sg in segs],
            "_segs": segs, "_origin": (c0, r0, fg),
            "stats": {"water_km2": round(float(big.sum()) * fg ** 2 / 1e6, 2), "seam_lines": len(segs),
                      "seam_km": round(sum(s["length_m"] for s in segs) / 1000, 2),
                      "correction_dn_p50": round(float(np.median(cw)), 2) if cw.size else 0.0,
                      "correction_dn_max": round(float(cw.max()), 2) if cw.size else 0.0,
                      "grid": [Hc, Wc]}}


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ramp_seg(C: np.ndarray, sg: dict, f: int, fg: float, prm: dict) -> None:
    """Add one seam's correction to the coarse grid C (cell size f fine px): -(step/2) * g(u) * along(t), where u is
    the signed distance to the line (m, + on the side the step points to), g a smooth sign over +-w_ramp_m that
    decays as exp(-(|u| - w_ramp_m) / w_lambda_m), and along() fades over w_end_m beyond the segment ends."""
    phi, rho, s = sg["phi"], sg["rho"], sg["step_dn"]
    w, lam, end = sg.get("ramp_m", prm["w_ramp_m"]), prm["w_lambda_m"], prm["w_end_m"]
    reach = (w + 5 * lam + end) / fg  # fine px
    xs = np.array([sg["p0"][0], sg["p1"][0]]); ys = np.array([sg["p0"][1], sg["p1"][1]])
    Hc, Wc = C.shape
    ca, cb = int(max(0, (xs.min() - reach) // f)), int(min(Wc, (xs.max() + reach) // f + 1))
    ra, rb = int(max(0, (ys.min() - reach) // f)), int(min(Hc, (ys.max() + reach) // f + 1))
    if ca >= cb or ra >= rb:
        return
    X = (np.arange(ca, cb) + 0.5) * f - 0.5
    Y = (np.arange(ra, rb) + 0.5) * f - 0.5
    X, Y = np.meshgrid(X, Y)
    u = (X * math.cos(phi) + Y * math.sin(phi) - rho) * fg
    t = (-X * math.sin(phi) + Y * math.cos(phi)) * fg
    g = (2 * _smoothstep((u + w) / (2 * w)) - 1) * np.exp(-np.maximum(np.abs(u) - w, 0) / lam)
    along = _smoothstep((t - (sg["t0_m"] - end)) / end) * _smoothstep(((sg["t1_m"] + end) - t) / end)
    C[ra:rb, ca:cb] += (-0.5 * s * g * along).astype(np.float32)


def detect_water_seams(lum: np.ndarray, wo: np.ndarray, res: float, prm: dict) -> list[dict]:
    """Straight brightness STEPS on open water (the orthomosaic's flight-line seamlines).

    Per open-water pixel, over 24 orientations: D = L(p + d n) - L(p - d n) at d = w_det_m on the 6-m low-passed
    luminance L, kept only when monotonic through p (a step, not a wake ridge) and sharp (|D at w_det_m| >=
    w_sharp * |D at w_meas_m|: a smooth glint gradient has ratio ~0.5, a seam ~1). Candidates (|D| >= w_step_min,
    non-maximum suppressed along n) vote in an orientation-constrained Hough transform; each peak's inliers are split
    into runs (gaps <= w_gap_m) and a run becomes a seam when it is >= w_min_len_m long, dense, and >= 70 % of its
    pixels agree on the step sign. step_dn = median signed |D at w_meas_m|. Deterministic (fixed orders, no RNG)."""
    L, _ = _normconv(lum, wo, 6.0 / res)
    rr, cc = np.nonzero(wo)
    if rr.size == 0:
        return []
    P = np.stack([rr, cc]).astype(np.float32)
    wf = wo.astype(np.float32)
    Lp = L[rr, cc]
    d1, d2 = prm["w_det_m"] / res, prm["w_meas_m"] / res
    # step orientation from the gradient of L (normal (cos th, sin th) in (x = col, y = row)), folded to [0, pi)
    gy, gx = np.gradient(ndimage.gaussian_filter(L, max(1.0, 0.5 * d1)))
    bth = (np.arctan2(gy[rr, cc], gx[rr, cc]) % np.pi).astype(np.float32)
    n = np.stack([np.sin(bth), np.cos(bth)])  # (row, col) offsets per pixel

    def at(off, arr=L, order=1):
        return ndimage.map_coordinates(arr, P + off * n, order=order, mode="constant", cval=0.0)
    a1, b1 = at(d1), at(-d1)
    a2, b2 = at(d2), at(-d2)
    ok = (at(d2, wf, 0) > 0) & (at(-d2, wf, 0) > 0) & ((a1 - Lp) * (Lp - b1) > 0)
    bd1, bd2 = (a1 - b1).astype(np.float32), (a2 - b2).astype(np.float32)
    best = np.where(ok, np.abs(bd1), 0).astype(np.float32)
    sharp = np.abs(bd1) / np.maximum(np.abs(bd2), 1e-6)
    cand = (best >= prm["w_step_min"]) & (np.abs(bd2) >= prm["w_step_min"]) & (sharp >= prm["w_sharp"])
    # non-maximum suppression along the step direction
    Bimg = np.zeros(wo.shape, np.float32)
    Bimg[rr, cc] = np.where(cand, best, 0)
    nr, nc = np.sin(bth), np.cos(bth)
    up = ndimage.map_coordinates(Bimg, [rr + nr, cc + nc], order=1)
    dnv = ndimage.map_coordinates(Bimg, [rr - nr, cc - nc], order=1)
    n_pre = int(cand.sum())
    cand &= (best >= up) & (best >= dnv)
    ci = np.flatnonzero(cand)
    if os.environ.get("MOSAIC_DEBUG"):
        log(f"seam detect: open water {rr.size} px, best>=min {(best >= prm['w_step_min']).sum()}, "
            f"candidates {n_pre}, after NMS {ci.size}")
    if ci.size == 0:
        return []
    x, y, th_i, s_i = cc[ci].astype(np.float64), rr[ci].astype(np.float64), bth[ci].astype(np.float64), bd2[ci]
    sh_i = sharp[ci]
    diag = int(math.hypot(*wo.shape)) + 2
    phis = np.deg2rad(np.arange(180))
    deg_i = np.rint(np.rad2deg(th_i)).astype(int)
    alive = np.ones(ci.size, bool)
    segs: list[dict] = []
    min_len, gap = prm["w_min_len_m"] / res, prm["w_gap_m"] / res
    for _ in range(prm["w_max_lines"] * 3):
        if len(segs) >= prm["w_max_lines"] or alive.sum() < min_len * 0.4:
            break
        acc = np.zeros((180, 2 * diag), np.int32)
        idx = np.flatnonzero(alive)
        for dd in range(-4, 5):
            ph = (deg_i[idx] + dd) % 180
            rho = np.rint(x[idx] * np.cos(phis[ph]) + y[idx] * np.sin(phis[ph])).astype(int) + diag
            np.add.at(acc, (ph, rho), 1)
        k = int(np.argmax(acc))
        pk, rk = divmod(k, 2 * diag)
        if os.environ.get("MOSAIC_DEBUG"):
            log(f"  peak phi={pk} rho={rk - diag} votes={acc[pk, rk]}")
        if acc[pk, rk] < min_len * 0.4:
            break
        phi, rho0 = phis[pk], rk - diag
        dist = x * math.cos(phi) + y * math.sin(phi) - rho0
        dang = np.abs(((th_i - phi) + np.pi / 2) % np.pi - np.pi / 2)
        # one seam = a plateau of candidates up to +-d1 wide around the step: take them all, re-centre the line
        inl = alive & (np.abs(dist) <= d1 + 1.5) & (dang <= np.deg2rad(12))
        alive &= ~inl
        ii = np.flatnonzero(inl)
        if ii.size == 0:
            continue
        rho0 = rho0 + float(np.median(dist[ii]))
        t = -x[ii] * math.sin(phi) + y[ii] * math.cos(phi)
        o = np.argsort(t, kind="stable")
        ii, t = ii[o], t[o]
        breaks = np.flatnonzero(np.diff(t) > gap) + 1
        for run in np.split(np.arange(ii.size), breaks):
            if run.size < 2:
                continue
            t0, t1 = t[run[0]], t[run[-1]]
            if t1 - t0 < min_len or run.size < prm["w_density"] * (t1 - t0):
                continue
            sgn = np.sign(np.cos(th_i[ii[run]] - phi))
            sv = s_i[ii[run]] * sgn
            if max((sv > 0).mean(), (sv < 0).mean()) < 0.7:
                continue
            step = float(np.median(sv))
            shp = float(np.median(sh_i[ii[run]]))  # ~1: sharp seam -> narrow ramp; soft seam -> w_ramp_m
            p0 = (rho0 * math.cos(phi) - t0 * math.sin(phi), rho0 * math.sin(phi) + t0 * math.cos(phi))
            p1 = (rho0 * math.cos(phi) - t1 * math.sin(phi), rho0 * math.sin(phi) + t1 * math.cos(phi))
            segs.append({"phi": phi, "rho": float(rho0), "t0_m": float(t0 * res), "t1_m": float(t1 * res),
                         "p0": [float(p0[0]), float(p0[1])], "p1": [float(p1[0]), float(p1[1])],
                         "step_dn": round(step, 2), "length_m": round(float((t1 - t0) * res), 1),
                         "support_px": int(run.size), "angle_deg": int(pk), "sharpness": round(shp, 2),
                         "ramp_m": prm["w_det_m"] if shp >= 0.9 else prm["w_ramp_m"]})
    return segs


def _ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    g, n = green.astype(np.float32), nir.astype(np.float32)
    return (g - n) / np.maximum(g + n, 1.0)


def _is_water(green: np.ndarray, nir: np.ndarray, prm: dict) -> np.ndarray:
    """Dark-NIR water or (sun-glint water, where NIR is high too) NDWI = (G - NIR) / (G + NIR) above ndwi_min."""
    return (nir < prm["nir_lo"]) | (_ndwi(green, nir) > prm["ndwi_min"])


def _water_weight(green: np.ndarray, nir: np.ndarray, prm: dict) -> np.ndarray:
    """Full-res soft water weight in [0, 1]: docks, boats, roofs, vegetation -> 0."""
    a = np.clip((prm["nir_hi"] - nir.astype(np.float32)) / (prm["nir_hi"] - prm["nir_lo"]), 0, 1)
    b = np.clip((_ndwi(green, nir) - prm["ndwi_lo"]) / (prm["ndwi_min"] - prm["ndwi_lo"]), 0, 1)
    return np.maximum(a, b)


def _water_full(x0: float, y1: float, w: int, h: int, nir: np.ndarray, green: np.ndarray) -> np.ndarray | None:
    """(C, h, w) additive water correction for a window, weighted by the pixel's own NIR/NDWI (docks, boats: 0)."""
    wf, prm = _G.get("water"), _G["prm"]
    if wf is None:
        return None
    gsd = prm["gsd"]
    m = _sample(wf["W"], wf["x0"], wf["y1"], wf["res"], x0, y1, w, h, gsd, "constant")
    if not (m > 0.5).any():
        return None
    wt = _water_weight(green, nir, prm) * (m > 0.5)
    return np.stack([_sample(c, wf["x0"], wf["y1"], wf["res"], x0, y1, w, h, gsd, "nearest") * wt
                     for c in wf["C"]])


# ---------------------------------------------------------------- blocks (full resolution)
_G: dict = {}  # set by init_globals in the parent before forking workers


def init_globals(*, year: int, out_dir: Path, tmp_dir: Path, paths: dict, lr: dict, luts: dict, fields: dict,
                 dates: dict, prm: dict = PARAMS, cells: set | None = None, water: dict | None = None) -> None:
    """cells: the planned block cells {(i, j)}; enables the fade to the raw composite at the coverage edge.
    water: water_field(...) result (None: no water flattening)."""
    _G.clear()
    _G.update({"year": year, "out_dir": Path(out_dir), "tmp_dir": Path(tmp_dir), "paths": dict(paths), "luts": luts,
               "fields": fields, "dates": dict(dates), "prm": dict(prm), "cells": set(cells) if cells else None,
               "water": water if prm.get("water") else None,
               "lr_d": {s: {"c0": v["c0"], "r0": v["r0"], "d": v["d"]} for s, v in lr.items()}})


def _sample(arr: np.ndarray, gx0: float, gy1: float, gres: float, x0: float, y1: float, w: int, h: int,
            gsd: float, mode: str, cval: float = 0.0) -> np.ndarray:
    """Bilinear sample of a north-up grid (origin gx0, gy1, pixel gres) at the pixel centres of a window."""
    cc = ((x0 + (np.arange(w) + 0.5) * gsd) - gx0) / gres - 0.5
    rr = (gy1 - (y1 - (np.arange(h) + 0.5) * gsd)) / gres - 0.5
    R, C = np.meshgrid(rr.astype(np.float32), cc.astype(np.float32), indexing="ij")
    return ndimage.map_coordinates(arr, [R, C], order=1, mode=mode, cval=cval)


def _d_full(s: str, x0: float, y1: float, w: int, h: int) -> np.ndarray:
    A, fg = _G["lr_d"][s], _G["prm"]["fit_gsd"]
    return _sample(A["d"], A["c0"] * fg, -A["r0"] * fg, fg, x0, y1, w, h, _G["prm"]["gsd"], "constant")


def _corr_full(s: str, x0: float, y1: float, w: int, h: int) -> np.ndarray | None:
    f = _G["fields"].get(s)
    if f is None:
        return None
    return np.stack([_sample(f["arr"][k], f["x0"], f["y1"], f["res"], x0, y1, w, h, _G["prm"]["gsd"], "nearest")
                     for k in range(3)])


def _edge_beta(i: int, j: int, x0: float, y1: float, w: int, h: int) -> np.ndarray | None:
    """Smoothstep(distance to the outside of the planned block cells / edge_blend_m): 0 at the coverage edge, 1 inside.
    None when every neighbour cell is planned (no edge nearby) or edge blending is off."""
    cells, prm = _G.get("cells"), _G["prm"]
    L = prm.get("edge_blend_m", 0.0)
    if not cells or L <= 0:
        return None
    bm = prm["block_px"] * prm["gsd"]
    gsd = prm["gsd"]
    xs = (x0 + (np.arange(w) + 0.5) * gsd)[None, :]
    ys = (y1 - (np.arange(h) + 0.5) * gsd)[:, None]
    dmin = None
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if (di, dj) == (0, 0) or (i + di, j + dj) in cells:
                continue
            rx0, rx1, ry0, ry1 = (i + di) * bm, (i + di + 1) * bm, (j + dj) * bm, (j + dj + 1) * bm
            dx = np.maximum(np.maximum(rx0 - xs, 0), xs - rx1)
            dy = np.maximum(np.maximum(ry0 - ys, 0), ys - ry1)
            d = np.hypot(dx, dy)
            dmin = d if dmin is None else np.minimum(dmin, d)
    if dmin is None:
        return None
    t = np.clip(dmin / L, 0, 1).astype(np.float32)
    return t * t * (3 - 2 * t)


def blend_window(stack: list[tuple[np.ndarray, np.ndarray, np.ndarray]], feather_px: int, gsd: float,
                 beta: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """stack: [(rgb float32 (3,h,w), mask bool (h,w), d float32 (h,w))] in provider priority order
    -> (uint8 (3,h,w), valid (h,w), normalised weights (S,h,w)).
    beta (h,w) in [0,1]: 1 = feathered weights, 0 = raw composite weights (first valid in stack order)."""
    T = feather_px * gsd
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
    if beta is not None:
        taken = np.zeros(D.shape[1:], bool)
        Wr = np.zeros_like(W)
        for k, (_, m, _) in enumerate(stack):
            take = m & ~taken
            Wr[k] = take
            taken |= take
        tot0 = np.maximum(W.sum(0), 1e-6)
        W = np.where(Wr.sum(0) > 0, beta * (W / tot0) + (1 - beta) * Wr, W)
    tot = W.sum(0)
    acc = np.zeros(stack[0][0].shape, dtype=np.float32)
    for k, (rgb, _, _) in enumerate(stack):
        acc += rgb * W[k][None]
    valid = tot > 1e-6
    out = np.zeros(acc.shape, dtype=np.uint8)
    out[:, valid] = np.clip(np.rint(acc[:, valid] / tot[valid]), 1, 255).astype(np.uint8)
    return out, valid, W / np.maximum(tot, 1e-6)[None]


def iter_block(blk: dict, mode: str = "full", extra: bool = False):
    """Yield (window, px uint8 (3,h,w), valid, info) over a block's processing windows.

    mode: "raw" (provider order, first valid pixel wins: the current tile composite), "feather" (LUT + feathered
    seams, no dodge) or "full" (LUT + dodge + feather). info["share"]: contributing pixels per scene; with
    extra=True also "src" (index of the dominant scene, -1 none), "cov" (number of valid scenes), "nir" (raw band 4)."""
    prm = _G["prm"]
    gsd, bpx, win_px = prm["gsd"], prm["block_px"], prm["win"]
    bm = bpx * gsd
    i, j, sids = blk["i"], blk["j"], blk["scenes"]
    x0, y1 = i * bm, (j + 1) * bm
    tr = from_origin(x0, y1, gsd, gsd)
    with rasterio.Env(**GDAL_ENV):
        srcs = [rasterio.open(_G["paths"][s]) for s in sids]
        vrts = [WarpedVRT(s, crs=MEASURE_CRS, transform=tr, width=bpx, height=bpx,
                          resampling=Resampling.bilinear, nodata=0, warp_mem_limit=256) for s in srcs]
        try:
            for r in range(0, bpx, win_px):
                for c in range(0, bpx, win_px):
                    ww, wh = min(win_px, bpx - c), min(win_px, bpx - r)
                    win = Window(c, r, ww, wh)
                    wx0, wy1 = x0 + c * gsd, y1 - r * gsd
                    beta = _edge_beta(i, j, wx0, wy1, ww, wh) if mode != "raw" else None
                    stack, used, nir = [], [], None
                    for k, (s, v) in enumerate(zip(sids, vrts)):
                        m = v.read_masks(1, window=win) > 0
                        if not m.any():
                            continue
                        want_w = mode == "full" and _G.get("water") is not None
                        bands = [1, 2, 3, 4] if ((extra or want_w) and v.count >= 4) else [1, 2, 3]
                        raw = v.read(indexes=bands, window=win)
                        snir = raw[3] if raw.shape[0] == 4 else None
                        if extra and snir is not None:
                            nir = np.where(m, snir, 0) if nir is None else np.where((nir == 0) & m, snir, nir)
                        raw = raw[:3]
                        if mode == "raw":
                            stack.append((raw, m, None)); used.append(k)
                            continue
                        lut = _G["luts"][s]
                        rgb = np.stack([lut[b][raw[b]] for b in range(3)])
                        if mode == "full" and prm["dodge"]:
                            corr = _corr_full(s, wx0, wy1, ww, wh)
                            if corr is not None:
                                rgb += corr if beta is None else corr * beta[None]
                        if want_w and snir is not None:
                            wc = _water_full(wx0, wy1, ww, wh, snir, raw[1])
                            if wc is not None:
                                rgb += wc if beta is None else wc * beta[None]
                        stack.append((rgb, m, _d_full(s, wx0, wy1, ww, wh))); used.append(k)
                    info: dict = {"share": {}}
                    if not stack:
                        px, valid = np.zeros((3, wh, ww), np.uint8), np.zeros((wh, ww), bool)
                        src = np.full((wh, ww), -1, np.int16)
                    elif mode == "raw":
                        px = np.zeros((3, wh, ww), np.uint8)
                        valid = np.zeros((wh, ww), bool)
                        src = np.full((wh, ww), -1, np.int16)
                        for k, (raw, m, _) in zip(used, stack):
                            take = m & ~valid
                            px[:, take] = raw[:, take]
                            src[take] = k
                            valid |= take
                            info["share"][sids[k]] = int(take.sum())
                    else:
                        px, valid, W = blend_window(stack, prm["feather_px"], gsd, beta)
                        for k, wk in zip(used, W):
                            info["share"][sids[k]] = int((wk > 0.01).sum())
                        src = np.where(valid, np.array(used, np.int16)[np.argmax(W, 0)], -1).astype(np.int16)
                    if extra:
                        info["src"] = src
                        info["cov"] = (np.sum([m for _, m, _ in stack], axis=0).astype(np.uint8) if stack
                                       else np.zeros((wh, ww), np.uint8))
                        info["nir"] = nir if nir is not None else np.zeros((wh, ww), np.uint8)
                    yield win, px, valid, info
        finally:
            for v in vrts:
                v.close()
            for s in srcs:
                s.close()


def render_block(blk: dict) -> dict:
    """Render one block to its COG (atomic). Returns the block record incl. provenance (scene ids, dates, shares)."""
    t0 = time.time()
    prm = _G["prm"]
    i, j, sids = blk["i"], blk["j"], blk["scenes"]
    out_dir: Path = _G["out_dir"] / "blocks"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"b_{i}_{j}.tif"
    tmp = _G["tmp_dir"] / f"b_{_G['year']}_{i}_{j}_{os.getpid()}.tif"
    bm = prm["block_px"] * prm["gsd"]
    tr = from_origin(i * bm, (j + 1) * bm, prm["gsd"], prm["gsd"])
    tile = min(512, prm["block_px"])
    prof = {"driver": "GTiff", "width": prm["block_px"], "height": prm["block_px"], "count": 3, "dtype": "uint8",
            "crs": MEASURE_CRS, "transform": tr, "tiled": True, "blockxsize": tile, "blockysize": tile,
            "compress": "none", "photometric": "rgb"}
    share = {s: 0 for s in sids}
    nvalid = 0
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(tmp, "w", **prof) as out:
            for win, px, valid, info in iter_block(blk, "full"):
                nvalid += int(valid.sum())
                for s, n in info["share"].items():
                    share[s] += n
                out.write(px, window=win)
                out.write_mask(np.where(valid, 255, 0).astype(np.uint8), window=win)
        total = prm["block_px"] ** 2
        sources = [{"scene_id": s, "date": _G["dates"].get(s) or scene_date(s),
                    "px_share": round(share[s] / max(nvalid, 1), 4)} for s in sids if share[s] > 0]
        res = {"i": i, "j": j, "scenes": sids, "bounds_m": blk["bounds_m"], "sources": sources,
               "valid_frac": round(nvalid / total, 6)}
        if nvalid == 0:
            tmp.unlink(missing_ok=True)
            return {**res, "file": None, "seconds": round(time.time() - t0, 1)}
        part = dst.with_suffix(".part.tif")
        rio_copy(str(tmp), str(part), driver="COG", COMPRESS="JPEG", QUALITY=str(JPEG_QUALITY), BLOCKSIZE=str(tile),
                 OVERVIEW_RESAMPLING="AVERAGE", NUM_THREADS="1", BIGTIFF="IF_SAFER")
        os.replace(part, dst)
        tmp.unlink(missing_ok=True)
    return {**res, "file": f"blocks/{dst.name}", "bytes": dst.stat().st_size, "seconds": round(time.time() - t0, 1)}


def save_fields(fields: dict, out_dir: Path) -> dict:
    """Write each scene's correction field (float16, DN) to fields/<sid>.npz; returns {sid: grid description}."""
    d = Path(out_dir) / "fields"
    d.mkdir(parents=True, exist_ok=True)
    desc = {}
    for s in sorted(fields):
        f = fields[s]
        np.savez_compressed(d / f"{s}.npz", arr=f["arr"].astype(np.float16), x0=f["x0"], y1=f["y1"], res=f["res"])
        desc[s] = {"file": f"fields/{s}.npz", "x0_m": f["x0"], "y1_m": f["y1"], "res_m": f["res"],
                   "shape": list(f["arr"].shape), "max_abs_dn": round(float(np.abs(f["arr"]).max()), 2)}
    return desc


def save_water(wf: dict, out_dir: Path) -> dict:
    p = Path(out_dir) / "fields" / "water.npz"
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, C=wf["C"].astype(np.float16), W=wf["W"].astype(np.uint8), x0=wf["x0"], y1=wf["y1"],
                        res=wf["res"])
    return {"file": "fields/water.npz", "x0_m": wf["x0"], "y1_m": wf["y1"], "res_m": wf["res"],
            "shape": list(wf["C"].shape)}


# ---------------------------------------------------------------- preview (one block, before/after + metrics)
def _down(a: np.ndarray, f: int) -> np.ndarray:
    """Area-mean downsample of (..., H, W) by integer f."""
    *lead, H, W = a.shape
    return a.reshape(*lead, H // f, f, W // f, f).mean((-3, -1))


def _masked_gauss(x: np.ndarray, m: np.ndarray, s: float) -> np.ndarray:
    mf = m.astype(np.float32)
    g = ndimage.gaussian_filter(mf, s)
    return np.where(g > 1e-3, ndimage.gaussian_filter(x * mf, s) / np.maximum(g, 1e-6), 0)


def preview_metrics(img: dict, src: dict, cov: np.ndarray, nir: np.ndarray, gsd: float,
                    nir_water: float = 45.0) -> dict:
    """Metrics on the downsampled arrays (gsd m/px). img[mode]: (3,H,W) float; src[mode]: dominant scene index."""
    lum = {k: v.mean(0) for k, v in img.items()}
    water = ((nir < nir_water) | (_ndwi(img["raw"][1], nir) > PARAMS["ndwi_min"])) & (cov > 0)
    water = ndimage.binary_opening(water, iterations=2)
    lab, n = ndimage.label(water)
    sizes = ndimage.sum(water, lab, index=np.arange(1, n + 1)) if n else np.array([])
    big = np.isin(lab, np.flatnonzero(sizes >= 10000 / gsd ** 2) + 1)  # >= 1 ha
    lake = ndimage.binary_erosion(big, iterations=max(1, int(20 / gsd)))
    ov = cov >= 2
    dist_ov = ndimage.distance_transform_edt(~ov) * gsd if ov.any() else np.full(cov.shape, np.inf)
    sig = 15.0 / gsd
    out: dict = {"gsd_m": gsd, "nir_water_threshold": nir_water, "lake_water_px": int(lake.sum()),
                 "overlap_px": int(ov.sum())}

    def switch_line(s):
        e = np.zeros(s.shape, bool)
        e[:, 1:] |= (s[:, 1:] != s[:, :-1]) & (s[:, 1:] >= 0) & (s[:, :-1] >= 0)
        e[1:, :] |= (s[1:, :] != s[:-1, :]) & (s[1:, :] >= 0) & (s[:-1, :] >= 0)
        return ndimage.binary_dilation(e, iterations=max(1, int(6 / gsd)))

    lines = {"raw_seam": switch_line(src["raw"]), "medial_seam": switch_line(src["full"])}
    ctrl = lake & (dist_ov > 1500)
    # step = |gradient| of the 15-m low-passed luminance x 60 m, on open water only (water is flat: any step is seam)
    for mode, L in lum.items():
        Ls = _masked_gauss(L, lake, sig)
        gy, gx = np.gradient(Ls)
        step = np.hypot(gx, gy) * (60.0 / gsd)
        row = {}
        for nm, ln in lines.items():
            sel = ln & lake
            ok = sel.sum() > 50
            row[f"step_at_{nm}_dn_p50"] = round(float(np.median(step[sel])), 2) if ok else None
            row[f"step_at_{nm}_dn_p90"] = round(float(np.percentile(step[sel], 90)), 2) if ok else None
            row[f"{nm}_water_px"] = int(sel.sum())
        row["control_water_dn_p50"] = round(float(np.median(step[ctrl])), 2) if ctrl.sum() > 50 else None
        # anywhere on open water (> 40 m from shore): the orthomosaic's seamlines are not at scene edges
        ow = ndimage.binary_erosion(lake, iterations=max(1, int(20 / gsd)))
        if ow.sum() > 50:
            for q in (50, 95, 99):
                row[f"open_water_step_dn_p{q}"] = round(float(np.percentile(step[ow], q)), 2)
            row["open_water_step_gt3dn_frac"] = round(float((step[ow] > 3).mean()), 4)
        out[f"water_{mode}"] = row
    # non-water objects: bright land (roofs, docks, concrete) + small water bodies (pools)
    land = (cov > 0) & ~ndimage.binary_dilation(big, iterations=max(1, int(10 / gsd)))
    small_w = water & ~big
    thr = np.percentile(lum["raw"][land], 90) if land.any() else 255
    objects = (land & (lum["raw"] >= thr)) | small_w
    near = dist_ov <= 1500
    same = src["raw"] == src["full"]
    hp = {k: v - np.stack([ndimage.gaussian_filter(v[b], 10.0 / gsd) for b in range(3)]) for k, v in img.items()}

    def stats(sel, a, b):
        if sel.sum() < 50:
            return None
        d = (img[a] - img[b])[:, sel]
        dh = (hp[a] - hp[b])[:, sel]
        return {"px": int(sel.sum()), "mean_abs_dn": round(float(np.abs(d).mean()), 2),
                "mean_signed_dn_rgb": [round(float(x), 2) for x in d.mean(1)],
                "p95_abs_dn": round(float(np.percentile(np.abs(d), 95)), 2),
                "highpass_mean_abs_dn": round(float(np.abs(dh).mean()), 2)}

    out["objects"] = {
        "definition": f"land pixels (>10 m from water >= 1 ha) with raw luminance >= p90 of land ({thr:.0f}; roofs, "
                      "docks, concrete) + water bodies < 1 ha (pools); compared at the preview gsd",
        "dodge_only_full_vs_feather_within_1500m_of_overlap": stats(objects & near, "full", "feather"),
        "dodge_only_full_vs_feather_all": stats(objects, "full", "feather"),
        "full_vs_raw_same_scene_within_1500m_of_overlap": stats(objects & near & same, "full", "raw"),
        "full_vs_raw_same_scene_beyond_1500m": stats(objects & ~near & same, "full", "raw"),
        "all_land_full_vs_raw_same_scene": stats(land & same, "full", "raw"),
    }
    return out


def seam_steps(img: dict, nir: np.ndarray, cov: np.ndarray, segs: list[dict], bx0: float, by1: float, g: float,
               half_m: float = 60.0) -> list[dict]:
    """Per detected seam inside the image: median signed luminance step L(+half_m) - L(-half_m) across the line, on
    water, for each mode (raw / feather / full). L = 6-m low-pass of luminance on water."""
    water = ((nir < 40) | (_ndwi(img["raw"][1], nir) > PARAMS["ndwi_min"])) & (cov > 0)
    out = []
    Ls = {k: _masked_gauss(v.mean(0), water, 6.0 / g) for k, v in img.items()}
    H, W = water.shape
    for sg in segs:
        (xa, ya), (xb, yb) = sg["xy0_m"], sg["xy1_m"]
        L = math.hypot(xb - xa, yb - ya)
        if L <= 0:
            continue
        ts = np.arange(0, L, g)
        px = ((xa + (xb - xa) * ts / L) - bx0) / g - 0.5
        py = (by1 - (ya + (yb - ya) * ts / L)) / g - 0.5
        dc, dr = (xb - xa) / L, -(yb - ya) / L
        nc, nr = -dr, dc
        k = half_m / g
        pa, pb = (py + k * nr, px + k * nc), (py - k * nr, px - k * nc)
        inside = (np.minimum(pa[0], pb[0]) >= 0) & (np.maximum(pa[0], pb[0]) < H - 1) & \
                 (np.minimum(pa[1], pb[1]) >= 0) & (np.maximum(pa[1], pb[1]) < W - 1)
        if inside.sum() < 10:
            continue
        wa = ndimage.map_coordinates(water.astype(np.float32), [pa[0], pa[1]], order=0) > 0
        wb = ndimage.map_coordinates(water.astype(np.float32), [pb[0], pb[1]], order=0) > 0
        sel = inside & wa & wb
        if sel.sum() < 10:
            continue
        row = {"xy0_m": sg["xy0_m"], "xy1_m": sg["xy1_m"], "detected_step_dn": sg["step_dn"], "n": int(sel.sum())}
        for m_, Lm in Ls.items():
            d = ndimage.map_coordinates(Lm, [pa[0], pa[1]], order=1) - ndimage.map_coordinates(Lm, [pb[0], pb[1]], order=1)
            row[f"{m_}_step_dn"] = round(float(np.median(d[sel])), 2)
        out.append(row)
    return out


def preview(blk: dict, out_dir: Path, point_m: tuple[float, float], extra_meta: dict, f: int = 4) -> dict:
    from PIL import Image
    prm = _G["prm"]
    bpx, gsd = prm["block_px"], prm["gsd"]
    out_dir.mkdir(parents=True, exist_ok=True)
    bm = bpx * gsd
    x0, y1 = blk["i"] * bm, (blk["j"] + 1) * bm
    pc, pr = int((point_m[0] - x0) / gsd), int((y1 - point_m[1]) / gsd)
    half = 768
    c0, r0 = min(max(pc - half, 0), bpx - 2 * half), min(max(pr - half, 0), bpx - 2 * half)
    imgs, srcs, timings = {}, {}, {}
    cov = nir = None
    for mode in ("raw", "feather", "full"):
        t = time.time()
        full = np.zeros((3, bpx, bpx), np.uint8)
        src = np.full((bpx, bpx), -1, np.int16)
        cv = np.zeros((bpx, bpx), np.uint8) if mode == "raw" else None
        nr = np.zeros((bpx, bpx), np.uint8) if mode == "raw" else None
        for win, px, v, info in iter_block(blk, mode, extra=True):
            sl = (slice(win.row_off, win.row_off + win.height), slice(win.col_off, win.col_off + win.width))
            full[(slice(None),) + sl] = px
            src[sl] = info["src"]
            if mode == "raw":
                cv[sl] = info["cov"]; nr[sl] = info["nir"]
        timings[mode] = round(time.time() - t, 1)
        crop = full[:, r0:r0 + 2 * half, c0:c0 + 2 * half]
        Image.fromarray(np.ascontiguousarray(np.transpose(crop, (1, 2, 0)))).save(out_dir / f"crop_{mode}.png")
        imgs[mode] = _down(full.astype(np.float32), f)
        Image.fromarray(np.ascontiguousarray(np.transpose(np.clip(np.rint(imgs[mode]), 0, 255).astype(np.uint8),
                                                          (1, 2, 0)))).save(out_dir / f"block_{mode}.png")
        srcs[mode] = src[f // 2::f, f // 2::f]
        if mode == "raw":
            cov = cv[f // 2::f, f // 2::f]
            nir = _down(nr.astype(np.float32), f)
        del full, src
    dif = np.clip(128 + 8 * (imgs["full"].mean(0) - imgs["feather"].mean(0)), 0, 255).astype(np.uint8)
    Image.fromarray(dif).save(out_dir / "dodge_x8.png")  # where the correction acts, x8 around grey
    met = preview_metrics(imgs, srcs, cov, nir, gsd * f)
    met["seam_steps"] = seam_steps(imgs, nir, cov, extra_meta.get("seams") or [], x0, y1, gsd * f)
    from PIL import ImageDraw
    ov = Image.open(out_dir / "block_raw.png").convert("RGB")
    dr = ImageDraw.Draw(ov)
    g = gsd * f
    for sg in extra_meta.get("seams") or []:
        (xa, ya), (xb, yb) = sg["xy0_m"], sg["xy1_m"]
        dr.line([((xa - x0) / g, (y1 - ya) / g), ((xb - x0) / g, (y1 - yb) / g)], fill=(255, 0, 255), width=3)
    ov.save(out_dir / "seams_detected.png")
    water_nir = nir[(cov > 0)]
    met.update({"block": [blk["i"], blk["j"]], "scenes": blk["scenes"],
                "dates": {s: _G["dates"].get(s, "") for s in blk["scenes"]}, "render_s": timings,
                "nir_percentiles_p1_p5_p25_p50": [round(float(np.percentile(water_nir, q)), 1) for q in (1, 5, 25, 50)],
                "crop_px_origin": [c0, r0], "crop_px": 2 * half, "params": prm, **extra_meta})
    (out_dir / "metrics.json").write_text(json.dumps(met, indent=1, default=float))
    return met


# ---------------------------------------------------------------- main
def main() -> None:
    from groundtruth import config
    from groundtruth.providers.public import PublicDataProvider

    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--version", default="", help="output folder data/tiles/mosaic/<version>/ (never an existing one)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--fetch", action="store_true", help="download missing scene COGs (public, no key)")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--out-root", default="", help="default: data/tiles/mosaic (preview: ~/mosaic_preview)")
    ap.add_argument("--areas", default="", help="comma-separated place names (default: every place)")
    ap.add_argument("--preview", default="", help="'lon,lat': render only that block raw/feather/full + metrics")
    ap.add_argument("--set", action="append", default=[], help="override a PARAMS entry, e.g. --set tau_m=2000")
    a = ap.parse_args()
    prm = dict(PARAMS)
    for kv in a.set:
        k, v = kv.split("=", 1)
        prm[k] = (v.lower() in ("1", "true", "yes")) if isinstance(PARAMS[k], bool) else type(PARAMS[k])(v)
    t_start = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    p = PublicDataProvider(download_scenes=a.fetch)
    areas = load_areas()
    if a.areas:
        want = [x.strip() for x in a.areas.split(",")]
        unknown = [x for x in want if x not in areas]
        if unknown:
            raise SystemExit(f"unknown places: {unknown}")
        areas = {k: v for k, v in areas.items() if k in want}
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
    if a.preview:
        out_root = Path(a.out_root or Path.home() / "mosaic_preview")
        out_dir = out_root / "scratch"
    else:
        if not a.version:
            raise SystemExit("--version is required for a build")
        out_root = Path(a.out_root or config.DATA / "tiles" / "mosaic") / a.version
        out_dir = out_root / str(a.year)
        if (out_dir / "meta.json").exists():
            raise SystemExit(f"{out_dir} already built; versions are never overwritten")
        (out_dir / "blocks").mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(os.environ.get("MOSAIC_TMP", Path.home() / ".cache" / "groundtruth" / "mosaic_tmp"))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    paths = {s: str(p.local_scene_path(s, a.year)) for s in pl["scenes"]}

    t = time.time()
    jobs = [(s, paths[s], pl["footprints"][s].bounds, prm["fit_gsd"]) for s in pl["scenes"]]
    with mp.get_context("fork").Pool(a.workers) as pool:
        lr = dict(pool.map(_read_lowres_job, jobs))
    log(f"low-res reads {time.time() - t:.0f} s")
    t = time.time()
    f = fit(lr, pl["scenes"], pl["reference"], prm)
    qb = [x["quantile_rms_dn"] for x in f["pairs"]] or [0.0]
    log(f"fit {time.time() - t:.0f} s: {len(f['pairs'])} pairs, overlap quantile RMS median {np.median(qb):.2f} DN, "
        f"max {max(qb):.2f} (global colour: {prm['global_colour']})")
    t = time.time()
    fields, dstats = dodge_fields(lr, f["luts"], pl["scenes"], prm) if prm["dodge"] else ({}, {})
    if dstats:
        log(f"dodge {time.time() - t:.0f} s: {len(fields)} fields, overlap |diff| p50 (median over scenes) "
            f"{np.median([v['overlap_diff_dn_p50'] for v in dstats.values()]):.2f} DN, correction max "
            f"{max(v['correction_dn_max'] for v in dstats.values()):.2f} DN")
    wf = None
    if prm["water"]:
        t = time.time()
        bb = np.array([b["bounds_m"] for b in pl["blocks"]])
        wf = water_field(lr, pl["scenes"], (bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max()), prm)
        log(f"water field {time.time() - t:.0f} s: {wf['stats'] if wf else 'no water'}")
    init_globals(year=a.year, out_dir=out_dir, tmp_dir=tmp_dir, paths=paths, lr=lr, luts=f["luts"], fields=fields,
                 dates=pl["dates"], prm=prm, cells={(b["i"], b["j"]) for b in pl["blocks"]}, water=wf)
    del lr

    if a.preview:
        from shapely.geometry import Point

        from groundtruth.geo import to_measure
        pt = to_measure(Point(*map(float, a.preview.split(","))))
        bm = prm["block_px"] * prm["gsd"]
        blk = next(b for b in pl["blocks"] if b["i"] == math.floor(pt.x / bm) and b["j"] == math.floor(pt.y / bm))
        od = out_root / f"preview_{a.year}_{blk['i']}_{blk['j']}"
        met = preview(blk, od, (pt.x, pt.y), {"dodge_stats": {s: dstats.get(s) for s in blk["scenes"]},
                                               "water_field": wf["stats"] if wf else None,
                                               "seams": wf["segments"] if wf else [],
                                               "pairs": [x for x in f["pairs"] if x["a"] in blk["scenes"]
                                                         and x["b"] in blk["scenes"]]})
        log(f"preview -> {od}")
        print(json.dumps({k: met[k] for k in met if k.startswith("water_") or k in (
            "objects", "render_s", "nir_percentiles_p1_p5_p25_p50", "seam_steps", "water_field")}, indent=1))
        return

    field_desc = save_fields(fields, out_dir)
    blocks = pl["blocks"]
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
        "build_version": BUILD_VERSION, "version": a.version, "year": a.year, "crs": MEASURE_CRS, "gsd_m": prm["gsd"],
        "partial": False,
        "grid": {"block_px": prm["block_px"], "block_m": prm["block_px"] * prm["gsd"], "origin_m": [0.0, 0.0],
                 "rule": "block (i, j) covers x in [i*block_m, (i+1)*block_m), y in [j*block_m, (j+1)*block_m)"},
        "format": {"bands": "RGB", "compress": "JPEG", "photometric": "YCbCr", "quality": JPEG_QUALITY,
                   "tile_px": 512, "mask": "internal", "overviews": "AVERAGE"},
        "blend": {"method": "medial-line feather: smoothstep of (d_i - max_j d_j) over feather_px; fades to the raw "
                            "composite within edge_blend_m of the coverage edge",
                  "feather_px": prm["feather_px"], "distance_grid_m": prm["fit_gsd"],
                  "edge_blend_m": prm["edge_blend_m"]},
        "colour": {"global_colour": prm["global_colour"], "reference": pl["reference"],
                   "reference_rule": "largest area inside the padded areas (ties: scene id); fixed at gain 1, offset 0",
                   "reference_area_in_areas_km2": round(pl["inside_m2"][pl["reference"]] / 1e6, 3),
                   "gains": f["gains"], "offsets": f["offsets"], "pairs": f["pairs"],
                   "quantile_rms_dn": {"median": round(float(np.median(qb)), 2), "max": round(max(qb), 2)}},
        "dodge": {"enabled": prm["dodge"],
                  "method": "additive per-scene field: low-pass (sigma sig_l_m) of clip(mean of covering scenes - "
                            "scene) on overlap cores, extended (sigma sig_e_m), tapered exp(-0.5 (d/tau_m)^2)",
                  "params": {k: prm[k] for k in ("sig_l_m", "sig_e_m", "tau_m", "cmax", "diff_clip", "edge_m",
                                                 "field_f", "fit_gsd")},
                  "fields": field_desc, "stats": dstats},
        "water": ({"enabled": True,
                   "method": "straight luminance steps detected on open water (NIR < nir_lo, bodies >= w_min_ha, "
                             ">= w_shore_m from shore) are cancelled by a smooth ramp (+-step/2 across +-w_ramp_m, "
                             "decaying over w_lambda_m); same offset on R, G, B; full-res weight from each pixel's "
                             "own NIR (land, docks, boats, pools: 0)",
                   "why": "NAIP quarter-quads share identical pixels in their overlaps; the visible seams are the "
                          "state orthomosaic's flight-line seamlines inside scenes, visible on water",
                   "params": {k: v for k, v in prm.items() if k.startswith("w_") or k.startswith("nir_")},
                   "seams": wf["segments"], "field": save_water(wf, out_dir), "stats": wf["stats"]}
                  if wf else {"enabled": False}),
        "areas": {"source": "engine/groundtruth/conf/places.json", "pad_m": PAD_M, "bbox_wgs84": areas,
                  "region_km2": round(pl["region"].area / 1e6, 1)},
        "scenes": [{"scene_id": s, "date": pl["dates"][s]} for s in pl["scenes"]],
        "blocks": [{k: r[k] for k in ("i", "j", "file", "bounds_m", "sources", "valid_frac", "scenes")} for r in built],
        "empty_blocks": [[r["i"], r["j"]] for r in results if not r["file"]],
        "build": {"started": started, "seconds": round(time.time() - t_start, 1),
                  "block_seconds_total": round(sum(r["seconds"] for r in results), 1), "workers": a.workers,
                  "host": socket.gethostname(), "bytes": total_bytes, "params": prm,
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "gdal": rasterio.__gdal_version__, "rasterio": rasterio.__version__, "numpy": np.__version__},
    }
    if len(built) + len(meta["empty_blocks"]) != len(blocks):
        meta["partial"] = f"{len(blocks) - len(built) - len(meta['empty_blocks'])} planned blocks missing"
    tmpm = out_dir / "meta.json.tmp"
    tmpm.write_text(json.dumps(meta, indent=1))
    os.replace(tmpm, out_dir / "meta.json")
    log(f"done {a.year}: {len(built)} blocks, {total_bytes / 2**30:.2f} GB, {time.time() - t_start:.0f} s")


if __name__ == "__main__":
    main()
