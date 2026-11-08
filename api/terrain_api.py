"""Terrain API: lidar terrain display products + elevation profiles (plain JSON / PNG, no envelope).

  GET  /api/v1/terrain/{place}/meta              meta.json of data/terrain/<data_versions.terrain>/<Place>/ (or unavailable)
  GET  /api/v1/terrain/{place}/{name}.png        hillshade | elevation_tint (EPSG:3857 image; corners in meta)
  GET  /api/v1/terrain/{place}/rgb/{z}/{x}/{y}.png  Terrain-RGB (Mapbox encoding) XYZ tile, 404 when absent
  POST /api/v1/terrain/profile                   {line: [[lon, lat], ...], n} → groundtruth.terrain.profile

Every route sits under /api/ and is therefore protected by api.auth.middleware (MapLibre tile requests from the page
carry the session cookie). Paths are resolved and checked with is_relative_to the versioned terrain folder.
Filled terrain-RGB heights are visual-only (see meta.products.terrain_rgb.fill); measure with /terrain/profile.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from groundtruth import config
from groundtruth import terrain as T

router = APIRouter(prefix="/api/v1/terrain")
_PLACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
_IMAGES = ("hillshade", "elevation_tint")
_CACHE = {"Cache-Control": "private, max-age=86400"}


def _root() -> Path:
    return config.data_dir("terrain").resolve()


def _place_dir(place: str) -> Path:
    if not _PLACE.match(place):
        raise HTTPException(404, "unknown place")
    root = _root()
    d = (root / place.replace(" ", "_")).resolve()
    if not d.is_relative_to(root) or d == root:
        raise HTTPException(404, "unknown place")
    return d


def _file(d: Path, *parts: str) -> Path:
    p = d.joinpath(*parts).resolve()
    if not p.is_relative_to(d) or not p.is_file():
        raise HTTPException(404, "not found")
    return p


@router.get("/{place}/meta")
def terrain_meta(place: str) -> dict[str, Any]:
    _place_dir(place)
    return T.meta(place)


@router.get("/{place}/rgb/{z}/{x}/{y}.png")
def terrain_rgb_tile(place: str, z: int, x: int, y: int) -> FileResponse:
    if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(404, "tile out of range")
    p = _file(_place_dir(place), "terrain_rgb", str(z), str(x), f"{y}.png")
    return FileResponse(p, media_type="image/png", headers=_CACHE)


@router.get("/{place}/{name}.png")
def terrain_image(place: str, name: str) -> FileResponse:
    if name not in _IMAGES:
        raise HTTPException(404, "unknown terrain image")
    p = _file(_place_dir(place), f"{name}.png")
    return FileResponse(p, media_type="image/png", headers=_CACHE)


@router.get("/{place}/contours.geojson")
def terrain_contours(place: str) -> FileResponse:
    p = _file(_place_dir(place), "contours_5m.geojson")
    return FileResponse(p, media_type="application/geo+json", headers=_CACHE)


class ProfileIn(BaseModel):
    line: list[list[float]] = Field(..., min_length=2, max_length=500)
    n: int = Field(200, ge=2, le=T.MAX_SAMPLES)
    place: str | None = Field(None, max_length=64)


@router.post("/profile")
def terrain_profile(b: ProfileIn) -> dict[str, Any]:
    if any(len(p) != 2 for p in b.line):
        raise HTTPException(422, "line vertices must be [lon, lat]")
    try:
        return T.profile([(p[0], p[1]) for p in b.line], n=b.n, place=b.place)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
