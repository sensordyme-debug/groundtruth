"""Geometry helpers. GeoJSON lives in EPSG:4326; all measurement happens in EPSG:32617 (UTM 17N)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

WGS84 = "EPSG:4326"
MEASURE_CRS = "EPSG:32617"
SQM_PER_ACRE = 4046.8564224

BBox = tuple[float, float, float, float]


@lru_cache(maxsize=8)
def transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def _reproject(geom, src: str, dst: str):
    """Vectorised reprojection (shapely 2 + pyproj over all coordinates at once; ~10× faster than shapely.ops)."""
    import numpy as np
    import shapely

    t = transformer(src, dst)

    def fn(xy):
        x, y = t.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, fn)


def to_measure(geom: BaseGeometry) -> BaseGeometry:
    return _reproject(geom, WGS84, MEASURE_CRS)


def to_wgs84(geom: BaseGeometry, src: str = MEASURE_CRS) -> BaseGeometry:
    return _reproject(geom, src, WGS84)


def area_m2(geom_wgs84: BaseGeometry | dict[str, Any]) -> float:
    g = shape(geom_wgs84) if isinstance(geom_wgs84, dict) else geom_wgs84
    return float(to_measure(g).area)


def area_acres(geom_wgs84: BaseGeometry | dict[str, Any]) -> float:
    return area_m2(geom_wgs84) / SQM_PER_ACRE


def length_m(geom_wgs84: BaseGeometry | dict[str, Any]) -> float:
    g = shape(geom_wgs84) if isinstance(geom_wgs84, dict) else geom_wgs84
    return float(to_measure(g).length)


def bbox_to_measure(b: BBox) -> BBox:
    return tuple(to_measure(box(*b)).bounds)  # type: ignore[return-value]


def bbox_polygon(b: BBox) -> dict[str, Any]:
    return mapping(box(*b))


def bbox_intersects(a: BBox, b: BBox) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
