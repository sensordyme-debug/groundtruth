"""Exhaustive object scan inside a box: every chip and every 16.8 m cell is scored; nothing is cut at a top-k.

1. Cell scores for ALL chips in the box (DINOv2 8×8 cells, query = mean(positive cells) − λ·mean(negative cells)).
2. Prefilter: chips whose best cell ≥ prefilter_sim, or (colour concepts) whose best 14 px patch has ≥ colour_min colour.
3. Full-resolution DINOv2 patch tokens (16×16 per chip) for the prefiltered chips; per patch
   score = patch_weight·cos(patch, q_patch) + colour_weight·colour_fraction.
4. Detection mask per patch: score ≥ score_min (and colour ≥ colour_min for colour concepts), and closer to the positives than
   to the negatives (the "not this" senses). Each 4-connected group of patches in a chip = one object at its best patch.
5. Objects within nms_m metres of a better one (overlapping chips, adjacent patches) are merged.
Thresholds come from conf/concepts.json ("detect"). Returns every object above threshold, best first.
"""

from __future__ import annotations

import concurrent.futures as cf
import io
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from PIL import Image

from groundtruth import config
from groundtruth.rerank import PATCHES, _cell_patches, colour_cached, concept_conf, patch_tokens


@dataclass
class Detection:
    row: int
    patch: int
    cell: int
    score: float
    patch_sim: float
    colour: float
    x_m: float
    y_m: float
    n_patches: int = 1
    parts: dict[str, Any] = field(default_factory=dict)


def _components(mask: np.ndarray) -> list[list[int]]:
    """4-connected components of a (16,16) bool mask → lists of flat patch ids."""
    seen = np.zeros_like(mask, bool)
    out = []
    H, W = mask.shape
    for r in range(H):
        for c in range(W):
            if mask[r, c] and not seen[r, c]:
                comp, stack = [], [(r, c)]
                seen[r, c] = True
                while stack:
                    y, x = stack.pop()
                    comp.append(y * W + x)
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and not seen[yy, xx]:
                            seen[yy, xx] = True
                            stack.append((yy, xx))
                out.append(comp)
    return out


def _thumb(index, i: int) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(index.thumb_jpeg(int(i)))).convert("RGB"))


def query_vectors(eng, positives: Sequence[tuple[int, int]], negatives: Sequence[tuple[int, int]],
                  concept: str | None, neg_weight: float = 0.5, soft: Sequence[tuple[int, int]] = (),
                  soft_weight: float | None = None):
    """Patch-level example vectors: per example cell, the patch with the most concept colour (else the cell mean).
    HARD negatives (the user's explicit "not this") also drive the per-object rule Sp > Sn + margin; SOFT negatives
    (the unpicked senses: other meanings of the word) only nudge the query.
    → (P (k,768), N_hard (m,768) or None, qp = normalised mean(P) − neg_weight·mean(N_hard) − soft_weight·mean(N_soft))."""
    idx = eng.index
    rule = concept_conf(concept)["rerank"].get("colour")
    if soft_weight is None:
        soft_weight = float(concept_conf(concept)["detect"].get("soft_neg_weight", 0.25))
    ex_rows = [i for i, _ in positives] + [i for i, _ in negatives] + [i for i, _ in soft]
    T_ex, RGB_ex, _ = patch_tokens(idx, ex_rows)

    def ex_vec(k: int, cell: int) -> np.ndarray:
        ps = _cell_patches(cell, idx.grid)
        if rule:
            from groundtruth.rerank import colour_patches

            cm = colour_patches(RGB_ex[k], rule).reshape(-1)[ps]
            if cm.max() > 0.05:
                return T_ex[k, ps[int(np.argmax(cm))]]
        v = T_ex[k, ps].mean(0)
        return v / max(np.linalg.norm(v), 1e-9)

    P = np.stack([ex_vec(k, c) for k, (_, c) in enumerate(positives)])
    N = np.stack([ex_vec(len(positives) + k, c) for k, (_, c) in enumerate(negatives)]) if negatives else None
    off = len(positives) + len(negatives)
    Ns = np.stack([ex_vec(off + k, c) for k, (_, c) in enumerate(soft)]) if soft else None
    qp = P.mean(0)
    if N is not None:
        qp = qp - neg_weight * N.mean(0)
    if Ns is not None:
        qp = qp - soft_weight * Ns.mean(0)
    qp /= max(np.linalg.norm(qp), 1e-9)
    return P, N, qp


