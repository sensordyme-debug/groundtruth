"""PublicDataProvider: public data only (Census, USDA NAIP via Planetary Computer, OSM, FEMA, Overture).

Imagery is read straight from NAIP Cloud-Optimised GeoTIFFs with signed Planetary Computer URLs
(anonymous SAS tokens; no API key), warped onto the engine's EPSG:32617 grid.
Vector layers are read from local extracts in ``data/layers`` produced by ``scripts/fetch_layers.py``.
"""

from __future__ import annotations

import json
import os
import time
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import planetary_computer
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from shapely.geometry import box, mapping, shape

from groundtruth import config
from groundtruth.envelope import Source
from groundtruth.geo import MEASURE_CRS, bbox_intersects, to_measure, to_wgs84
from groundtruth.providers.base import BBox, DataProvider, Imagery, ImageryScene, TileSource

GDAL_ENV: dict[str, Any] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": 4,
    "GDAL_HTTP_RETRY_DELAY": 1,
    "VSI_CACHE": True,
    "VSI_CACHE_SIZE": 256 * 1024 * 1024,
    "GDAL_CACHEMAX": 512,
}

NAIP_LICENCE = "Public domain (USDA FSA NAIP); hosted by Microsoft Planetary Computer (PC Terms of Use)"


def registry_date(source_id: str) -> str:
    """The dataset's own date from the source registry (conf/sources.json), with its kind spelled out:
    "2026-11-06 (data timestamp)" for OSM, "2026-10-21 (release 2026-10-21.0)" for Overture. Never a file
    modification time (that is when the file was copied, not when the data was captured). "unavailable" when the
    registry has no date: unknown stays unknown."""
    import re

    p = Path(__file__).resolve().parents[1] / "conf" / "sources.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return "unavailable"
    rows = d.get("sources", []) if isinstance(d, dict) else d
    r = next((x for x in rows if x.get("source_id") == source_id), None)
    if not r:
        return "unavailable"
    acq, pub = str(r.get("acquisition_date") or ""), str(r.get("publication_date") or "")
    rel = re.search(r"release (\d{4}-\d{2}-\d{2}(?:\.\d+)?)", acq)
    if rel:
        return f"{rel.group(1)[:10]} (release {rel.group(1)})"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", acq)
    if m:
        return f"{m.group(1)} (data timestamp)"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", pub)
    if m:
        return f"{m.group(1)} (publication)"
    return "unavailable"


