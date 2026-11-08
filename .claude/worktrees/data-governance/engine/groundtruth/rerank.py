"""Query-time re-rank for small objects (e.g. backyard pools).

The stored index has DINOv2 cells of 16.8 m (8×8 per chip): good for recall, too coarse for a 6×10 m pool. For the top
~400 candidates we recompute full-resolution DINOv2 patch tokens (16×16 per chip, 14 px = 8.4 m) from the stored
thumbnails and blend in a per-patch colour score (HSV range from conf/concepts.json). Score per chip = best patch.
GPU when available (VRAM-capped, backs off when the card is busy), else CPU.
"""

from __future__ import annotations

import io
import json
from collections import OrderedDict
from functools import lru_cache
from typing import Any, Sequence

import numpy as np
from PIL import Image

from groundtruth import config
from groundtruth.embed import IMAGENET_MEAN, IMAGENET_STD, get_dino
from groundtruth.index import ChipIndex

PATCHES = 16  # per side (224 / 14)


@lru_cache(maxsize=1)
def concepts() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "concepts.json").read_text())


def concept_conf(concept: str | None) -> dict[str, Any]:
    c = concepts()
    out = json.loads(json.dumps(c["_default"]))
    if concept and concept in c:
        out.update(json.loads(json.dumps(c[concept])))
    return out


