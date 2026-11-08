"""Terrain: elevation profiles from the lidar bare-earth DTM, and the terrain display products' metadata.

  profile(line_lonlat, n=200, place=None)  sample the DTM of a lidar place along a line (EPSG:4326 in, EPSG:32617 measure)
  meta(place)                              data/terrain/<data_versions.terrain>/<Place>/meta.json, or status "unavailable"
  measured_mask(dsm, dtm, radius_px)       True where the lidar really measured something (shared with the build script)

Any lidar place under risk.lidar_root() works (nothing here is specific to one place). Display products (hillshade,
hypsometric tint, terrain-RGB tiles, contours) are built by scripts/build_terrain.py; see docs/TERRAIN.md if present.

Measured vs filled. scripts/build_lidar.py fills every cell without returns (DTM := nearest ground cell + 5 m median,
DSM := DTM), so the DTM has no nodata and a filled cell looks like flat ground. A DTM cell counts as MEASURED only when
  (1) it lies within conf risk.json canopy.coverage_radius_m (5 m) of a cell whose DSM != DTM (a real return; the
      docs/CANOPY.md rule), AND
  (2) the DTM there is backed by ground returns (ground_support): part of Lake Norman HAS water-surface returns in the
      DSM, so rule (1) alone marks those lake blocks measured although their DTM is the nearest-shore fill.
The area outside the lidar project, open water and the interior of large roofs are therefore unmeasured, and profile()
returns elev_m = None there: values are never interpolated across unmeasured cells.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from groundtruth import config

VERSION = "1.0.0"
MAX_SAMPLES = 2000
SUPPORT_PX, SUPPORT_MIN_SHARE = 5, 0.5  # ground_support window half-size (cells) and textured-share threshold
COVER_M = 2.0  # DSM - DTM at or above this: the ground lies under a roof or canopy (bare earth may be interpolated there)
SOURCE = {
    "name": "USGS 3DEP lidar, project USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019 (bare-earth DTM, 1 m, Groundtruth build)",
    "url": "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019/ept.json",
    "date": "2016 (published 2019)",
    "licence": "Public domain (USGS 3DEP)",
}
VERTICAL_DATUM = ("NAVD88 metres (3DEP project convention; the GeoTIFFs carry only the horizontal CRS EPSG:32617, "
                  "so the vertical datum is not machine-verifiable from the rasters)")


def coverage_radius_m() -> float:
    try:
        return float(json.loads((config.SENSES_PATH.parent / "risk.json").read_text())["canopy"]["coverage_radius_m"])
    except (OSError, KeyError, ValueError):
        return 5.0


def _disk(r: int) -> np.ndarray:
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def return_mask(dsm: np.ndarray, dtm: np.ndarray, radius_px: int) -> np.ndarray:
    """True within ``radius_px`` of a cell with a real lidar return (DSM != DTM): the scripts/build_canopy.py
    coverage_mask rule (docs/CANOPY.md)."""
    from scipy import ndimage

    ok = np.isfinite(dsm) & np.isfinite(dtm) & (dsm > -9000) & (dtm > -9000)
    hit = ok & (dsm != dtm)
    if radius_px > 0:
        hit = ndimage.binary_dilation(hit, structure=_disk(radius_px))
    return hit & ok


def ground_support(dtm: np.ndarray, support_px: int = SUPPORT_PX, min_share: float = SUPPORT_MIN_SHARE) -> np.ndarray:
    """True where the DTM is backed by nearby ground returns rather than the lidar build's fill.

    The fill (nearest ground cell + 5×5 median) makes values that repeat exactly: flat patches over voids, straight
    Voronoi steps, and stripes copied from a line of ground cells (e.g. a bridge deck). A cell is "textured" when it
    differs from a vertical AND a horizontal neighbour; real ground returns (cm resolution) are textured almost
    everywhere (measured ≥ 94 % on Lake Norman land windows), filled lake ≤ 20 %. Supported = textured share in the
    (2·support_px+1)² window ≥ min_share."""
    from scipy import ndimage

    p = np.pad(dtm, 1, mode="edge")
    c = p[1:-1, 1:-1]
    tex = ((c != p[:-2, 1:-1]) | (c != p[2:, 1:-1])) & ((c != p[1:-1, :-2]) | (c != p[1:-1, 2:]))
    share = ndimage.uniform_filter(tex.astype(np.float32), size=2 * support_px + 1, mode="nearest")
    return share >= min_share


def measured_mask(dsm: np.ndarray, dtm: np.ndarray, radius_px: int) -> np.ndarray:
    """True where the bare-earth DTM is measured: a real return within ``radius_px`` (return_mask) AND the DTM is
    backed by ground returns (ground_support). Callers reading windows must pad them by PAD_PX(radius_px)."""
    return return_mask(dsm, dtm, radius_px) & ground_support(dtm)


def pad_px(radius_px: int) -> int:
    return max(radius_px, SUPPORT_PX + 1) + 1


# ------------------------------------------------------------------ products
def terrain_dir(place: str) -> Path:
    return config.data_dir("terrain") / place.replace(" ", "_")


def meta(place: str) -> dict[str, Any]:
    """The terrain products' meta.json for a place, or {"status": "unavailable", ...}."""
    p = terrain_dir(place) / "meta.json"
    if not p.exists():
        return {"status": "unavailable", "place": place.replace("_", " "), "version": config.data_version("terrain"),
                "reason": "terrain products not built for this place (scripts/build_terrain.py)"}
    m = json.loads(p.read_text())
    m.setdefault("status", "ok")
    return m


