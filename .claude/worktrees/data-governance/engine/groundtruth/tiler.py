"""Chip grid: fixed, globally anchored grid in EPSG:32617 so chip ids are stable across runs and AOIs.

chip (col, row) covers x ∈ [col·stride, col·stride + chip], y ∈ [row·stride, row·stride + chip] (metres, UTM 17N).
chip_id = "{year}_e{col}_n{row}". Pixel (0,0) of a chip is its north-west corner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from groundtruth import config
from groundtruth.geo import MEASURE_CRS, WGS84, to_measure, transformer


@dataclass(frozen=True)
class Chip:
    chip_id: str
    year: int
    col: int
    row: int
    x0_m: float  # west (UTM 17N)
    y0_m: float  # south
    x1_m: float  # east
    y1_m: float  # north
    scene_id: str = ""

    @property
    def bounds_m(self) -> tuple[float, float, float, float]:
        return (self.x0_m, self.y0_m, self.x1_m, self.y1_m)

    def bbox_wgs84(self) -> tuple[float, float, float, float]:
        t = transformer(MEASURE_CRS, WGS84)
        xs, ys = t.transform([self.x0_m, self.x1_m, self.x1_m, self.x0_m],
                             [self.y0_m, self.y0_m, self.y1_m, self.y1_m])
        return (min(xs), min(ys), max(xs), max(ys))

    def polygon_wgs84(self) -> list[list[float]]:
        t = transformer(MEASURE_CRS, WGS84)
        xs, ys = t.transform([self.x0_m, self.x1_m, self.x1_m, self.x0_m, self.x0_m],
                             [self.y0_m, self.y0_m, self.y1_m, self.y1_m, self.y0_m])
        return [[round(x, 7), round(y, 7)] for x, y in zip(xs, ys)]


def chip_id(year: int, col: int, row: int) -> str:
    return f"{year}_e{col}_n{row}"


def make_chip(year: int, col: int, row: int, *, stride_m: float = config.STRIDE_M,
              chip_m: float = config.CHIP_M, scene_id: str = "") -> Chip:
    x0, y0 = col * stride_m, row * stride_m
    return Chip(chip_id(year, col, row), year, col, row, x0, y0, x0 + chip_m, y0 + chip_m, scene_id)


def chips_for_area(area_m: BaseGeometry, year: int, *, stride_m: float = config.STRIDE_M,
                   chip_m: float = config.CHIP_M) -> list[Chip]:
    """All grid chips whose footprint intersects ``area_m`` (a geometry in EPSG:32617), row-major N→S, W→E."""
    minx, miny, maxx, maxy = area_m.bounds
    c0 = math.floor((minx - chip_m) / stride_m) + 1
    c1 = math.floor(maxx / stride_m)
    r0 = math.floor((miny - chip_m) / stride_m) + 1
    r1 = math.floor(maxy / stride_m)
    p = prep(area_m)
    out = []
    for row in range(r1, r0 - 1, -1):
        for col in range(c0, c1 + 1):
            ch = make_chip(year, col, row, stride_m=stride_m, chip_m=chip_m)
            if p.intersects(box(*ch.bounds_m)):
                out.append(ch)
    return out


def chips_for_bbox(bbox_wgs84: Sequence[float], year: int, **kw) -> list[Chip]:
    return chips_for_area(to_measure(box(*bbox_wgs84)), year, **kw)


def assign_scenes(chips: Iterable[Chip], scenes: Sequence[tuple[str, Polygon]]) -> list[Chip]:
    """Assign each chip to one scene. ``scenes``: (scene_id, footprint in EPSG:32617), in priority order.
    Prefer the first scene that fully contains the chip; else the one with the largest overlap."""
    prepared = [(sid, geom, prep(geom)) for sid, geom in scenes]
    out = []
    for ch in chips:
        cb = box(*ch.bounds_m)
        sid = next((s for s, _, pg in prepared if pg.contains(cb)), None)
        if sid is None:
            best = max(prepared, key=lambda t: t[1].intersection(cb).area, default=None)
            if best is None or best[1].intersection(cb).area == 0:
                continue
            sid = best[0]
        out.append(Chip(ch.chip_id, ch.year, ch.col, ch.row, ch.x0_m, ch.y0_m, ch.x1_m, ch.y1_m, sid))
    return out
