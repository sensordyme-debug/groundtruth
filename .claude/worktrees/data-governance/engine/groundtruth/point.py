"""Point at anything (T2): turn a click or a box on the map into index examples ("<chip_id>#<cell>"), so find_all can
find every similar object in an area. No labels, no training: the examples are the user's clicks.

A point falls in up to four overlapping chips (stride < chip size); we take the chip whose centre is nearest (the most
context around the object) and the cell (CHIP_M / grid ≈ 16.8 m) that contains the point. A box becomes the cells whose
centres fall inside it (capped, nearest the box centre first), so the whole object, not one patch, is the example.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from shapely.geometry import Point, box, shape

from groundtruth import config
from groundtruth.geo import to_measure
from groundtruth.tiler import chip_id


def example_at(index, lon: float, lat: float, year: int) -> str | None:
    """The example id for a point (EPSG:4326), or None when no indexed chip covers it."""
    p = to_measure(Point(lon, lat))
    return _example_xy(index, p.x, p.y, year)


def _example_xy(index, x: float, y: float, year: int) -> str | None:
    s, c = config.STRIDE_M, config.CHIP_M
    best = None
    for col in (math.floor(x / s), math.floor(x / s) - 1):
        for row in (math.floor(y / s), math.floor(y / s) - 1):
            x0, y0 = col * s, row * s
            if not (x0 <= x < x0 + c and y0 <= y < y0 + c):
                continue
            cid = chip_id(year, col, row)
            if cid not in index.pos:
                continue
            d = (x - (x0 + c / 2)) ** 2 + (y - (y0 + c / 2)) ** 2
            if best is None or d < best[0]:
                best = (d, cid, x0, y0)
    if best is None:
        return None
    _, cid, x0, y0 = best
    g = index.grid
    size = c / g
    cc = min(g - 1, max(0, int((x - x0) // size)))
    rr = min(g - 1, max(0, int((y0 + c - y) // size)))  # rows count from the chip's north edge
    return f"{cid}#{rr * g + cc}"


def examples_in_box(index, bbox: Sequence[float], year: int, max_cells: int = 12) -> list[str]:
    """Example ids for the cells covering a box drawn around one object (nearest the box centre first, deduped)."""
    b = to_measure(box(*bbox))
    minx, miny, maxx, maxy = b.bounds
    size = config.CHIP_M / index.grid
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    pts = []
    x = minx + size / 2
    while x < maxx:
        y = miny + size / 2
        while y < maxy:
            pts.append((x, y))
            y += size
        x += size
    if not pts:
        pts = [(cx, cy)]
    pts.sort(key=lambda q: ((q[0] - cx) ** 2 + (q[1] - cy) ** 2, q[0], q[1]))
    out: list[str] = []
    for q in pts:
        ex = _example_xy(index, q[0], q[1], year)
        if ex and ex not in out:
            out.append(ex)
        if len(out) >= max_cells:
            break
    return out


def resolve(index, items: Sequence[dict[str, Any]], year: int) -> tuple[list[str], list[dict[str, Any]]]:
    """[{lon, lat} | {bbox} | {id}] → example ids, plus a record of what each item resolved to (for the envelope)."""
    ids: list[str] = []
    rec = []
    for it in items:
        if it.get("id"):
            got = [str(it["id"])]
        elif it.get("bbox"):
            got = examples_in_box(index, it["bbox"], year)
        elif it.get("lon") is not None and it.get("lat") is not None:
            e = example_at(index, float(it["lon"]), float(it["lat"]), year)
            got = [e] if e else []
        else:
            got = []
        rec.append({**{k: it[k] for k in ("lon", "lat", "bbox", "id") if k in it}, "examples": got})
        ids += [g for g in got if g not in ids]
    return ids, rec


def _unit(v):
    import numpy as np
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-8)


def _cell_vecs(index, ids: Sequence[str]):
    import numpy as np

    from groundtruth.tools import parse_example
    out = []
    for ex in ids:
        cid, cell = parse_example(ex)
        if cid in index.pos and cell is not None:
            out.append(index.cells(index.pos[cid])[cell])
    return _unit(np.stack(out)) if out else None


def calibrate(index, feats: list[dict[str, Any]], positives: Sequence[str], negatives: Sequence[str],
              bbox: Sequence[float] | None, year: int, conf: dict[str, Any],
              use_threshold: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-click calibration (no labels, no training): sample background patches from the same area, measure how similar
    they are to the examples, and keep only candidates above the ``bg_quantile`` of that background and closer to the
    examples than to any "not this" example. Each kept feature records ``sim_example`` and ``sim_threshold`` (cosine
    similarities: uncalibrated, not probabilities). Deterministic (seeded sample)."""
    import numpy as np

    from groundtruth.tiler import chips_for_bbox
    P = _cell_vecs(index, positives)
    if P is None or not feats:
        return feats, {"calibrated": False}
    if not use_threshold and not negatives:
        return feats, {"calibrated": False}
    N = _cell_vecs(index, negatives)
    rng = np.random.default_rng(int(conf.get("seed", 7)))
    if bbox:
        rows = [index.pos[c.chip_id] for c in chips_for_bbox(bbox, year) if c.chip_id in index.pos]
    else:
        rows = list(range(len(index)))
    rows = np.asarray(rows)
    n = int(conf.get("bg_samples", 3000))
    pick_r = rows[rng.integers(0, len(rows), n)]
    pick_c = rng.integers(0, index.grid * index.grid, n)
    bg = _unit(np.stack([index.cells(int(r))[int(c)] for r, c in zip(pick_r, pick_c)]))
    bg_sim = (bg @ P.T).max(axis=1)
    tau = float(np.quantile(bg_sim, float(conf.get("bg_quantile", 0.995)))) if use_threshold else -1.0
    margin = float(conf.get("neg_margin", 0.0))
    from groundtruth.tools import parse_example
    kept = []
    for f in feats:
        ex = (f.get("properties") or {}).get("example")
        if not ex:
            continue
        cid, cell = parse_example(ex)
        if cid not in index.pos or cell is None:
            continue
        v = _unit(index.cells(index.pos[cid])[cell])
        sp = float((P @ v).max())
        sn = float((N @ v).max()) if N is not None else -1.0
        if sp >= tau and sp > sn + margin:
            f["properties"].update(sim_example=round(sp, 3), sim_threshold=round(tau, 3),
                                   **({"sim_not_this": round(sn, 3)} if N is not None else {}))
            kept.append(f)
    # keep the scan's own ranking (patch-level score): re-sorting by the coarser cell similarity measurably hurt
    # precision@24 (docs/EVALUATION.md, "Point at anything")
    return kept, {"calibrated": True, "threshold": round(tau, 3), "bg_quantile": conf.get("bg_quantile"),
                  "bg_samples": n, "before": len(feats), "after": len(kept)}


def clip_to(features: list[dict[str, Any]], geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the features whose geometry intersects a drawn polygon (EPSG:4326)."""
    poly = shape(geojson)
    return [f for f in features if f.get("geometry") and shape(f["geometry"]).intersects(poly)]
