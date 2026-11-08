"""Area inventory ("what's in this area?", Phase 7): SAM 2.1 automatic masks + labels → counts, area, % cover per class.

1. Read the bbox raster at 0.6 m (EPSG:32617) from the provider; tile into windows (conf/inventory.json).
2. Per window: SAM 2.1 image embedding once, a point grid of prompts in batches → masks with predicted IoU ≥ iou_min;
   greedy NMS by mask IoU; masks touching the window edge are dropped unless the window is on the bbox edge (the
   overlapping neighbour sees them whole).
3. Label each mask: OpenCLIP crop vs the class prompts; then rules (Overture building overlap → roof; lidar height:
   tree canopy vs lawn, roof vs pavement; pool colour → pool).
4. Global dedupe across windows (polygon IoU), cover = union area per class / bbox area.
Samgeo isn't used: it would pin a second SAM stack; this is the same automatic-mask algorithm on the SAM 2.1 already here.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Sequence

import numpy as np
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from shapely.geometry import box, mapping, shape
from shapely.strtree import STRtree

from groundtruth import config
from groundtruth.geo import SQM_PER_ACRE, to_measure, to_wgs84


def conf() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "inventory.json").read_text())


def _auto_masks(outl, win: np.ndarray, c: dict[str, Any]) -> list[tuple[np.ndarray, float]]:
    import torch
    from PIL import Image

    proc, model = outl._model
    img = Image.fromarray(win)
    h, w = win.shape[:2]
    n = int(c["points_per_side"])
    xs = (np.arange(n) + 0.5) * w / n
    ys = (np.arange(n) + 0.5) * h / n
    pts = [[float(x), float(y)] for y in ys for x in xs]
    inputs = proc(images=img, return_tensors="pt").to(outl.device)
    out_masks = []
    with torch.inference_mode():
        emb = model.get_image_embeddings(inputs["pixel_values"])
        for k in range(0, len(pts), int(c["point_batch"])):
            pb = pts[k:k + int(c["point_batch"])]
            pin = proc(images=img, input_points=[[[p] for p in pb]], input_labels=[[[1] for _ in pb]],
                       return_tensors="pt").to(outl.device)
            o = model(image_embeddings=emb, input_points=pin["input_points"], input_labels=pin["input_labels"],
                      multimask_output=True)
            ms = proc.post_process_masks(o.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]  # (pb, 3, H, W)
            ious = o.iou_scores.float().cpu().numpy()[0]  # (pb, 3)
            ms = np.asarray(ms).astype(bool)
            for j in range(ms.shape[0]):
                b = int(np.argmax(ious[j]))
                if ious[j, b] >= c["iou_min"]:
                    out_masks.append((ms[j, b], float(ious[j, b])))
    # greedy NMS by mask IoU
    out_masks.sort(key=lambda t: -t[1])
    kept: list[tuple[np.ndarray, float]] = []
    for m, s in out_masks:
        a = m.sum()
        if a == 0 or a > c["max_area_frac"] * m.size:
            continue
        if any((m & k).sum() / max((m | k).sum(), 1) > c["nms_iou"] for k, _ in kept):
            continue
        kept.append((m, s))
    return kept


def run(eng, bbox: Sequence[float], log: Callable[[str], None] = print) -> dict[str, Any]:
    """→ {features (EPSG:4326, properties: class, area_m2, score, label_how, height_m), summary, legend, timings}."""
    from groundtruth import lidar
    from groundtruth.outline import Outliner
    from groundtruth.rerank import _hsv, concept_conf
    from groundtruth.vector import store

    c = conf()
    t0 = time.perf_counter()
    g = config.GSD_M
    mb = to_measure(box(*bbox)).bounds
    area_km2 = (mb[2] - mb[0]) * (mb[3] - mb[1]) / 1e6
    if area_km2 > c["max_area_km2"]:
        raise ValueError(f"bbox is {area_km2:.2f} km²; the inventory is limited to {c['max_area_km2']} km²")
    x0, y0 = np.floor(mb[0] / g) * g, np.floor(mb[1] / g) * g
    x1, y1 = np.ceil(mb[2] / g) * g, np.ceil(mb[3] / g) * g
    im = eng.provider.read_grid((x0, y0, x1, y1), eng.year)
    rgb = im.rgb
    H, W = rgb.shape[:2]
    t_read = time.perf_counter() - t0
    outl = Outliner()
    wp, st = int(c["window_px"]), int(c["stride_px"])
    rows = list(range(0, max(H - wp, 0) + 1, st)) or [0]
    cols = list(range(0, max(W - wp, 0) + 1, st)) or [0]
    if rows[-1] + wp < H:
        rows.append(max(H - wp, 0))
    if cols[-1] + wp < W:
        cols.append(max(W - wp, 0))
    polys: list[tuple[Any, float]] = []
    t1 = time.perf_counter()
    for r in rows:
        for cc in cols:
            win = rgb[r:r + wp, cc:cc + wp]
            if win.shape[0] < 32 or win.shape[1] < 32:
                continue
            tr = from_origin(x0 + cc * g, y1 - r * g, g, g)
            for m, s in _auto_masks(outl, np.ascontiguousarray(win), c):
                edge = (m[0].any() and r > 0) or (m[-1].any() and r + wp < H) or (m[:, 0].any() and cc > 0) or \
                       (m[:, -1].any() and cc + wp < W)
                if edge:
                    continue
                ps = [shape(gm) for gm, v in rfeatures.shapes(m.astype(np.uint8), mask=m, transform=tr) if v == 1]
                if not ps:
                    continue
                p = max(ps, key=lambda q: q.area).buffer(0).simplify(0.3)
                if p.area >= c["min_area_m2"]:
                    polys.append((p, s))
            log(f"  window {r},{cc}: {len(polys)} masks so far ({time.perf_counter() - t1:.0f} s)")
    # global dedupe (overlapping windows)
    polys.sort(key=lambda t: -t[1])
    kept: list[tuple[Any, float]] = []
    tree_items: list[Any] = []
    for p, s in polys:
        if tree_items:
            tr_ = STRtree(tree_items)
            if any(p.intersection(tree_items[j]).area / max(p.union(tree_items[j]).area, 1e-9) > c["nms_iou"]
                   for j in tr_.query(p)):
                continue
        kept.append((p, s))
        tree_items.append(p)
    t_sam = time.perf_counter() - t1
    # 3. labels: CLIP crops + rules
    t2 = time.perf_counter()
    clip = eng._clip_model() if hasattr(eng, "_clip_model") else __import__("groundtruth.embed", fromlist=["get_clip"]).get_clip()
    classes = list(c["classes"])
    prompts, owner = [], []
    for k in classes:
        for pr in c["classes"][k]["prompts"]:
            prompts.append(f"aerial photo of {pr}")
            owner.append(k)
    T = clip.embed_texts(prompts)
    crops = []
    from PIL import Image

    for p, _ in kept:
        b = p.bounds
        cx0, cy0 = int((b[0] - x0) / g), int((y1 - b[3]) / g)
        cx1, cy1 = int(np.ceil((b[2] - x0) / g)), int(np.ceil((y1 - b[1]) / g))
        pad = max(4, int(0.25 * max(cx1 - cx0, cy1 - cy0)))
        crop = rgb[max(cy0 - pad, 0):min(cy1 + pad, H), max(cx0 - pad, 0):min(cx1 + pad, W)]
        crops.append(np.asarray(Image.fromarray(crop).resize((224, 224), Image.BICUBIC)))
    E = clip.embed_images(np.stack(crops)) if crops else np.zeros((0, T.shape[1]))
    S = E @ T.T
    best_by_class = np.stack([S[:, [i for i, o in enumerate(owner) if o == k]].max(1) for k in classes], 1) if len(E) else np.zeros((0, len(classes)))
    fc, _ = store().buildings(bbox)
    bpolys = [to_measure(shape(f["geometry"])) for f in fc["features"]]
    btree = STRtree(bpolys) if bpolys else None
    lp = lidar.place_for(bbox)
    rule = concept_conf("swimming_pool")["rerank"]["colour"]
    lo, hi = np.array(rule["hsv_lo"], np.float32), np.array(rule["hsv_hi"], np.float32)
    R = c["rules"]
    feats = []
    for n_, (p, s) in enumerate(kept):
        k = classes[int(np.argmax(best_by_class[n_]))]
        how = "clip"
        overlap = 0.0
        if btree is not None:
            overlap = sum(p.intersection(bpolys[j]).area for j in btree.query(p)) / max(p.area, 1e-9)
        h = None
        if lp:
            v = lidar.values(p, lp)
            h = float(np.median(v)) if len(v) else None
        mask = rfeatures.rasterize([p], out_shape=(H, W), transform=from_origin(x0, y1, g, g)) > 0
        hsv = _hsv(rgb[mask][None])[0] if mask.any() else np.zeros((0, 3))
        cfrac = float(np.all((hsv >= lo) & (hsv <= hi), -1).mean()) if len(hsv) else 0.0
        if cfrac >= R["pool_colour_frac_min"] and (h is None or h < R["low_max_height_m"] + 0.7):
            k, how = "pool", "rule: pool colour"
        elif overlap >= R["building_overlap_min"] and (h is None or h >= R["roof_min_height_m"]):
            k, how = ("solar" if k == "solar" else "roof"), "rule: Overture building" + (" + height" if h is not None else "")
        elif h is not None and k in ("lawn", "tree canopy", "bare soil", "other"):
            k, how = ("tree canopy", "rule: lidar height") if h >= R["tree_min_height_m"] else \
                     (("lawn" if k in ("lawn", "tree canopy") else k), "rule: lidar height")
        elif h is not None and k == "roof" and h < R["roof_min_height_m"]:
            k, how = ("driveway" if p.area < 150 else "parking lot"), "rule: no height → not a roof"
        feats.append({"type": "Feature", "id": f"inv{n_}", "geometry": mapping(to_wgs84(p)),
                      "properties": {"class": k, "area_m2": round(p.area, 1), "sam_score": round(s, 3),
                                     "clip_class": classes[int(np.argmax(best_by_class[n_]))], "label_how": how,
                                     "height_m": None if h is None else round(h, 2), "building_overlap": round(overlap, 2),
                                     "colour": c["classes"][k]["colour"]}})
    t_lab = time.perf_counter() - t2
    total = (mb[2] - mb[0]) * (mb[3] - mb[1])
    from shapely.ops import unary_union

    summary = {}
    for k in classes:
        ps = [shape(f["geometry"]) for f in feats if f["properties"]["class"] == k]
        if not ps:
            continue
        a = to_measure(unary_union(ps)).area
        summary[k] = {"count": len(ps), "area_m2": round(a, 1), "area_acres": round(a / SQM_PER_ACRE, 3),
                      "cover_pct": round(100 * a / total, 2)}
    covered = to_measure(unary_union([shape(f["geometry"]) for f in feats])).area if feats else 0.0
    return {"features": feats, "summary": summary, "legend": {k: c["classes"][k]["colour"] for k in classes},
            "bbox": list(bbox), "area_km2": round(area_km2, 3), "unlabelled_pct": round(100 * (1 - covered / total), 1),
            "lidar": lp, "masks": len(feats), "windows": len(rows) * len(cols),
            "ms": {"read": round(t_read * 1000), "sam": round(t_sam * 1000), "label": round(t_lab * 1000),
                   "total": round((time.perf_counter() - t0) * 1000)}, "device": outl.device_name}


def centre_bbox(place_bbox: Sequence[float], km: float) -> list[float]:
    """A km × km box at the centre of a place box (EPSG:4326)."""
    b = to_measure(box(*place_bbox))
    cx, cy = b.centroid.x, b.centroid.y
    h = km * 500
    return list(to_wgs84(box(cx - h, cy - h, cx + h, cy + h)).bounds)
