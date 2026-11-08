"""Height from USGS 3DEP lidar (Phase 6): per-place DTM / DSM / nDSM rasters at 1 m in EPSG:32617, built by
scripts/build_lidar.py with PDAL and cached under data/lidar/<place>/. Until a place is built, heights come from
Overture (stated in limits).
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import numpy as np

from groundtruth import config


def _places() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]


def built_places() -> list[str]:
    d = config.data_dir("lidar")
    return sorted(p.name.replace("_", " ") for p in d.iterdir() if (p / "ndsm.tif").exists()) if d.exists() else []


def place_for(bbox: Sequence[float] | None) -> str | None:
    """The built lidar area (a demo place or a named extra area, box from its meta.json) containing the bbox centre."""
    if not bbox:
        return None
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for name in built_places():
        try:
            b = json.loads((config.data_dir("lidar") / name.replace(" ", "_") / "meta.json").read_text())["bbox"]
        except (OSError, KeyError):
            b = _places().get(name, {}).get("bbox")
        if b and b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
            return name
    return None


def available_for(bbox: Sequence[float] | None) -> bool:
    return place_for(bbox) is not None


def raster(place: str, kind: str = "ndsm"):
    import rasterio

    return rasterio.open(config.data_dir("lidar") / place.replace(" ", "_") / f"{kind}.tif")


_ARR: dict[tuple[str, str], tuple[np.ndarray, Any]] = {}


def array(place: str, kind: str = "ndsm") -> tuple[np.ndarray, Any]:
    """(H×W float32, affine transform) held in memory per place (Uptown nDSM ≈ 37 MB)."""
    key = (place, kind)
    if key not in _ARR:
        with raster(place, kind) as src:
            _ARR[key] = (src.read(1).astype(np.float32), src.transform)
    return _ARR[key]


def values(geom_m, place: str, kind: str = "ndsm") -> np.ndarray:
    """Raster values inside a geometry (EPSG:32617), all-touched=False (cell centres inside)."""
    from rasterio.features import geometry_mask
    from rasterio.windows import Window, from_bounds

    arr, tr = array(place, kind)
    b = geom_m.bounds
    w = from_bounds(*b, transform=tr).round_offsets().round_lengths()
    r0, c0 = max(int(w.row_off), 0), max(int(w.col_off), 0)
    r1, c1 = min(int(w.row_off + w.height) + 1, arr.shape[0]), min(int(w.col_off + w.width) + 1, arr.shape[1])
    if r1 <= r0 or c1 <= c0:
        return np.zeros(0, np.float32)
    sub = arr[r0:r1, c0:c1]
    from rasterio.windows import transform as wtransform

    m = ~geometry_mask([geom_m], out_shape=sub.shape, transform=wtransform(Window(c0, r0, c1 - c0, r1 - r0), tr))
    if not m.any():  # tiny object: take the cell under its centroid
        cx, cy = geom_m.centroid.x, geom_m.centroid.y
        col, row = ~tr * (cx, cy)
        row, col = int(row), int(col)
        return arr[row:row + 1, col:col + 1].ravel() if 0 <= row < arr.shape[0] and 0 <= col < arr.shape[1] else np.zeros(0)
    v = sub[m]
    return v[np.isfinite(v) & (v > -9000)]


def building_heights(feats: Sequence[dict[str, Any]], bbox, q: float = 90.0) -> dict[str, dict[str, Any]]:
    """Per building: the q-th percentile of nDSM inside its footprint (robust to edges / overhanging trees)."""
    from shapely.geometry import shape

    from groundtruth.geo import to_measure

    place = place_for(bbox)
    if place is None:
        return {}
    out = {}
    for f in feats:
        v = values(to_measure(shape(f["geometry"])), place)
        if len(v) >= 3:
            out[f["properties"].get("id")] = {"height_m": round(float(np.percentile(v, q)), 1), "source": "lidar"}
    return out


def height_stats(geom_m, place: str) -> dict[str, Any] | None:
    """height_m (90th pct nDSM), mean height and volume_m3 (Σ nDSM × cell area) for one geometry (EPSG:32617)."""
    v = values(geom_m, place)
    if not len(v):
        return None
    _arr, tr = array(place)
    px = abs(tr.a * tr.e)
    return {"height_m": round(float(np.percentile(v, 90)), 2), "mean_height_m": round(float(v.mean()), 2),
            "volume_m3": round(float(np.clip(v, 0, None).sum() * px), 1)}
