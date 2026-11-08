"""Object outlines with SAM 2.1 (hiera-tiny, Apache-2.0), prompted by a hit's 16.8 m cell box.

Lazy by design: only for results on screen, cached by ``chip_id#cell`` under data/cache/outlines/<index version>/.
Never run county-wide on CPU.
"""

from __future__ import annotations

import io
import json
import threading
import time
from functools import cached_property
from typing import Any

import numpy as np
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from shapely.geometry import mapping, shape

from groundtruth import config
from groundtruth.device import pick_device, torch_device
from groundtruth.geo import SQM_PER_ACRE, to_wgs84
from groundtruth.index import ChipIndex

SAM_MODEL = "facebook/sam2.1-hiera-tiny"
SAM_ID = "sam2.1-hiera-tiny"


class Outliner:
    def __init__(self, device: str | None = None) -> None:
        self.device_name = pick_device(device)
        self.device = torch_device(self.device_name)

    @cached_property
    def _model(self):
        from transformers import Sam2Model, Sam2Processor

        proc = Sam2Processor.from_pretrained(SAM_MODEL)
        model = Sam2Model.from_pretrained(SAM_MODEL).to(self.device).eval()
        return proc, model

    _lock = threading.Lock()

    @staticmethod
    def _cache_path(chip_id: str, cell: int, patch: int | None = None):
        tail = f"_p{patch}" if patch is not None else ""
        return config.DATA / "cache" / "outlines" / config.INDEX_VERSION / f"{chip_id}_{cell}{tail}.json"

    def outline(self, index: ChipIndex, i: int, cell: int, margin_px: int = 8, patch: int | None = None) -> dict[str, Any]:
        """→ GeoJSON Feature (EPSG:4326) with area_m2/area_acres/sam_score/ms/cached. Thread-safe.
        With ``patch`` (0–255, 14 px / 8.4 m), the prompt is that patch's centre + its box ± 12 px (finer than the cell)."""
        chip_id = index.ids[i]
        cp = self._cache_path(chip_id, cell, patch)
        if cp.exists():
            f = json.loads(cp.read_text())
            f["properties"]["cached"] = True
            return f
        with self._lock:
            if cp.exists():
                f = json.loads(cp.read_text()); f["properties"]["cached"] = True
                return f
            return self._outline_uncached(index, i, cell, margin_px, cp, patch)

    def segment(self, img, box: list[float], point: list[float]) -> tuple[np.ndarray, float]:
        """SAM 2.1 on one RGB image (PIL or uint8 array), prompted by a box + positive point (pixels).
        Multimask; picks the highest-IoU mask no larger than 1.5× the box. → (bool mask HxW, sam_score)."""
        import torch
        from PIL import Image

        if not isinstance(img, Image.Image):
            img = Image.fromarray(np.asarray(img))
        w, h = img.size
        proc, model = self._model
        inputs = proc(images=img, input_boxes=[[box]], input_points=[[[point]]], input_labels=[[[1]]],
                      return_tensors="pt").to(self.device)
        with torch.inference_mode():
            out = model(**inputs, multimask_output=True)
        masks = proc.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"].cpu())[0]
        cand = np.asarray(masks).reshape(-1, h, w).astype(bool)
        ious = out.iou_scores.reshape(-1).float().cpu().numpy()[: len(cand)]
        box_area = (box[2] - box[0]) * (box[3] - box[1])
        ok = [k for k in range(len(cand)) if 0 < cand[k].sum() <= 1.5 * box_area] or list(range(len(cand)))
        k = max(ok, key=lambda j: ious[j])
        return cand[k], float(ious[k])

    def _outline_uncached(self, index: ChipIndex, i: int, cell: int, margin_px: int, cp,
                          patch: int | None = None) -> dict[str, Any]:
        chip_id = index.ids[i]
        import torch
        from PIL import Image

        t0 = time.perf_counter()
        img = Image.open(io.BytesIO(index.thumb_jpeg(i))).convert("RGB")
        g = index.grid
        px = config.CHIP_PX // g
        r, c = divmod(int(cell), g)
        m = margin_px
        box = [max(0, c * px - m), max(0, r * px - m), min(config.CHIP_PX, (c + 1) * px + m),
               min(config.CHIP_PX, (r + 1) * px + m)]
        point = [c * px + px / 2, r * px + px / 2]
        if patch is not None:
            pp = config.CHIP_PX // 16
            pr, pc = divmod(int(patch), 16)
            m = 12
            box = [max(0, pc * pp - m), max(0, pr * pp - m), min(config.CHIP_PX, (pc + 1) * pp + m),
                   min(config.CHIP_PX, (pr + 1) * pp + m)]
            point = [pc * pp + pp / 2, pr * pp + pp / 2]
        mask, score = self.segment(img, box, point)
        # polygonise in UTM: chip pixel (0,0) = NW corner
        x0, y0 = float(index.meta["x0_m"][i]), float(index.meta["y0_m"][i])
        tr = from_origin(x0, y0 + config.CHIP_M, config.GSD_M, config.GSD_M)
        polys = [shape(geom) for geom, v in rfeatures.shapes(mask.astype(np.uint8), mask=mask, transform=tr) if v == 1]
        ms = round((time.perf_counter() - t0) * 1000, 1)
        ex_id = f"{chip_id}#{cell}" + (f":{patch}" if patch is not None else "")
        props: dict[str, Any] = {"chip_id": chip_id, "cell": int(cell), "example": ex_id, "patch": patch,
                                 "sam_score": round(score, 4), "model": SAM_ID, "ms": ms, "cached": False}
        if not polys:
            feat = {"type": "Feature", "id": ex_id, "geometry": None,
                    "properties": {**props, "area_m2": 0.0, "area_acres": 0.0, "empty": True}}
        else:
            # keep the component containing / overlapping the prompt most
            from shapely.geometry import Point
            from shapely.geometry import box as sbox

            bx0, by0, bx1, by1 = box
            pbox = sbox(x0 + bx0 * config.GSD_M, y0 + config.CHIP_M - by1 * config.GSD_M,
                        x0 + bx1 * config.GSD_M, y0 + config.CHIP_M - by0 * config.GSD_M)
            ppt = Point(x0 + point[0] * config.GSD_M, y0 + config.CHIP_M - point[1] * config.GSD_M)
            best = max(polys, key=lambda p: (p.contains(ppt), p.intersection(pbox).area, p.area)).simplify(0.3)
            area = best.area
            per = best.length
            feat = {"type": "Feature", "id": ex_id, "geometry": mapping(to_wgs84(best)),
                    "properties": {**props, "area_m2": round(area, 2), "area_acres": round(area / SQM_PER_ACRE, 5),
                                   "perimeter_m": round(per, 2),
                                   "compactness": round(4 * np.pi * area / max(per * per, 1e-9), 3)}}
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(json.dumps(feat))
        tmp.replace(cp)
        return feat
