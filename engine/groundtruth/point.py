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


def clip_to(features: list[dict[str, Any]], geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the features whose geometry intersects a drawn polygon (EPSG:4326)."""
    poly = shape(geojson)
    return [f for f in features if f.get("geometry") and shape(f["geometry"]).intersects(poly)]