# ------------------------------------------------------------------ profile
def _pick_place(xy: np.ndarray, place: str | None):
    from groundtruth import risk

    places = risk.lidar_places()
    if place is not None:
        want = place.replace("_", " ").lower()
        return next((p for p in places if p[0].lower() == want), None)
    best, best_n = None, 0
    for p in places:  # sorted by folder name → deterministic tie-break
        b = p[1]
        k = int(((xy[:, 0] >= b[0]) & (xy[:, 0] <= b[2]) & (xy[:, 1] >= b[1]) & (xy[:, 1] <= b[3])).sum())
        if k > best_n:
            best, best_n = p, k
    return best


@lru_cache(maxsize=8)
def _lidar_meta(folder: str) -> dict[str, Any]:
    p = Path(folder) / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def profile(line_lonlat: Sequence[Sequence[float]], n: int = 200, place: str | None = None) -> dict[str, Any]:
    """Elevation profile along a line (lon/lat vertices, EPSG:4326) from a lidar place's bare-earth DTM.

    Returns dist_m (n evenly spaced stations, increasing), elev_m (metres, None where the lidar has no real return
    within the coverage radius, the DTM there is fill rather than ground, or the station is outside the raster: never
    interpolated), basis per station ("measured" | "under_cover" | "no_return" | "no_ground" | "outside"),
    line_length_m (EPSG:32617), measured_share, source, date."""
    import rasterio
    from rasterio.windows import Window
    from shapely.geometry import LineString

    from groundtruth.geo import to_measure

    pts = [(float(p[0]), float(p[1])) for p in line_lonlat]
    if len(pts) < 2 or any(not (math.isfinite(a) and math.isfinite(b)) for a, b in pts):
        raise ValueError("line needs at least 2 finite [lon, lat] vertices")
    if any(not (-180 <= a <= 180 and -90 <= b <= 90) for a, b in pts):
        raise ValueError("line vertices must be [lon, lat] in EPSG:4326")
    n = int(max(2, min(MAX_SAMPLES, n)))
    lm = to_measure(LineString(pts))
    length = float(lm.length)
    dist = np.linspace(0.0, length, n)
    xy = np.array([lm.interpolate(d).coords[0] for d in dist], dtype=np.float64)

    pl = _pick_place(xy, place)
    elev: list[float | None] = [None] * n
    basis = ["outside"] * n
    out: dict[str, Any] = {
        "tool": "terrain.profile", "version": VERSION, "place": pl[0] if pl else None, "n": n,
        "line_length_m": round(length, 2), "dist_m": [round(float(d), 2) for d in dist],
        "vertical_datum": VERTICAL_DATUM, "source": dict(SOURCE),
    }
    if pl is None:
        out.update(elev_m=elev, basis=basis, measured_share=0.0, status="unavailable",
                   reason="no lidar place covers this line")
        return out
    folder = Path(pl[2])
    lmeta = _lidar_meta(str(folder))
    out["source"]["date"] = lmeta.get("acquired", SOURCE["date"])
    out["acquired"] = lmeta.get("acquired", SOURCE["date"])
    R = int(round(coverage_radius_m()))
    P = pad_px(R)
    with rasterio.open(folder / "dtm.tif") as s_dtm, rasterio.open(folder / "dsm.tif") as s_dsm:
        inv = ~s_dtm.transform
        cols, rows = inv * (xy[:, 0], xy[:, 1])
        cols, rows = np.floor(cols).astype(np.int64), np.floor(rows).astype(np.int64)
        inside = (cols >= 0) & (cols < s_dtm.width) & (rows >= 0) & (rows < s_dtm.height)
        B = 256
        groups: dict[tuple[int, int], list[int]] = {}
        for i in np.flatnonzero(inside):
            groups.setdefault((int(rows[i] // B), int(cols[i] // B)), []).append(int(i))
        for (br, bc), idx in sorted(groups.items()):
            r0, c0 = max(0, br * B - P), max(0, bc * B - P)
            r1, c1 = min(s_dtm.height, (br + 1) * B + P), min(s_dtm.width, (bc + 1) * B + P)
            win = Window(c0, r0, c1 - c0, r1 - r0)
            dtm = s_dtm.read(1, window=win)
            dsm = s_dsm.read(1, window=win)
            ret = return_mask(dsm, dtm, R)
            sup = ground_support(dtm)
            for i in idx:
                rr, cc = rows[i] - r0, cols[i] - c0
                if not ret[rr, cc]:
                    basis[i] = "no_return"
                    continue
                if not sup[rr, cc]:
                    basis[i] = "no_ground"
                    continue
                elev[i] = round(float(dtm[rr, cc]), 2)
                basis[i] = "under_cover" if float(dsm[rr, cc] - dtm[rr, cc]) >= COVER_M else "measured"
    have = [e for e in elev if e is not None]
    out.update(
        status="ok" if len(have) == n else ("partial" if have else "unavailable"),
        elev_m=elev, basis=basis,
        measured_share=round(len(have) / n, 4),
        under_cover_share=round(basis.count("under_cover") / n, 4),
        min_elev_m=min(have) if have else None, max_elev_m=max(have) if have else None,
        method=(f"Bare-earth DTM (1 m, EPSG:32617) sampled at {n} evenly spaced stations along the line measured in "
                f"EPSG:32617 (nearest cell). A station is measured only within {R} m of a real lidar return "
                "(DSM != DTM) where the DTM is backed by ground returns (not the build's fill); elsewhere elev_m is None."),
        limits=["Lidar from 2016: earthworks since then are not reflected.",
                "Water has no ground returns, so lake surfaces are unmeasured (None): neither the water level nor the "
                "lake bed is reported.",
                "Inside large roofs the ground is unmeasured (basis 'no_ground').",
                f"basis 'under_cover' (surface >= {COVER_M:g} m above ground): bare earth beneath canopy or a roof edge, "
                "from leaf-off ground returns nearby."],
    )
    return out
