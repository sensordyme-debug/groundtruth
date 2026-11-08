"""Open-vocabulary "find anything" (Phase 4) for concepts that are not configured in senses.json.

CLIP ranks chips for the text → Grounding DINO (Apache-2.0 weights, conf/openvocab.json) boxes the concept inside each
chip → SAM 2.1 masks each box → verifier (concept area range when configured, else the detector score) → dedupe by
distance. Results are cached per (text, place/bbox) in memory.
"""

from __future__ import annotations

import io
import json
import threading
import time
from functools import cached_property
from typing import Any, Sequence

import numpy as np
from PIL import Image
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from shapely.geometry import Point, box, mapping, shape

from groundtruth import config
from groundtruth.device import pick_device, torch_device
from groundtruth.geo import SQM_PER_ACRE, to_wgs84


def conf() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "openvocab.json").read_text())


class Detector:
    """Grounding DINO, loaded lazily on first use and unloaded after ``idle_unload_min`` minutes without use
    (conf/openvocab.json): it is not on the demo's hot path (boat docks are served from the disk cache)."""

    _lock = threading.Lock()

    def __init__(self, model_id: str, device: str | None = None) -> None:
        self.model_id = model_id
        self.device_name = pick_device(device)
        self.device = torch_device(self.device_name)
        self._m = None
        self.last_used = 0.0
        self._timer: threading.Timer | None = None

    @property
    def _model(self):
        if self._m is None:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

            proc = AutoProcessor.from_pretrained(self.model_id)
            model = AutoModelForZeroShotObjectDetection.from_pretrained(self.model_id).to(self.device).eval()
            self._m = (proc, model)
        self.last_used = time.time()
        self._arm_unload()
        return self._m

    def _arm_unload(self) -> None:
        mins = float(conf().get("idle_unload_min", 5))
        if mins <= 0:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(mins * 60, self.unload_if_idle, args=(mins,))
        self._timer.daemon = True
        self._timer.start()

    def unload_if_idle(self, mins: float) -> None:
        with self._lock:
            if self._m is not None and time.time() - self.last_used >= mins * 60 - 1:
                self._m = None
                import gc

                gc.collect()
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    @property
    def loaded(self) -> bool:
        return self._m is not None

    def detect(self, images: Sequence[Image.Image], prompt: str, box_th: float, text_th: float):
        """→ per image: (boxes xyxy px (k,4), scores (k,))."""
        import torch

        proc, model = self._model
        text = prompt.lower().strip().rstrip(".") + "."
        with self._lock, torch.inference_mode():
            inputs = proc(images=list(images), text=[text] * len(images), return_tensors="pt").to(self.device)
            out = model(**inputs)
            sizes = [im.size[::-1] for im in images]
            try:
                res = proc.post_process_grounded_object_detection(out, inputs["input_ids"], threshold=box_th,
                                                                  text_threshold=text_th, target_sizes=sizes)
            except TypeError:  # older transformers
                res = proc.post_process_grounded_object_detection(out, inputs["input_ids"], box_threshold=box_th,
                                                                  text_threshold=text_th, target_sizes=sizes)
        return [(r["boxes"].float().cpu().numpy(), r["scores"].float().cpu().numpy()) for r in res]


_DET: dict[str, Detector] = {}


def detector() -> Detector:
    mid = conf()["detector"]
    if mid not in _DET:
        _DET[mid] = Detector(mid)
    return _DET[mid]


def concept_entry(text: str) -> dict[str, Any]:
    t = text.lower().strip()
    cs = conf()["concepts"]
    for k, v in cs.items():
        if k == t or k + "s" == t or t.rstrip("s") == k:
            return {"key": k, **v}
    return {"key": t, "prompt": t}


def _disk_key(text: str, bbox, year: int) -> str:
    import hashlib

    c = conf()
    blob = json.dumps({"t": concept_entry(text), "b": [round(v, 6) for v in bbox] if bbox else None, "y": year,
                       "c": {k: c[k] for k in ("detector", "top_k_chips", "place_max_chips", "box_threshold",
                                               "text_threshold", "max_box_frac", "min_box_px", "nms_m",
                                               "clip_mask_to_box", "clip_verify") if k in c}}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def find_anything(eng, text: str, bbox: Sequence[float] | None, place: str | None, log=None) -> tuple[list[dict], dict]:
    """Disk-cached (data/cache/openvocab/<version>/, keyed by text + area + year + detector config): a cached answer
    never loads the detector. → (features EPSG:4326, info)."""
    d = config.DATA / "cache" / "openvocab" / config.data_version("openvocab_cache")
    p = d / f"{_disk_key(text, bbox, eng.year)}.json"
    if p.exists():
        out = json.loads(p.read_text())
        out["info"]["from_disk_cache"] = True
        return out["features"], out["info"]
    feats, info = _find_anything(eng, text, bbox, place, log)
    d.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"features": feats, "info": info}))
    tmp.replace(p)
    return feats, info


