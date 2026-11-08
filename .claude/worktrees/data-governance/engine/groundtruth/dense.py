"""Dense mode (Phase 2): higher recall for colour concepts in the demo neighbourhoods.

Build (offline, per place; scripts/dense_build.py → data/dense/<place>/<concept>_<year>/):
  1. Read the place raster at 0.6 m (EPSG:32617) from the local COGs.
  2. Proposals = connected blobs of the concept colour on the full-resolution raster (area range from config). Each is
     re-tiled into a 224 px window CENTRED on it (50%-stride re-tiling generalised: no object is cut by a chip edge).
  3. Per proposal: DINOv2 patch token (the most concept-coloured patch of the 3×3 around the centre), SAM 2.1 outline
     (prompted by the blob box + centre point), and the object check on the colour-refined outline (same verifier as
     standard mode: area, compactness, colour fraction / area).
Query (find_all with dense, cheap): proposals that pass the verifier AND match the picked examples under the same rule
as the standard scan (colour ≥ colour_min, similarity ≥ sim_min, closer to positives than to the "not this" negatives),
merged with the standard scan's detections, deduped by distance (nms_m).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from shapely.geometry import Point, box, mapping, shape

from groundtruth import config
from groundtruth.geo import SQM_PER_ACRE, to_measure, to_wgs84
from groundtruth.rerank import _hsv, concept_conf

DENSE_VERSION = "dense-v1"
W = config.CHIP_PX  # 224
PATCH = W // 16  # 14 px


def dense_dir(place: str, concept: str, year: int, root: Path | None = None) -> Path:
    """Read path (current data version) unless a root is given (a rebuild writes to config.next_data_dir("dense"))."""
    return (root or config.data_dir("dense")) / place.replace(" ", "_") / f"{concept}_{year}"


def available(place: str | None, concept: str | None, year: int) -> bool:
    return bool(place and concept) and (dense_dir(place, concept, year) / "meta.json").exists()


def _colour_mask(rgb: np.ndarray, rule: dict[str, Any], strip: int = 1024) -> np.ndarray:
    lo, hi = np.array(rule["hsv_lo"], np.float32), np.array(rule["hsv_hi"], np.float32)
    out = np.zeros(rgb.shape[:2], bool)
    for r in range(0, rgb.shape[0], strip):
        h = _hsv(rgb[r:r + strip])
        out[r:r + strip] = np.all((h >= lo) & (h <= hi), -1)
    return out


def build(eng, place: str, concept: str = "swimming_pool", log: Callable[[str], None] = print,
          batch: int = 64, out_root: Path | None = None) -> dict[str, Any]:
    from groundtruth.outline import Outliner

    t0 = time.perf_counter()
    cc = concept_conf(concept)
    rule = cc["rerank"].get("colour")
    if not rule:
        raise ValueError(f"dense mode needs a concept colour; {concept!r} has none")
    oc, dc = cc.get("object_check", {}), cc.get("dense", {})
    bb = eng.place_bbox(place)
    g = config.GSD_M
    mx = to_measure(box(*bb)).bounds
    # blocks of block_m (default 1.8 km) so memory stays flat for big places; each blob belongs to the block whose
    # core holds its centroid, and every block is read with a 140 m margin (> half a window + the largest blob)
    bm = float(dc.get("block_m", 1800.0))
    xs = list(np.arange(mx[0], mx[2], bm)) or [mx[0]]
    ys = list(np.arange(mx[1], mx[3], bm)) or [mx[1]]
    outl = Outliner()
    all_props, all_toks, all_col, all_feats = [], [], [], []
    n_blobs, t_read, t_emb, t_sam, raster_px = 0, 0.0, 0.0, 0.0, 0
    a_lo = float(dc.get("blob_area_min_m2", oc.get("colour_area_min_m2", 6)))
    a_hi = float(dc.get("blob_area_max_m2", 1.5 * oc.get("area_m2", [0, 2000])[1]))
    for bx in xs:
        for by in ys:
            core = (bx, by, min(bx + bm, mx[2]), min(by + bm, mx[3]))
            r = _block(eng, rule, oc, core, a_lo, a_hi, outl, batch)
            all_props += r["props"]; all_toks.append(r["toks"]); all_col.append(r["colp"]); all_feats += r["feats"]
            n_blobs += r["n"]; t_read += r["t_read"]; t_emb += r["t_emb"]; t_sam += r["t_sam"]; raster_px += r["px"]
            log(f"  block {core[0]:.0f},{core[1]:.0f}: {r['n']} blobs → {len(r['props'])} proposals "
                f"(total {len(all_props)}; read {t_read:.0f} s, embed {t_emb:.0f} s, SAM+verify {t_sam:.0f} s)")
    props, feats = all_props, all_feats
    toks = np.concatenate(all_toks) if all_toks else np.zeros((0, 768), np.float16)
    colp = np.concatenate(all_col) if all_col else np.zeros(0, np.float32)
    n = n_blobs
    out = dense_dir(place, concept, eng.year, out_root or config.next_data_dir("dense"))  # never the version in use
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "proposals.npz", tokens=toks, colour=colp,
                        x_m=np.array([p["x_m"] for p in props]), y_m=np.array([p["y_m"] for p in props]),
                        passed=np.array([f["properties"]["object_check"]["pass"] for f in feats], bool))
    (out / "outlines.json").write_text(json.dumps(feats))
    meta = {"version": DENSE_VERSION, "place": place, "concept": concept, "year": eng.year, "bbox": list(bb),
            "raster_mp": round(raster_px / 1e6, 1), "blocks": len(xs) * len(ys), "block_m": bm,
            "colour_blobs": int(n), "proposals": len(props),
            "passed_verifier": int(sum(f["properties"]["object_check"]["pass"] for f in feats)),
            "blob_area_m2": [a_lo, a_hi], "ms_read": round(t_read * 1000), "ms_embed": round(t_emb * 1000),
            "ms_sam_verify": round(t_sam * 1000), "ms_total": round((time.perf_counter() - t0) * 1000),
            "device": outl.device_name, "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "concept_conf": cc}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    log(f"{place}: {meta['proposals']} proposals, {meta['passed_verifier']} pass the verifier; {meta['ms_total'] / 1000:.0f} s")
    return meta


def _block(eng, rule, oc, core, a_lo: float, a_hi: float, outl, batch: int) -> dict[str, Any]:
    """Proposals whose blob centroid lies in ``core`` (EPSG:32617): read core + 140 m, colour blobs, centred windows →
    DINOv2 token + SAM outline + verifier."""
    from scipy import ndimage

    from groundtruth.rerank import dino_tokens

    g = config.GSD_M
    m = 140.0
    t0 = time.perf_counter()
    x0, y0 = np.floor((core[0] - m) / g) * g, np.floor((core[1] - m) / g) * g
    x1, y1 = np.ceil((core[2] + m) / g) * g, np.ceil((core[3] + m) / g) * g
    im = eng.provider.read_grid((x0, y0, x1, y1), eng.year)
    rgb, valid = im.rgb, im.meta["valid"]
    t_read = time.perf_counter() - t0
    mx = core
    # 2. colour blobs
    cmask = _colour_mask(rgb, rule) & valid
    lab, n = ndimage.label(cmask, structure=np.ones((3, 3)))
    px_area = g * g
    sizes = ndimage.sum_labels(np.ones_like(lab, np.float32), lab, index=np.arange(1, n + 1)) * px_area
    keep = [k + 1 for k in range(n) if a_lo <= sizes[k] <= a_hi]
    objs = ndimage.find_objects(lab)
    props = []
    for lbl in keep:
        sl = objs[lbl - 1]
        rr, cc_ = np.nonzero(lab[sl] == lbl)
        r_c, c_c = sl[0].start + rr.mean(), sl[1].start + cc_.mean()
        # centroid in this block's core only (half-open, so a blob on a core edge is counted once)
        xm, ym = x0 + (c_c + 0.5) * g, y1 - (r_c + 0.5) * g
        if not (mx[0] <= xm < mx[2] and mx[1] <= ym < mx[3]):
            continue
        props.append({"r": float(r_c), "c": float(c_c), "x_m": float(xm), "y_m": float(ym), "blob_m2": float(sizes[lbl - 1]),
                      "bbox_px": [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop]})
    del lab
    # 3. centred windows → DINOv2 token + SAM outline + verifier
    pad = W
    rgbp = np.pad(rgb, ((pad, pad), (pad, pad), (0, 0)))
    cmp_ = np.pad(cmask, ((pad, pad), (pad, pad)))
    toks = np.zeros((len(props), 768), np.float16)
    colp = np.zeros(len(props), np.float32)
    feats = []
    t_emb = t_sam = 0.0
    for k0 in range(0, len(props), batch):
        chunk = props[k0:k0 + batch]
        wins, cwins = [], []
        for p in chunk:
            r0, c0 = int(round(p["r"])) - W // 2 + pad, int(round(p["c"])) - W // 2 + pad
            p["win"] = (r0 - pad, c0 - pad)
            wins.append(rgbp[r0:r0 + W, c0:c0 + W])
            cwins.append(cmp_[r0:r0 + W, c0:c0 + W])
        te = time.perf_counter()
        T = dino_tokens(np.stack(wins)).float().cpu().numpy()  # (b, 256, 768)
        t_emb += time.perf_counter() - te
        for j, p in enumerate(chunk):
            cw = cwins[j]
            cp = cw.reshape(16, PATCH, 16, PATCH).mean((1, 3))  # colour fraction per patch
            best, bv = 8 * 16 + 8, -1.0
            for pr in (7, 8):
                for pc in (7, 8):
                    if cp[pr, pc] > bv:
                        bv, best = float(cp[pr, pc]), pr * 16 + pc
            toks[k0 + j] = T[j, best].astype(np.float16)
            colp[k0 + j] = bv
            ts = time.perf_counter()
            feats.append(_outline_one(outl, wins[j], cw, p, x0, y1, oc))
            t_sam += time.perf_counter() - ts
    return {"props": props, "toks": toks, "colp": colp, "feats": feats, "n": int(len(keep)), "t_read": t_read,
            "t_emb": t_emb, "t_sam": t_sam, "px": int(rgb.shape[0] * rgb.shape[1])}


def _outline_one(outl, win: np.ndarray, cwin: np.ndarray, p: dict[str, Any], x0: float, y1: float,
                 oc: dict[str, Any]) -> dict[str, Any]:
    """SAM outline of the proposal at the window centre, colour-refined, + verifier → GeoJSON Feature (EPSG:4326)."""
    g = config.GSD_M
    wr, wc = p["win"]
    bx = [p["bbox_px"][0] - wc, p["bbox_px"][1] - wr, p["bbox_px"][2] - wc, p["bbox_px"][3] - wr]
    m = 6
    bx = [max(0, bx[0] - m), max(0, bx[1] - m), min(W, bx[2] + m), min(W, bx[3] + m)]
    mask, score = outl.segment(win, bx, [W / 2, W / 2])
    tr = from_origin(x0 + wc * g, y1 - wr * g, g, g)
    centre = Point(x0 + (wc + W / 2) * g, y1 - (wr + W / 2) * g)
    dil = cwin.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        dil |= np.roll(np.roll(cwin, dy, 0), dx, 1)
    ref = mask & dil
    sam_polys = [shape(gm) for gm, v in rfeatures.shapes(mask.astype(np.uint8), mask=mask, transform=tr) if v == 1]
    polys = [shape(gm) for gm, v in rfeatures.shapes(ref.astype(np.uint8), mask=ref, transform=tr) if v == 1]
    sam_area = max((q.area for q in sam_polys), default=0.0)
    ident = f"dense:{p['x_m']:.1f},{p['y_m']:.1f}"
    base = {"example": ident, "dense": True, "x_m": round(p["x_m"], 2), "y_m": round(p["y_m"], 2),
            "sam_score": round(score, 4), "sam_area_m2": round(sam_area, 1), "blob_m2": round(p["blob_m2"], 1)}
    if not polys:
        chk = {"pass": False, "reason": "no colour inside the SAM outline"}
        return {"type": "Feature", "id": ident, "geometry": None, "properties": {**base, "object_check": chk}}
    best = min(polys, key=lambda q: (q.distance(centre), -q.area)).buffer(0).simplify(0.3)
    area, per = best.area, best.length
    comp = 4 * np.pi * area / max(per * per, 1e-9)
    from rasterio.features import rasterize

    rmask = rasterize([best], out_shape=cwin.shape, transform=tr) > 0
    inside = cwin[rmask] if rmask.any() else np.zeros(1, bool)
    cfrac, carea = float(inside.mean()), float(inside.sum()) * g * g
    lo_a, hi_a = oc.get("area_m2", [0, 1e12])
    reasons = []
    if not (lo_a <= area <= hi_a):
        reasons.append(f"area {area:.0f} m² outside {lo_a}–{hi_a}")
    if comp < oc.get("compactness_min", 0):
        reasons.append(f"compactness {comp:.2f} < {oc['compactness_min']}")
    if cfrac < oc.get("colour_frac_min", 0):
        reasons.append(f"colour {cfrac:.2f} < {oc['colour_frac_min']}")
    if carea < oc.get("colour_area_min_m2", 0):
        reasons.append(f"colour area {carea:.0f} m² < {oc['colour_area_min_m2']}")
    chk = {"pass": not reasons, "reason": "; ".join(reasons) or "ok", "area_m2": round(area, 1),
           "compactness": round(float(comp), 3), "colour_frac": round(cfrac, 3), "colour_area_m2": round(carea, 1),
           "refined": True}
    return {"type": "Feature", "id": ident, "geometry": mapping(to_wgs84(best)),
            "properties": {**base, "object_check": chk, "area_m2": round(area, 2),
                           "area_acres": round(area / SQM_PER_ACRE, 5), "perimeter_m": round(per, 2),
                           "compactness": round(float(comp), 3), "geometry_kind": "outline"}}


class DenseSet:
    """Loaded dense proposals for one place/concept/year (small: tokens n×768 fp16)."""

    def __init__(self, place: str, concept: str, year: int) -> None:
        d = dense_dir(place, concept, year)
        z = np.load(d / "proposals.npz")
        self.tokens = z["tokens"].astype(np.float32)
        self.colour, self.x_m, self.y_m, self.passed = z["colour"], z["x_m"], z["y_m"], z["passed"]
        self.feats = json.loads((d / "outlines.json").read_text())
        self.meta = json.loads((d / "meta.json").read_text())

    def match(self, P: np.ndarray, N: np.ndarray | None, qp: np.ndarray, det: dict[str, Any]) -> list[int]:
        """Indices of proposals that pass the verifier and match the examples (same rule as the standard scan)."""
        S = self.tokens @ qp
        from groundtruth.rerank import concept_conf

        cc = concept_conf(self.meta["concept"])
        dc = cc.get("dense", {})  # current config (not the build-time copy)
        passed = self.passed_with({**cc.get("object_check", {}), **dc.get("object_check", {})})
        m = passed & (S >= float(dc.get("sim_min", det.get("sim_min", det.get("score_min", 0)))))
        if dc.get("require_patch_colour", False):  # proposals are colour blobs already; the patch fraction of a small
            m &= self.colour >= det.get("colour_min", 0)  # pool is ~0.1, so this check is off by default
        if N is not None:
            m &= (self.tokens @ P.T).max(1) > (self.tokens @ N.T).max(1) + det.get("neg_margin", 0.0)
        idx = np.nonzero(m)[0]
        return sorted(idx.tolist(), key=lambda k: (-float(S[k]), k))

    def heights(self) -> np.ndarray:
        """Median lidar height-above-ground inside each outline (NaN where there's no lidar or no outline). Cached."""
        if getattr(self, "_h", None) is None:
            from groundtruth import lidar
            from groundtruth.geo import to_measure

            h = np.full(len(self.feats), np.nan)
            lp = lidar.place_for(self.meta["bbox"])
            if lp:
                for k, f in enumerate(self.feats):
                    if f.get("geometry"):
                        v = lidar.values(to_measure(shape(f["geometry"])), lp)
                        if len(v):
                            h[k] = float(np.median(v))
            self._h = h
        return self._h

    def passed_with(self, oc: dict[str, Any]) -> np.ndarray:
        """Re-apply the object check with (possibly different) thresholds from the stored per-proposal measurements;
        plus the lidar height rule (``max_height_m``: median height-above-ground) where lidar exists."""
        lo, hi = oc.get("area_m2", [0, 1e12])
        out = np.zeros(len(self.feats), bool)
        for k, f in enumerate(self.feats):
            c = f["properties"]["object_check"]
            if f.get("geometry") is None or "area_m2" not in c:
                continue
            out[k] = (lo <= c["area_m2"] <= hi and c["compactness"] >= oc.get("compactness_min", 0)
                      and c["colour_frac"] >= oc.get("colour_frac_min", 0)
                      and c["colour_area_m2"] >= oc.get("colour_area_min_m2", 0))
        if oc.get("max_height_m") is not None:
            h = self.heights()
            out &= ~(h > float(oc["max_height_m"]))  # NaN (no lidar) passes
        sh = oc.get("shadow", {})
        if sh.get("enabled"):  # dark AND next to something lidar-tall → a building shadow, not a pool
            p = dense_dir(self.meta["place"], self.meta["concept"], self.meta["year"]) / "shadow_v1.npz"
            if p.exists():
                z = np.load(p)
                dark = z["v_mean"] < float(sh.get("v_max", 110))
                near_tall = z["tall_within_m"] > float(sh.get("tall_m", 8))
                out &= ~(dark & near_tall)
        if oc.get("max_building_overlap") is not None:  # a "pool" lying on a building footprint is a roof
            out &= ~(self.building_overlap() > float(oc["max_building_overlap"]))
        return out

    def building_overlap(self) -> np.ndarray:
        """Fraction of each outline lying on Overture building footprints (0 where no outline). Cached."""
        if getattr(self, "_bo", None) is None:
            from groundtruth.geo import to_measure
            from groundtruth.vector import store

            fc, _ = store().buildings(self.meta["bbox"])
            bg = [to_measure(shape(f["geometry"])) for f in fc["features"]]
            tree = STRtreeLazy(bg)
            bo = np.zeros(len(self.feats))
            for k, f in enumerate(self.feats):
                if f.get("geometry"):
                    g = to_measure(shape(f["geometry"]))
                    bo[k] = sum(g.intersection(bg[j]).area for j in tree.query(g)) / max(g.area, 1e-9)
            self._bo = bo
        return self._bo

    def sims(self, qp: np.ndarray) -> np.ndarray:
        return self.tokens @ qp

    def index_ids(self, index) -> list[str]:
        """Per proposal, the index id ``chip#cell:patch`` of the chip in which it sits most centrally (so refine /
        outline / compare_years accept dense objects). Cached."""
        if getattr(self, "_ids", None) is None:
            x0, y0 = np.asarray(index.meta["x0_m"], float), np.asarray(index.meta["y0_m"], float)
            cm, g = config.CHIP_M, index.grid
            out = []
            for x, y in zip(self.x_m, self.y_m):
                inside = np.nonzero((x0 <= x) & (x < x0 + cm) & (y0 <= y) & (y < y0 + cm))[0]
                if not len(inside):
                    out.append(None)
                    continue
                edge = np.minimum.reduce([x - x0[inside], x0[inside] + cm - x, y - y0[inside], y0[inside] + cm - y])
                i = int(inside[int(np.argmax(edge))])
                pc, pr = int((x - x0[i]) / (cm / 16)), int((y0[i] + cm - y) / (cm / 16))
                pc, pr = min(max(pc, 0), 15), min(max(pr, 0), 15)
                cell = (pr * g // 16) * g + (pc * g // 16)
                out.append(f"{index.ids[i]}#{cell}:{pr * 16 + pc}")
            self._ids = out
        return self._ids


class STRtreeLazy:
    """STRtree over a possibly empty list (query → [] when empty), intersecting candidates only."""

    def __init__(self, geoms: list[Any]) -> None:
        from shapely.strtree import STRtree

        self.g = geoms
        self.t = STRtree(geoms) if geoms else None

    def query(self, geom) -> list[int]:
        return [int(j) for j in self.t.query(geom, predicate="intersects")] if self.t is not None else []