class PublicDataProvider(DataProvider):
    name = "public"

    def __init__(self, data_dir: Path | None = None, *, download_scenes: bool = True,
                 cog_cache: Path | None = None) -> None:
        self.data = Path(data_dir or config.DATA)
        self.download_scenes = download_scenes
        self.cog_cache = Path(cog_cache or os.environ.get("GROUNDTRUTH_COG_CACHE")
                              or Path.home() / ".cache" / "groundtruth" / "naip")

    # ---------- boundary + coverage ----------
    @cached_property
    def _boundary(self) -> dict[str, Any]:
        return json.loads((self.data / "boundary" / "mecklenburg.geojson").read_text())

    @cached_property
    def _coverage(self) -> dict[str, Any]:
        return json.loads((self.data / "naip" / "coverage.json").read_text())

    def get_boundary(self) -> dict[str, Any]:
        return self._boundary

    def imagery_years(self) -> list[int]:
        return list(self._coverage["full_years"])

    @property
    def primary_year(self) -> int:
        return int(self._coverage["primary_year"])

    # ---------- scenes ----------
    def _items(self, year: int) -> list[dict[str, Any]]:
        p = self.data / "naip" / f"items_{year}.json"
        if not p.exists():
            raise FileNotFoundError(f"{p} missing: run scripts/fetch_boundary_naip.py")
        return json.loads(p.read_text())

    def list_scenes(self, bbox: BBox, year: int) -> list[ImageryScene]:
        out = []
        for it in self._items(year):
            if bbox_intersects(tuple(it["bbox"]), bbox):
                out.append(ImageryScene(it["scene_id"], year, tuple(it["bbox"]), it["gsd_m"], it["href"],
                                        it.get("geometry")))
        # priority: newest acquisition first, then id (deterministic)
        dt = {it["scene_id"]: it.get("datetime", "") for it in self._items(year)}
        out.sort(key=lambda s: (dt.get(s.scene_id, ""), s.scene_id), reverse=True)
        return out

    def scene_footprints_m(self, bbox: BBox, year: int) -> list[tuple[str, Any]]:
        """(scene_id, footprint polygon in EPSG:32617) in priority order."""
        out = []
        for s in self.list_scenes(bbox, year):
            g = shape(s.geometry) if s.geometry else box(*s.bbox)
            out.append((s.scene_id, to_measure(g)))
        return out

    def _href(self, scene_id: str, year: int) -> str:
        for it in self._items(year):
            if it["scene_id"] == scene_id:
                return it["href"]
        raise KeyError(scene_id)

    # ---------- pixels ----------
    def read_grid(self, bounds_m: tuple[float, float, float, float], year: int, *, gsd_m: float = config.GSD_M,
                  scene_ids: list[str] | None = None, bands: tuple[int, ...] = (1, 2, 3),
                  retries: int = 3) -> Imagery:
        """Pixels exactly aligned to ``bounds_m`` (EPSG:32617) at ``gsd_m``. Scenes are composited in priority
        order (first valid pixel wins). Returns uint8 (H, W, len(bands)) and a ``valid`` mask in meta."""
        x0, y0, x1, y1 = bounds_m
        width, height = int(round((x1 - x0) / gsd_m)), int(round((y1 - y0) / gsd_m))
        dst_transform = from_origin(x0, y1, gsd_m, gsd_m)
        if scene_ids is None:
            bb = to_wgs84(box(*bounds_m)).bounds
            scene_ids = [s.scene_id for s in self.list_scenes(bb, year)]
        out = np.zeros((height, width, len(bands)), dtype=np.uint8)
        valid = np.zeros((height, width), dtype=bool)
        used = []
        for sid in scene_ids:
            if valid.all():
                break
            arr, mask = self._read_scene_window(sid, year, dst_transform, width, height, bands, retries)
            fill = mask & ~valid
            if fill.any():
                out[fill] = arr[fill]
                valid |= fill
                used.append(sid)
        bbox_wgs = tuple(to_wgs84(box(*bounds_m)).bounds)
        return Imagery(rgb=out, transform=dst_transform, crs=MEASURE_CRS, year=year, scene_ids=used,
                       bbox=bbox_wgs, meta={"valid": valid, "gsd_m": gsd_m})

    # ---------- local COG cache (whole-scene download is ~20x faster than remote windowed reads) ----------
    def local_scene_path(self, sid: str, year: int) -> Path:
        return self.cog_cache / str(year) / f"{sid}.tif"

    def ensure_local(self, sid: str, year: int, *, streams: int = 16, retries: int = 4) -> Path:
        """Download one scene COG with parallel HTTP range requests (atomic; resumable per scene)."""
        dst = self.local_scene_path(sid, year)
        if dst.exists():
            return dst
        import concurrent.futures as cf

        import httpx

        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_suffix(".part")
        url = planetary_computer.sign(self._href(sid, year))
        with httpx.Client(timeout=120) as c:
            size = int(c.head(url).headers["content-length"])
        chunk = max(8 << 20, -(-size // streams))
        ranges = [(s, min(s + chunk, size) - 1) for s in range(0, size, chunk)]
        with open(part, "wb") as f:
            f.truncate(size)

        def fetch(r: tuple[int, int]) -> int:
            for attempt in range(retries):
                try:
                    with httpx.Client(timeout=300) as c:
                        resp = c.get(url, headers={"Range": f"bytes={r[0]}-{r[1]}"})
                        resp.raise_for_status()
                        body = resp.content
                    if len(body) != r[1] - r[0] + 1:
                        raise IOError("short read")
                    with open(part, "r+b") as f:
                        f.seek(r[0])
                        f.write(body)
                    return len(body)
                except Exception:
                    if attempt == retries - 1:
                        raise
                    time.sleep(2 * (attempt + 1))
            return 0

        with cf.ThreadPoolExecutor(streams) as ex:
            total = sum(ex.map(fetch, ranges))
        if total != size:
            raise IOError(f"{sid}: got {total} of {size} bytes")
        os.replace(part, dst)
        return dst

    def _open_url(self, sid: str, year: int) -> str:
        local = self.local_scene_path(sid, year)
        if local.exists():
            return str(local)
        if config.offline():
            raise FileNotFoundError(f"{sid} ({year}) is not in the local COG cache and GROUNDTRUTH_OFFLINE=1")
        if self.download_scenes:
            return str(self.ensure_local(sid, year))
        return planetary_computer.sign(self._href(sid, year))

    def _read_scene_window(self, sid: str, year: int, dst_transform, width: int, height: int,
                           bands: tuple[int, ...], retries: int) -> tuple[np.ndarray, np.ndarray]:
        err: Exception | None = None
        for attempt in range(retries):
            try:
                url = self._open_url(sid, year)
                with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
                    with WarpedVRT(src, crs=MEASURE_CRS, transform=dst_transform, width=width, height=height,
                                   resampling=Resampling.bilinear, nodata=0, warp_mem_limit=512,
                                   warp_extras={"NUM_THREADS": os.environ.get("GROUNDTRUTH_WARP_THREADS", "4")}) as vrt:
                        data = vrt.read(indexes=list(bands))  # (B, H, W)
                        m = vrt.read_masks(1) > 0
                return np.transpose(data, (1, 2, 0)), m
            except Exception as e:  # network / SAS expiry: retry with a fresh signature
                err = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"failed to read {sid} after {retries} attempts: {err}")

    def get_imagery(self, bbox: BBox, year: int, *, gsd_m: float | None = None) -> Imagery:
        g = gsd_m or config.GSD_M
        bx = to_measure(box(*bbox)).bounds
        # snap outward to the pixel grid
        x0 = np.floor(bx[0] / g) * g
        y0 = np.floor(bx[1] / g) * g
        x1 = np.ceil(bx[2] / g) * g
        y1 = np.ceil(bx[3] / g) * g
        return self.read_grid((x0, y0, x1, y1), year, gsd_m=g)

    def render_tile(self, year: int, z: int, x: int, y: int, size: int = 256) -> bytes | None:
        """Web-Mercator PNG display tile. Returns None when the tile cannot be rendered locally (the caller then falls
        back to Planetary Computer, unless offline) and b"" when no imagery covers the tile.

        1) When the colour-balanced, feathered display mosaic (``data/tiles/mosaic/<data_versions["mosaic"]>/<year>/``,
           built by ``scripts/heavy/build_mosaic.py``) covers the WHOLE tile, the tile is rendered from it
           (``GROUNDTRUTH_MOSAIC=0`` turns this off). Display only: ``read_grid``/``get_imagery`` keep reading raw scenes.
        2) Otherwise the raw local scene COGs (``render_tile_raw``)."""
        if os.environ.get("GROUNDTRUTH_MOSAIC", "1") == "1":
            png = self.render_tile_mosaic(year, z, x, y, size)
            if png is not None:
                return png
        return self.render_tile_raw(year, z, x, y, size)

    def _mosaic_index(self, year: int) -> dict[str, Any] | None:
        """{block_m, cells: {(i, j): path}, version} for a year's display mosaic, or None (cached per version)."""
        ver = config.data_version("mosaic")
        cache = self.__dict__.setdefault("_mosaic_cache", {})
        if (ver, year) not in cache:
            root = self.data / "tiles" / "mosaic" / ver / str(year)
            idx = None
            if (root / "meta.json").exists():
                m = json.loads((root / "meta.json").read_text())
                if not m.get("partial"):
                    idx = {"version": ver, "block_m": float(m["grid"]["block_m"]),
                           "cells": {(b["i"], b["j"]): root / b["file"] for b in m["blocks"]}}
            cache[(ver, year)] = idx
        return cache[(ver, year)]

    def render_tile_mosaic(self, year: int, z: int, x: int, y: int, size: int = 256) -> bytes | None:
        """Tile from the display mosaic; None when the mosaic is absent or does not cover the whole tile."""
        idx = self._mosaic_index(year)
        if idx is None:
            return None
        import math

        import morecantile
        from rio_tiler.errors import TileOutsideBounds
        from rio_tiler.io import Reader
        from rio_tiler.mosaic import mosaic_reader
        from rio_tiler.mosaic.methods import FirstMethod

        from groundtruth.geo import WGS84, transformer

        tb = morecantile.tms.get("WebMercatorQuad").bounds(morecantile.Tile(x, y, z))
        x0, y0, x1, y1 = transformer(WGS84, MEASURE_CRS).transform_bounds(tb.left, tb.bottom, tb.right, tb.top,
                                                                          densify_pts=21)
        bm = idx["block_m"]
        paths = []
        for i in range(math.floor(x0 / bm), math.floor(x1 / bm) + 1):
            for j in range(math.floor(y0 / bm), math.floor(y1 / bm) + 1):
                p = idx["cells"].get((i, j))
                if p is None or not p.exists():
                    return None
                paths.append(p)

        def tiler(path, tx, ty, tz, **kw):
            with Reader(str(path)) as src:  # 3-band JPEG COG with an internal mask: no nodata value
                return src.tile(tx, ty, tz, tilesize=size, **kw)

        try:
            img, _ = mosaic_reader(paths, tiler, x, y, z, pixel_selection=FirstMethod(), threads=0)
        except Exception as e:  # nothing in the blocks here: let the raw scenes answer
            if isinstance(e, TileOutsideBounds) or type(e).__name__ == "EmptyMosaicError":
                return None
            raise
        # any hole (block at the data boundary, a masked strip) -> raw fallback: the mosaic never shows nodata where
        # the raw scenes may have imagery
        if img.mask is not None and bool((np.asarray(img.mask) == 0).any()):
            return None
        return img.render(img_format="PNG")

    def render_tile_raw(self, year: int, z: int, x: int, y: int, size: int = 256) -> bytes | None:
        """Web-Mercator PNG tile from the LOCAL COG cache via rio-tiler (TiTiler's tiling engine), scenes composited in
        priority order (first valid pixel wins). Returns None when any scene under the tile is not local (the caller
        then falls back to Planetary Computer, unless offline) and b"" when no scene covers the tile."""
        import morecantile
        from rio_tiler.errors import TileOutsideBounds
        from rio_tiler.io import Reader
        from rio_tiler.mosaic import mosaic_reader
        from rio_tiler.mosaic.methods import FirstMethod

        tb = morecantile.tms.get("WebMercatorQuad").bounds(morecantile.Tile(x, y, z))
        scenes = self.list_scenes((tb.left, tb.bottom, tb.right, tb.top), year)
        if not scenes:
            return b""
        paths = [self.local_scene_path(s.scene_id, year) for s in scenes]
        if not all(p.exists() for p in paths):
            return None

        def tiler(path, tx, ty, tz, **kw):
            with Reader(str(path)) as src:
                return src.tile(tx, ty, tz, indexes=(1, 2, 3), tilesize=size, nodata=0, **kw)

        try:
            img, _ = mosaic_reader(paths, tiler, x, y, z, pixel_selection=FirstMethod(), threads=0)
        except Exception as e:  # an all-empty mosaic raises EmptyMosaicError
            if isinstance(e, TileOutsideBounds) or type(e).__name__ == "EmptyMosaicError":
                return b""
            raise
        return img.render(img_format="PNG")

    def get_tiles(self, year: int) -> TileSource:
        return TileSource(url_template=f"/api/tiles/naip/{year}/{{z}}/{{x}}/{{y}}.png",
                          attribution="USDA NAIP (public domain) via Microsoft Planetary Computer", year=year,
                          min_zoom=10, max_zoom=18)

    # ---------- vector layers ----------
    OVERTURE = {"overture_buildings": "buildings", "overture_places": "places"}
    # Community infrastructure (scripts/build_community.py → data/community/<conf/data_versions.json version>/):
    # layer name → (file stem, facility kinds to keep or None for all). Only used when no data/layers file has the name.
    COMMUNITY = {
        "community_facilities": ("facilities", None),
        "community_hospitals": ("facilities", ("hospital",)),
        "community_fire_stations": ("facilities", ("fire_station",)),
        "community_ems": ("facilities", ("ambulance_station",)),
        "community_police": ("facilities", ("police",)),
        "community_schools": ("facilities", ("school",)),
        "community_shelters": ("facilities", ("shelter",)),
        "community_bridges": ("bridges", None),
        "community_waterways": ("waterways", None),
    }

    def _community_dir(self) -> Path:
        return self.data / "community" / config.data_version("community")

    def layer_names(self) -> list[str]:
        d = self.data / "layers"
        names = sorted(p.stem for p in d.glob("*.geojson")) if d.exists() else []
        cd = self._community_dir()
        return (names + [k for k in self.OVERTURE if (d / f"{k}.parquet").exists()]
                + [k for k, (stem, _) in self.COMMUNITY.items() if k not in names and (cd / f"{stem}.geojson").exists()])

    def _parsed(self, p: Path):
        """Parsed GeoJSON + shapely geometries + STRtree, cached per file (reloaded if the file changes)."""
        from shapely.strtree import STRtree

        cache = self.__dict__.setdefault("_layer_cache", {})
        key = (str(p), p.stat().st_mtime)
        if key not in cache:
            fc = json.loads(p.read_text())
            feats = [f for f in fc["features"] if f.get("geometry")]
            geoms = [shape(f["geometry"]) for f in feats]
            cache[key] = (fc, feats, geoms, STRtree(geoms) if geoms else None)
        return cache[key]

    def get_layer(self, name: str, bbox: BBox | None = None, *, detail: str = "web") -> dict[str, Any]:
        """``detail='web'`` (default) serves the simplified web build when one exists (scripts/build_web_layers.py);
        ``detail='raw'`` always serves the full-resolution layer. Overture layers come from the DuckDB store."""
        if name in self.OVERTURE and (self.data / "layers" / f"{name}.parquet").exists():
            from groundtruth.vector import store

            fc, _ms = store().query(self.OVERTURE[name], bbox)
            return fc
        web = self.data / "layers" / "web" / f"{name}.geojson"
        p = web if detail == "web" and web.exists() else self.data / "layers" / f"{name}.geojson"
        kinds = None
        if not p.exists() and name in self.COMMUNITY:
            stem, kinds = self.COMMUNITY[name]
            p = self._community_dir() / f"{stem}.geojson"
        if not p.exists():
            raise KeyError(f"unknown layer {name!r}; available: {self.layer_names()}")
        fc, feats, geoms, tree = self._parsed(p)
        if bbox is None and kinds is None:
            return fc
        if bbox is None:
            idx = range(len(feats))
        else:
            idx = sorted(int(i) for i in tree.query(box(*bbox), predicate="intersects")) if tree is not None else []
        return {"type": "FeatureCollection", "features": [
            feats[i] for i in idx if kinds is None or (feats[i].get("properties") or {}).get("kind") in kinds]}

    # ---------- routing (Valhalla, built by scripts/build_routing.py from the NC OSM extract) ----------
    @cached_property
    def _valhalla(self):
        import valhalla

        cfg = config.data_dir("routing") / "valhalla.json"
        if not cfg.exists():
            raise FileNotFoundError("no routing graph: run scripts/build_routing.py (gt-routing env)")
        return valhalla.Actor(str(cfg))

    def isochrone(self, lon: float, lat: float, minutes: float, costing: str = "auto") -> dict[str, Any]:
        """Travel-time polygon (GeoJSON Feature, EPSG:4326) from a point: costing auto | pedestrian | bicycle.
        Local Valhalla graph (OSM roads, Mecklenburg + 5 km); free-flow speeds, no live traffic."""
        cache = self.__dict__.setdefault("_iso_cache", {})
        key = (round(lon, 5), round(lat, 5), float(minutes), costing)
        if key not in cache:
            req = {"locations": [{"lat": lat, "lon": lon}], "costing": costing,
                   "contours": [{"time": float(minutes)}], "polygons": True, "denoise": 0.5, "generalize": 15}
            r = json.loads(self._valhalla.isochrone(json.dumps(req)))
            cache[key] = r["features"][0]
            if len(cache) > 2000:
                cache.pop(next(iter(cache)))
        return cache[key]

    def routing_source(self) -> Source:
        # the OSM DATA timestamp from the source registry (never the file's modification time, which is the copy date)
        return Source(name="OpenStreetMap roads (Geofabrik North Carolina extract), routed with Valhalla",
                      url="https://download.geofabrik.de/north-america/us/north-carolina.html",
                      date=registry_date("osm_geofabrik_nc"),
                      licence="ODbL 1.0 (© OpenStreetMap contributors); Valhalla MIT")

    def overture_source(self, theme: str) -> Source:
        return Source(name=f"Overture Maps {theme}", url="https://docs.overturemaps.org/attribution/",
                      date=registry_date("overture_places" if theme == "places" else "overture_buildings"),
                      licence=("CDLA-Permissive-2.0 / CC0 / Apache-2.0 per record" if theme == "places"
                               else "ODbL 1.0 (© OpenStreetMap contributors, Overture Maps Foundation)"))

    # ---------- provenance ----------
    def sources(self) -> list[Source]:
        cov = self._coverage
        out = [
            Source(name="US Census TIGERweb county boundary (Mecklenburg, GEOID 37119)",
                   url="https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1",
                   date=cov.get("checked", ""), licence="Public domain (US Government work)"),
        ]
        for y in cov["full_years"]:
            v = cov["years"][str(y)] if str(y) in cov["years"] else cov["years"][y]
            out.append(Source(name=f"USDA NAIP {y}", url="https://planetarycomputer.microsoft.com/dataset/naip",
                              date=f"{v['date_min']}/{v['date_max']}", licence=NAIP_LICENCE))
        meta = self.data / "layers" / "_sources.json"
        if meta.exists():
            out += [Source(**s) for s in json.loads(meta.read_text())]
        cmeta = self._community_dir() / "meta.json"
        if cmeta.exists():
            src = json.loads(cmeta.read_text())["source"]
            out.append(Source(name=f"OpenStreetMap community infrastructure (community {self._community_dir().name})",
                              url="https://www.openstreetmap.org/copyright", date=src.get("osm_timestamp", ""),
                              licence=f"{src.get('licence', 'ODbL 1.0')} "
                                      f"({src.get('attribution', '© OpenStreetMap contributors')})"))
        return out

    def naip_source(self, year: int) -> Source:
        return next(s for s in self.sources() if s.name == f"USDA NAIP {year}")