def scan(eng, positives: Sequence[tuple[int, int]], negatives: Sequence[tuple[int, int]], rows: np.ndarray,
         concept: str | None, neg_weight: float = 0.5,
         soft: Sequence[tuple[int, int]] = ()) -> tuple[list[Detection], dict[str, Any]]:
    cc = concept_conf(concept)
    det, rr = cc["detect"], cc["rerank"]
    rule, wc, wp = rr.get("colour"), float(rr.get("colour_weight", 0)), float(rr.get("patch_weight", 1))
    idx = eng.index
    t0 = time.perf_counter()
    info: dict[str, Any] = {"chips_in_box": int(len(rows)), "cells_scanned": int(len(rows) * idx.n_cells)}
    # 1. cell scores for every chip in the box
    q = np.mean([idx.cells(i)[c] for i, c in positives], 0)
    if negatives:
        q = q - neg_weight * np.mean([idx.cells(i)[c] for i, c in negatives], 0)
    if soft:
        q = q - float(det.get("soft_neg_weight", 0.25)) * np.mean([idx.cells(i)[c] for i, c in soft], 0)
    q /= max(np.linalg.norm(q), 1e-9)
    chip_max = np.concatenate([(np.asarray(idx.dino[rows[k:k + 4096]], np.float32) @ q).max(1)
                               for k in range(0, len(rows), 4096)]) if len(rows) else np.zeros(0)
    keep = chip_max >= det["prefilter_sim"]
    info["ms_cells"] = round((time.perf_counter() - t0) * 1000)
    # 2. colour prefilter (threaded JPEG decode; colour maps cached across queries)
    root = str(idx.root)
    colour_max = np.zeros(len(rows))
    if rule:
        t1 = time.perf_counter()
        from groundtruth.rerank import colour_map

        with cf.ThreadPoolExecutor(8) as ex:
            colour_max = np.array(list(ex.map(lambda k: float(colour_map(idx, int(rows[k]), rule).max()), range(len(rows)))))
        keep |= colour_max >= det["colour_min"]
        info["ms_colour"] = round((time.perf_counter() - t1) * 1000)
    cand = [int(r) for r in rows[keep]]
    info["chips_prefiltered"] = len(cand)
    if not cand:
        return [], info
    # 3. full-res patch tokens (examples + candidates), patch-level query
    t2 = time.perf_counter()
    P, N, qp = query_vectors(eng, positives, negatives, concept, neg_weight, soft=soft)
    import torch

    from groundtruth.rerank import GPU_PATCHES, colour_map

    dets: list[Detection] = []
    B = 512
    for k in range(0, len(cand), B):
        rows_b = cand[k:k + B]
        T, dev = GPU_PATCHES.get_many(idx, rows_b)  # (b, 256, 768) on device, cached across queries
        info["device"] = dev
        with torch.inference_mode():
            qt = torch.from_numpy(qp.astype(np.float32)).to(T.device, T.dtype)
            Pt = torch.from_numpy(P.astype(np.float32)).to(T.device, T.dtype)
            S = (T @ qt).float().cpu().numpy()  # (b, 256)
            Sp = (T @ Pt.T).amax(-1).float().cpu().numpy()  # best positive similarity per patch
            Sn = None
            if N is not None:
                Nt = torch.from_numpy(N.astype(np.float32)).to(T.device, T.dtype)
                Sn = (T @ Nt.T).amax(-1).float().cpu().numpy()
        for j, i in enumerate(rows_b):
            colp = colour_map(idx, i, rule).reshape(-1) if rule else np.zeros(PATCHES * PATCHES)
            comb = wp * S[j] + wc * colp
            if rule and "sim_min" in det:
                # colour concepts: the colour is the primary evidence, DINOv2 similarity rejects look-alike shapes
                m = (colp >= det["colour_min"]) & (S[j] >= det["sim_min"])
            else:
                m = comb >= det["score_min"]
                if rule:
                    m &= colp >= det["colour_min"]
            if Sn is not None:
                m &= Sp[j] > Sn[j] + det.get("neg_margin", 0.0)
            if not m.any():
                continue
            x0, y0 = float(idx.meta["x0_m"][i]), float(idx.meta["y0_m"][i])
            pm = config.CHIP_M / PATCHES
            for comp in _components(m.reshape(PATCHES, PATCHES)):
                p = max(comp, key=lambda q_: comb[q_])
                pr, pc = divmod(p, PATCHES)
                dets.append(Detection(row=i, patch=p, cell=(pr * idx.grid // PATCHES) * idx.grid + (pc * idx.grid // PATCHES),
                                      score=float(comb[p]), patch_sim=float(S[j, p]), colour=float(colp[p]),
                                      x_m=x0 + (pc + 0.5) * pm, y_m=y0 + config.CHIP_M - (pr + 0.5) * pm,
                                      n_patches=len(comp)))
    info["ms_patches"] = round((time.perf_counter() - t2) * 1000)
    # 5. merge duplicates across chip borders / adjacent components
    dets.sort(key=lambda d: (-d.score, idx.ids[d.row], d.patch))
    kept: list[Detection] = []
    pts = np.zeros((0, 2))
    r2 = det["nms_m"] ** 2
    for d in dets:
        if len(pts) and (((pts - (d.x_m, d.y_m)) ** 2).sum(1) < r2).any():
            continue
        kept.append(d)
        pts = np.vstack([pts, (d.x_m, d.y_m)])
    info.update(raw_detections=len(dets), objects=len(kept), ms_total=round((time.perf_counter() - t0) * 1000))
    return kept, info