def _hsv(rgb: np.ndarray) -> np.ndarray:
    """uint8 RGB (…,3) → H degrees [0,360), S,V [0,255]."""
    x = rgb.astype(np.float32) / 255.0
    mx, mn = x.max(-1), x.min(-1)
    d = mx - mn + 1e-9
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    h = np.where(mx == r, (g - b) / d % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    h = np.where(mx - mn < 1e-6, 0, h)
    s = np.where(mx > 0, (mx - mn) / (mx + 1e-9), 0) * 255
    return np.stack([h, s, mx * 255], -1)


def colour_patches(rgb: np.ndarray, rule: dict[str, Any] | None) -> np.ndarray:
    """(224,224,3) → (16,16) fraction of pixels inside the HSV range."""
    if not rule:
        return np.zeros((PATCHES, PATCHES), np.float32)
    p = config.CHIP_PX // PATCHES
    if rgb.shape[0] == config.CHIP_PX and p % 2 == 0:  # half resolution: 7×7 px per patch is plenty for a fraction
        rgb, p = rgb[::2, ::2], p // 2
    hsv = _hsv(rgb)
    lo, hi = np.array(rule["hsv_lo"], np.float32), np.array(rule["hsv_hi"], np.float32)
    m = np.all((hsv >= lo) & (hsv <= hi), -1).astype(np.float32)
    return m.reshape(PATCHES, p, PATCHES, p).mean((1, 3))


_COLOUR: "OrderedDict[tuple, np.ndarray]" = OrderedDict()


def colour_cached(key: tuple, rgb: np.ndarray, rule: dict[str, Any] | None) -> np.ndarray:
    if not rule:
        return np.zeros((PATCHES, PATCHES), np.float32)
    k = key + (json.dumps(rule, sort_keys=True),)
    v = _COLOUR.get(k)
    if v is None:
        v = colour_patches(rgb, rule)
        _COLOUR[k] = v
        while len(_COLOUR) > 20000:
            _COLOUR.popitem(last=False)
    return v


class PatchCache:
    """LRU of per-chip patch tokens (256×768, fp16) + RGB, keyed by (index root, chip row)."""

    def __init__(self, max_items: int = 1500) -> None:
        self.max = max_items
        self.d: OrderedDict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def get(self, key):
        v = self.d.get(key)
        if v is not None:
            self.d.move_to_end(key)
        return v

    def put(self, key, v) -> None:
        self.d[key] = v
        self.d.move_to_end(key)
        while len(self.d) > self.max:
            self.d.popitem(last=False)


_CACHE = PatchCache(int(__import__("os").environ.get("GROUNDTRUTH_PATCH_CACHE", "600")))  # ~0.55 MB per chip; ≥ re-rank pool


def patch_tokens(index: ChipIndex, rows: Sequence[int], batch: int = 64) -> tuple[np.ndarray, np.ndarray, str]:
    """→ tokens (n,256,768) fp32 L2-normalised, rgb (n,224,224,3), device used."""
    import torch

    dino = get_dino()
    root = str(index.root)
    out_t: list[np.ndarray | None] = [None] * len(rows)
    out_rgb: list[np.ndarray | None] = [None] * len(rows)
    todo = []
    for j, i in enumerate(rows):
        hit = _CACHE.get((root, int(i)))
        if hit is not None:
            out_t[j], out_rgb[j] = hit[0].astype(np.float32), hit[1]
        else:
            todo.append(j)
    for k in range(0, len(todo), batch):
        js = todo[k:k + batch]
        imgs = np.stack([np.asarray(Image.open(io.BytesIO(index.thumb_jpeg(int(rows[j])))).convert("RGB")) for j in js])
        x = (imgs.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
        xt = torch.from_numpy(np.ascontiguousarray(x.transpose(0, 3, 1, 2))).to(dino.device)
        with torch.inference_mode():
            if dino.device_name == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    h = dino.model(pixel_values=xt).last_hidden_state[:, 1:, :]
            else:
                h = dino.model(pixel_values=xt).last_hidden_state[:, 1:, :]
            h = torch.nn.functional.normalize(h.float(), dim=-1).cpu().numpy()
        for t, j in enumerate(js):
            out_t[j], out_rgb[j] = h[t], imgs[t]
            _CACHE.put((root, int(rows[j])), (h[t].astype(np.float16), imgs[t]))
    return np.stack(out_t), np.stack(out_rgb), dino.device_name


def dino_tokens(imgs: np.ndarray):
    """(n, 224, 224, 3) uint8 → L2-normalised DINOv2 patch tokens (n, 256, 768) as a torch tensor on the DINOv2 device
    (fp16 on CUDA via autocast, fp32 on CPU)."""
    import torch

    dino = get_dino()
    x = (imgs.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    xt = torch.from_numpy(np.ascontiguousarray(x.transpose(0, 3, 1, 2))).to(dino.device)
    with torch.inference_mode():
        if dino.device_name == "cuda":
            with torch.autocast("cuda", dtype=torch.float16):
                h = dino.model(pixel_values=xt).last_hidden_state[:, 1:, :]
            return torch.nn.functional.normalize(h.float(), dim=-1).half()
        return torch.nn.functional.normalize(dino.model(pixel_values=xt).last_hidden_state[:, 1:, :].float(), dim=-1)


class GpuPatchCache:
    """LRU of per-chip DINOv2 patch tokens kept ON THE DEVICE (fp16, 256×768 ≈ 0.39 MB per chip).
    Size: GROUNDTRUTH_PATCH_GPU_CACHE chips (default 3000 ≈ 1.2 GB VRAM, inside the 4 GB cap)."""

    def __init__(self) -> None:
        self.max = int(__import__("os").environ.get("GROUNDTRUTH_PATCH_GPU_CACHE", "3000"))
        self.d: OrderedDict[tuple[str, int], Any] = OrderedDict()

    def get_many(self, index: ChipIndex, rows: Sequence[int], batch: int = 64):
        """→ torch tensor (n, 256, 768) on the DINOv2 device (fp16 on CUDA, fp32 on CPU), rows in order."""
        import torch

        dino = get_dino()
        root = str(index.root)
        missing = [int(r) for r in rows if (root, int(r)) not in self.d]
        for k in range(0, len(missing), batch):
            rb = missing[k:k + batch]
            imgs = np.stack([np.asarray(Image.open(io.BytesIO(index.thumb_jpeg(r))).convert("RGB")) for r in rb])
            h = dino_tokens(imgs)
            for t, r in enumerate(rb):
                self.d[(root, r)] = h[t]
        out = []
        for r in rows:
            key = (root, int(r))
            self.d.move_to_end(key)
            out.append(self.d[key])
        while len(self.d) > self.max:
            self.d.popitem(last=False)
        return torch.stack(out) if out else None, dino.device_name


GPU_PATCHES = GpuPatchCache()


def colour_map(index: ChipIndex, i: int, rule: dict[str, Any] | None) -> np.ndarray:
    """(16,16) concept-colour fraction per patch for chip row i (cached; decodes the thumbnail only on a miss)."""
    if not rule:
        return np.zeros((PATCHES, PATCHES), np.float32)
    k = (str(index.root), int(i), json.dumps(rule, sort_keys=True))
    v = _COLOUR.get(k)
    if v is None:
        v = colour_patches(np.asarray(Image.open(io.BytesIO(index.thumb_jpeg(int(i)))).convert("RGB")), rule)
        _COLOUR[k] = v
        while len(_COLOUR) > 50000:
            _COLOUR.popitem(last=False)
    return v


def _cell_patches(cell: int, grid: int) -> list[int]:
    """8×8 cell → its 2×2 patch ids in the 16×16 patch grid."""
    f = PATCHES // grid
    r, c = divmod(cell, grid)
    return [(r * f + dr) * PATCHES + (c * f + dc) for dr in range(f) for dc in range(f)]


def rerank(index: ChipIndex, positives: Sequence[tuple[int, int]], negatives: Sequence[tuple[int, int]],
           candidates: Sequence[int], concept: str | None = None, neg_weight: float = 0.5
           ) -> tuple[list[tuple[int, float, int, dict[str, float]]], dict[str, Any]]:
    """→ ([(chip_row, score, cell, parts)], info) sorted best first."""
    cc = concept_conf(concept)["rerank"]
    rule, wc, wp = cc.get("colour"), float(cc.get("colour_weight", 0)), float(cc.get("patch_weight", 1))
    ex_rows = [i for i, _ in positives] + [i for i, _ in negatives]
    T, RGB, dev = patch_tokens(index, ex_rows + list(candidates))
    g = index.grid

    def ex_vec(k: int, cell: int) -> np.ndarray:
        ps = _cell_patches(cell, g)
        if rule:  # the patch in the example cell that best shows the concept colour
            col = colour_patches(RGB[k], rule).reshape(-1)[ps]
            if col.max() > 0.05:
                return T[k, ps[int(np.argmax(col))]]
        v = T[k, ps].mean(0)
        return v / max(np.linalg.norm(v), 1e-9)

    q = np.mean([ex_vec(k, c) for k, (_, c) in enumerate(positives)], 0)
    if negatives:
        q = q - neg_weight * np.mean([ex_vec(len(positives) + k, c) for k, (_, c) in enumerate(negatives)], 0)
    q /= max(np.linalg.norm(q), 1e-9)
    off = len(ex_rows)
    S = T[off:] @ q  # (n, 256)
    out = []
    root = str(index.root)
    for j, i in enumerate(candidates):
        col = colour_cached((root, int(i)), RGB[off + j], rule).reshape(-1)
        comb = wp * S[j] + wc * col
        p = int(np.argmax(comb))
        pr, pc = divmod(p, PATCHES)
        cell = (pr * g // PATCHES) * g + (pc * g // PATCHES)
        out.append((int(i), float(comb[p]), cell, {"patch_sim": round(float(S[j, p]), 4), "colour": round(float(col[p]), 3)}))
    out.sort(key=lambda t: (-round(t[1], 6), index.ids[t[0]]))
    return out, {"device": dev, "candidates": len(candidates), "colour_weight": wc, "patch_weight": wp,
                 "colour_rule": bool(rule)}