def _find_anything(eng, text: str, bbox: Sequence[float] | None, place: str | None, log=None) -> tuple[list[dict], dict]:
    """→ (features EPSG:4326, info). Features use index ids ``chip#cell:patch`` at the object centre."""
    c = conf()
    ce = concept_entry(text)
    t0 = time.perf_counter()
    info: dict[str, Any] = {"concept": ce["key"], "prompt": ce["prompt"], "detector": c["detector"]}
    # 1. chips: CLIP-ranked (every chip of the place up to place_max_chips, else county top_k_chips)
    q = eng._clip_model().embed_query(ce["prompt"])
    if bbox is not None:
        rows = eng._bbox_rows(bbox)
        V = eng.index.clip_views(rows)  # (n, V, 512)
        s = (V @ q).max(1)
        order = np.argsort(-s, kind="stable")[: int(c["place_max_chips"])]
        chips = [int(rows[j]) for j in order]
        info["chips_in_box"] = int(len(rows))
    else:
        chips = [i for i, _, _ in eng._clip_rank(q, int(c["top_k_chips"]))]
    info["chips_searched"] = len(chips)
    info["ms_clip"] = round((time.perf_counter() - t0) * 1000)
    # 2. Grounding DINO
    det = detector()
    t1 = time.perf_counter()
    raw = []  # (row, box px, score)
    B = int(c["batch"])
    for k in range(0, len(chips), B):
        rb = chips[k:k + B]
        ims = [Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB") for i in rb]
        for i, (bx, sc) in zip(rb, det.detect(ims, ce["prompt"], c["box_threshold"], c["text_threshold"])):
            for b, s_ in zip(bx, sc):
                w, h = b[2] - b[0], b[3] - b[1]
                if w * h > c["max_box_frac"] * config.CHIP_PX ** 2 or min(w, h) < c["min_box_px"]:
                    continue
                raw.append((i, [float(v) for v in b], float(s_)))
    info["ms_detect"] = round((time.perf_counter() - t1) * 1000)
    info["raw_boxes"] = len(raw)
    info["device"] = det.device_name
    # 3. dedupe by box-centre distance (higher score wins)
    g = config.GSD_M
    pts, kept = [], []
    for i, b, s_ in sorted(raw, key=lambda r: (-r[2], eng.index.ids[r[0]])):
        x0, y0 = float(eng.index.meta["x0_m"][i]), float(eng.index.meta["y0_m"][i])
        cx, cy = x0 + (b[0] + b[2]) / 2 * g, y0 + config.CHIP_M - (b[1] + b[3]) / 2 * g
        if any((cx - px) ** 2 + (cy - py) ** 2 < c["nms_m"] ** 2 for px, py in pts):
            continue
        pts.append((cx, cy))
        kept.append((i, b, s_, cx, cy))
    info["boxes_after_dedupe"] = len(kept)
    # 3b. CLIP crop verifier (A/B flag clip_verify.enabled): the box crop (+context) must match the prompt better than
    # every background prompt by min_margin
    cv = c.get("clip_verify", {})
    if cv.get("enabled") and kept:
        t3 = time.perf_counter()
        clip = eng._clip_model()
        crops = []
        for i, b, *_ in kept:
            im = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
            w, h = b[2] - b[0], b[3] - b[1]
            pad = float(cv.get("context", 0.3))
            x0_, y0_ = max(0, b[0] - pad * w), max(0, b[1] - pad * h)
            x1_, y1_ = min(config.CHIP_PX, b[2] + pad * w), min(config.CHIP_PX, b[3] + pad * h)
            crops.append(np.asarray(im.crop((int(x0_), int(y0_), int(np.ceil(x1_)), int(np.ceil(y1_))))
                                    .resize((224, 224), Image.BICUBIC)))
        E = clip.embed_images(np.stack(crops))
        txt = clip.embed_texts([f"aerial photo of a {ce['prompt']}"] + [f"aerial photo of {n}" for n in cv["negatives"]])
        S = E @ txt.T
        margin = S[:, 0] - S[:, 1:].max(1)
        keep_mask = margin >= float(cv.get("min_margin", 0.0))
        info["clip_rejected"] = int((~keep_mask).sum())
        kept = [k for k, ok in zip(kept, keep_mask) if ok]
        info["ms_clip_verify"] = round((time.perf_counter() - t3) * 1000)
    # 4. SAM 2.1 per box + verifier
    t2 = time.perf_counter()
    outl = None
    if c.get("sam", True):
        if eng._outliner is None:
            from groundtruth.outline import Outliner

            eng._outliner = Outliner()
        outl = eng._outliner
    lo, hi = ce.get("area_m2", [0, 1e12])
    feats, rejected = [], 0
    for i, b, s_, cx, cy in kept:
        x0, y0 = float(eng.index.meta["x0_m"][i]), float(eng.index.meta["y0_m"][i])
        tr = from_origin(x0, y0 + config.CHIP_M, g, g)
        geom_m, sam_score = None, None
        if outl is not None:
            img = Image.open(io.BytesIO(eng.index.thumb_jpeg(i))).convert("RGB")
            mask, sam_score = outl.segment(img, b, [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
            if c.get("clip_mask_to_box", False):  # A/B flag: SAM may bleed into neighbours; keep it inside the box
                mpad = int(c.get("mask_box_pad_px", 4))
                bm = np.zeros_like(mask)
                bm[max(0, int(b[1]) - mpad):int(np.ceil(b[3])) + mpad, max(0, int(b[0]) - mpad):int(np.ceil(b[2])) + mpad] = True
                mask = mask & bm
            polys = [shape(gm) for gm, v in rfeatures.shapes(mask.astype(np.uint8), mask=mask, transform=tr) if v == 1]
            if polys:
                bb_m = box(x0 + b[0] * g, y0 + config.CHIP_M - b[3] * g, x0 + b[2] * g, y0 + config.CHIP_M - b[1] * g)
                geom_m = max(polys, key=lambda p: (p.intersection(bb_m).area, p.area)).buffer(0).simplify(0.3)
        if geom_m is None:
            geom_m = box(x0 + b[0] * g, y0 + config.CHIP_M - b[3] * g, x0 + b[2] * g, y0 + config.CHIP_M - b[1] * g)
        area = geom_m.area
        ok = lo <= area <= hi
        if not ok:
            rejected += 1
            continue
        pr = min(15, max(0, int(((b[1] + b[3]) / 2) / 14)))
        pc = min(15, max(0, int(((b[0] + b[2]) / 2) / 14)))
        gr = eng.index.grid
        cell = (pr * gr // 16) * gr + (pc * gr // 16)
        conf_ = s_ * (0.5 + 0.5 * sam_score) if sam_score is not None else s_ * 0.85
        f = eng.index.feature(i, cell=cell, score=round(s_, 4), example=f"{eng.index.ids[i]}#{cell}:{pr * 16 + pc}",
                              patch=pr * 16 + pc, detector_score=round(s_, 4),
                              sam_score=None if sam_score is None else round(sam_score, 4))
        f["geometry"] = mapping(to_wgs84(geom_m))
        f["properties"].update(geometry_kind="outline" if sam_score is not None else "box",
                               area_m2=round(area, 1), area_acres=round(area / SQM_PER_ACRE, 5),
                               confidence=round(float(conf_), 3), x_m=round(cx, 2), y_m=round(cy, 2),
                               concept=ce["key"])
        feats.append(f)
    feats.sort(key=lambda f: (-f["properties"]["confidence"], f["properties"]["example"]))
    for r, f in enumerate(feats):
        f["properties"]["rank"] = r + 1
    info.update(ms_sam=round((time.perf_counter() - t2) * 1000), rejected_by_area=rejected,
                ms_total=round((time.perf_counter() - t0) * 1000))
    ep = c.get("eye_precision", {}).get(ce["key"])
    info["eye_precision_12"] = ep
    info["low_confidence"] = ep is None or ep < c["low_confidence_below"]
    return feats, info
