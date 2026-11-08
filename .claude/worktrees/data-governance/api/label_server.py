"""Labelling server: the human test-set tool. Runs as its own long-lived process (port 8765).

Kept separate from the engine API so engine work never forces a restart of the labelling page.
Serves: the page (/label), NAIP tiles (cached proxy of Planetary Computer's mosaic tiler),
the county boundary, OSM place search (Nominatim, bounded to Mecklenburg, cached) and the label store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel
from shapely.geometry import Point, shape
from shapely.geometry import box as shp_box

from groundtruth.labels import CONCEPTS, KINDS, LabelStore

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("GROUNDTRUTH_DATA", ROOT / "data"))
PAGE = ROOT / "web" / "label" / "index.html"
PC_DATA = "https://planetarycomputer.microsoft.com/api/data/v1"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "Groundtruth/0.1 (CrownHacks hackathon labelling tool)"

log = logging.getLogger("label_server")
app = FastAPI(title="Groundtruth labelling", version="0.1.0")
store = LabelStore(DATA / "labels")

_boundary = json.loads((DATA / "boundary" / "mecklenburg.geojson").read_text())
_coverage = json.loads((DATA / "naip" / "coverage.json").read_text())
_county_geom = shape(_boundary["geometry"])
COUNTY_BBOX = tuple(round(v, 6) for v in _county_geom.bounds)

_http: httpx.AsyncClient | None = None
_search_ids: dict[int, str] = {}
_search_lock = asyncio.Lock()
_geo_cache_path = DATA / "cache" / "geocode.json"
_geo_cache: dict[str, Any] = json.loads(_geo_cache_path.read_text()) if _geo_cache_path.exists() else {}
_geo_last = 0.0
_geo_lock = asyncio.Lock()


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}, follow_redirects=True)
    return _http


# ---------------- page + config ----------------
@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/label")


@app.get("/label")
def page() -> FileResponse:
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "labels": len(store.labels()), "journal": str(store.journal)}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "concepts": list(CONCEPTS),
        "kinds": list(KINDS),
        "naip_full_years": _coverage["full_years"],
        "primary_year": _coverage["primary_year"],
        "secondary_year": _coverage["secondary_year"],
        "naip_dates": {
            str(y): [v["date_min"], v["date_max"]] for y, v in _coverage["years"].items() if v["full"]
        },
        "county_bbox": COUNTY_BBOX,
    }


@app.get("/api/boundary")
def boundary() -> dict[str, Any]:
    return _boundary


# ---------------- NAIP tiles (cached proxy) ----------------
async def _search_id(year: int, refresh: bool = False) -> str:
    async with _search_lock:
        if refresh or year not in _search_ids:
            body = {
                "collections": ["naip"],
                "filter-lang": "cql2-json",
                "filter": {"op": "and", "args": [
                    {"op": "=", "args": [{"property": "naip:year"}, str(year)]},
                    {"op": "=", "args": [{"property": "naip:state"}, "nc"]},
                ]},
                "sortby": [{"field": "datetime", "direction": "desc"}],
            }
            r = await http().post(f"{PC_DATA}/mosaic/register", json=body)
            r.raise_for_status()
            _search_ids[year] = r.json()["searchid"]
        return _search_ids[year]


def _tile_in_county(z: int, x: int, y: int) -> bool:
    import math

    n = 2**z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    w, s, e, nn = COUNTY_BBOX
    return not (lon1 < w - 0.02 or lon0 > e + 0.02 or lat1 < s - 0.02 or lat0 > nn + 0.02)


@app.get("/api/tiles/naip/{year}/{z}/{x}/{y}.png")
async def naip_tile(year: int, z: int, x: int, y: int) -> Response:
    if year not in _coverage["full_years"]:
        raise HTTPException(404, "year without full county coverage")
    if z < 8 or z > 19 or not _tile_in_county(z, x, y):
        return Response(status_code=204)
    cache = DATA / "cache" / "tiles" / "naip" / str(year) / str(z) / str(x) / f"{y}.png"
    if cache.exists():
        return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "max-age=86400"})
    params = {"collection": "naip", "assets": "image", "asset_bidx": "image|1,2,3", "nodata": "0"}
    for attempt in range(3):
        sid = await _search_id(year, refresh=attempt > 0)
        try:
            r = await http().get(
                f"{PC_DATA}/mosaic/{sid}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x.png", params=params
            )
        except httpx.HTTPError:
            await asyncio.sleep(0.5 * (attempt + 1))
            continue
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            os.replace(tmp, cache)
            return Response(r.content, media_type="image/png", headers={"Cache-Control": "max-age=86400"})
        if r.status_code == 404 and "Unknown search" in r.text:
            continue
        if r.status_code == 404:
            return Response(status_code=204)
        await asyncio.sleep(0.5 * (attempt + 1))
    raise HTTPException(502, "tile upstream failed")


# ---------------- place search (OSM Nominatim) ----------------
@app.get("/api/geocode")
async def geocode(q: str) -> list[dict[str, Any]]:
    global _geo_last
    key = q.strip().lower()
    if not key:
        return []
    if key in _geo_cache:
        return _geo_cache[key]
    w, s, e, n = COUNTY_BBOX
    async with _geo_lock:  # Nominatim policy: max 1 request/second
        results: list[dict[str, Any]] = []
        for query in (q, f"{q}, Mecklenburg County, North Carolina"):
            wait = 1.1 - (time.monotonic() - _geo_last)
            if wait > 0:
                await asyncio.sleep(wait)
            # Not "bounded": features straddling the county edge (Lake Norman) must still match.
            r = await http().get(NOMINATIM, params={
                "q": query, "format": "jsonv2", "limit": 10, "viewbox": f"{w},{n},{e},{s}",
                "countrycodes": "us",
            })
            _geo_last = time.monotonic()
            if r.status_code == 200:
                results = []
                for it in r.json():
                    bb = it["boundingbox"]  # [s, n, w, e]
                    rect = shp_box(float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1]))
                    inside = rect.intersection(_county_geom) if rect.area > 0 else None
                    pt = Point(float(it["lon"]), float(it["lat"]))
                    if not _county_geom.contains(pt) and (inside is None or inside.is_empty):
                        continue  # outside Mecklenburg
                    if not _county_geom.contains(pt) and inside is not None:
                        pt = inside.representative_point()
                    box_in = inside.bounds if inside is not None and not inside.is_empty else rect.bounds
                    results.append({
                        "name": it.get("display_name"), "lon": pt.x, "lat": pt.y, "bbox": list(box_in),
                        "osm": f"{it.get('osm_type')}/{it.get('osm_id')}", "type": it.get("type"),
                        "importance": float(it.get("importance") or 0),
                    })
                results.sort(key=lambda it: -it["importance"])
            if results:
                break
        _geo_cache[key] = results
        _geo_cache_path.parent.mkdir(parents=True, exist_ok=True)
        _geo_cache_path.write_text(json.dumps(_geo_cache, indent=1))
        return results


# ---------------- labels ----------------
class LabelIn(BaseModel):
    concept: str
    kind: str
    lon: float
    lat: float
    naip_year: int | None = None
    map_zoom: float | None = None
    note: str = ""
    id: str | None = None  # client-generated, makes retries idempotent


def _state(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {"counts": store.counts(), "total": len(store.labels())}
    if extra:
        out.update(extra)
    return out


@app.get("/api/labels")
def list_labels() -> dict[str, Any]:
    return _state({"geojson": store.geojson()})


@app.post("/api/labels")
def add_label(body: LabelIn) -> dict[str, Any]:
    try:
        lab = store.add(concept=body.concept, kind=body.kind, lon=body.lon, lat=body.lat,
                        naip_year=body.naip_year, map_zoom=body.map_zoom, note=body.note, label_id=body.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _state({"label": lab.feature()})


@app.post("/api/labels/undo")
def undo_label() -> dict[str, Any]:
    lab = store.undo()
    return _state({"undone": lab.feature() if lab else None})


@app.delete("/api/labels/{label_id}")
def delete_label(label_id: str) -> dict[str, Any]:
    lab = store.delete(label_id)
    if lab is None:
        raise HTTPException(404, "no such label")
    return _state({"deleted": lab.feature()})


@app.get("/api/labels/export.geojson")
def export_geojson() -> JSONResponse:
    return JSONResponse(store.geojson(), headers={
        "Content-Disposition": 'attachment; filename="groundtruth_labels.geojson"'})


@app.get("/api/labels/export.csv")
def export_csv() -> PlainTextResponse:
    return PlainTextResponse(store.csv(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="groundtruth_labels.csv"'})


@app.exception_handler(Exception)
async def _err(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"error": str(exc)}, status_code=500)
