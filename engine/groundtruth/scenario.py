"""Scenario engine: SYNTHETIC what-if layers, kept apart from real data.

A scenario reads real layers (lidar ground, FEMA's base flood elevation, OSM water, Overture buildings, OSM roads) and
writes hypothetical outcomes to data/scenarios/<v>/<id>/. Every record it writes carries:

  synthetic_id, is_synthetic=true, scenario_id, description, created_at, assumptions, real_sources, visualization,
  simulation_status

Nothing here is a prediction. The first kind, ``connected_bathtub``, raises a flat water surface to a stated level and
keeps the ground below that level that is connected to a seed water body. It has no rainfall, runoff, flow, timing,
drainage or structures, and the UI and reports must say so (``simulation_status``).

  build(sid)              compute every level of a scenario (scripts/build_scenarios.py)
  scenarios()             the defined scenarios + whether each is built
  meta(sid)               the built scenario: levels, counts, the FEMA consistency check
  extent(sid, offset_m)   GeoJSON of the hypothetical wet land at one level
  impacts(sid, offset_m)  buildings and road segments touching that extent
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from groundtruth import config

VERSION = "0.1.0"
STATUS = "SIMULATED: connected-bathtub what-if. Not a hydraulic, rainfall or flood-forecast model"


def conf() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "scenarios.json").read_text())


def _def(sid: str) -> dict[str, Any]:
    d = next((x for x in conf()["scenarios"] if x["id"] == sid), None)
    if d is None:
        raise KeyError(f"unknown scenario {sid!r}")
    return d


def scenario_dir(sid: str) -> Path:
    return config.data_dir("scenarios") / sid


def level_key(offset_m: float) -> str:
    return f"plus_{float(offset_m):.1f}m".replace(".", "_")


def scenarios() -> list[dict[str, Any]]:
    out = []
    for d in conf()["scenarios"]:
        built = (scenario_dir(d["id"]) / "meta.json").exists()
        out.append({k: d[k] for k in ("id", "place", "title", "description", "kind", "offsets_m")} |
                   {"built": built, "is_synthetic": True, "simulation_status": STATUS})
    return out


def meta(sid: str) -> dict[str, Any]:
    p = scenario_dir(sid) / "meta.json"
    if not p.exists():
        raise FileNotFoundError(f"scenario {sid} not built (scripts/build_scenarios.py {sid})")
    return json.loads(p.read_text())


def extent(sid: str, offset_m: float) -> dict[str, Any]:
    p = scenario_dir(sid) / f"extent_{level_key(offset_m)}.geojson"
    if not p.exists():
        raise FileNotFoundError(f"no level {offset_m} m for {sid}")
    return json.loads(p.read_text())


def impacts(sid: str, offset_m: float) -> dict[str, Any]:
    p = scenario_dir(sid) / f"impacts_{level_key(offset_m)}.json"
    if not p.exists():
        raise FileNotFoundError(f"no level {offset_m} m for {sid}")
    return json.loads(p.read_text())


def record(d: dict[str, Any], i: str, created: str, visualization: str, **props) -> dict[str, Any]:
    """The provenance block every synthetic record carries."""
    return {"synthetic_id": f"{d['id']}:{i}", "is_synthetic": True, "scenario_id": d["id"], "description": d["description"],
            "created_at": created, "assumptions": d["assumptions"],
            "real_sources": _real_sources(d), "visualization": visualization, "simulation_status": STATUS, **props}


def _real_sources(d: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": "USGS 3DEP lidar, NC Phase 4 Mecklenburg (bare earth)", "date": "acquired 2016, published 2019",
             "role": "ground elevation"},
            {"name": d["reference"]["name"], "date": "FIRM panels effective 2009-03-02 to 2018-11-16",
             "role": f"reference level {d['reference']['value_m']} m {d['reference']['datum']}"},
            {"name": "OpenStreetMap water bodies", "date": "extract 2026-11-06", "role": "seed water body"},
            {"name": "Overture Maps buildings 2026-10-21.0", "date": "release 2026-10-21", "role": "buildings touched"},
            {"name": "OpenStreetMap major roads", "date": "extract 2026-11-06", "role": "road segments touched"}]


# ------------------------------------------------------------------ build (connected bathtub)

def _seed_polygon(d: dict[str, Any]):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    wb = json.loads((config.data_dir("risk") / "shore" / "water_bodies.geojson").read_text())
    gs = [shape(f["geometry"]) for f in wb["features"] if (f["properties"].get("name") or "") == d["seed_water_body"]]
    if not gs:
        raise ValueError(f"seed water body {d['seed_water_body']!r} not found")
    return unary_union(gs)


def _window(r, window_lonlat):
    """A rasterio window for a lon/lat bbox (None = the whole raster)."""
    if not window_lonlat:
        return None
    from pyproj import Transformer
    from rasterio.windows import from_bounds

    t = Transformer.from_crs("EPSG:4326", r.crs, always_xy=True).transform
    x0, y0 = t(window_lonlat[0], window_lonlat[1])
    x1, y1 = t(window_lonlat[2], window_lonlat[3])
    return from_bounds(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), r.transform).round_offsets().round_lengths()


@lru_cache(maxsize=1)
def _grid(place: str, grid_m: float, window_lonlat: tuple | None = None):
    """The lidar DTM resampled (average) to grid_m, with its nodata mask and transform (EPSG:32617); optionally only a
    lon/lat window (a bounded build: memory scales with the window, not the place)."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling

    p = config.data_dir("lidar") / place.replace(" ", "_") / "dtm.tif"
    with rasterio.open(p) as r:
        win = _window(r, window_lonlat)
        W, H = (int(win.width), int(win.height)) if win is not None else (r.width, r.height)
        f = grid_m / r.res[0]
        h, w = int(H / f), int(W / f)
        a = r.read(1, window=win, out_shape=(h, w), resampling=Resampling.average, masked=True)
        base = r.window_transform(win) if win is not None else r.transform
        tr = base * base.scale(W / w, H / h)
        crs = r.crs
    dtm = np.asarray(a.filled(np.nan), dtype="float32")
    return dtm, tr, crs


def _measured_mask(place: str, grid_m: float, shape_hw, window_lonlat: tuple | None = None):
    """Cells with a real lidar ground measurement nearby: build_lidar fills unreturned cells with DSM = DTM, so a cell
    whose DSM equals its DTM AND has no neighbouring difference is treated as unmeasured (docs/CANOPY.md)."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from scipy.ndimage import maximum_filter

    base = config.data_dir("lidar") / place.replace(" ", "_")
    with rasterio.open(base / "dsm.tif") as a, rasterio.open(base / "dtm.tif") as b:
        win = _window(b, window_lonlat)
        dsm = a.read(1, window=win, out_shape=shape_hw, resampling=Resampling.average, masked=True).filled(np.nan)
        dtm = b.read(1, window=win, out_shape=shape_hw, resampling=Resampling.average, masked=True).filled(np.nan)
    diff = np.abs(dsm - dtm) > 0.01
    return maximum_filter(diff, size=max(3, int(round(6 / grid_m)) | 1)) & np.isfinite(dtm)


def connected_wet(dtm, measured, seed, level_m: float):
    """The connected bathtub: land cells (not seed) at or below ``level_m`` that are 4-connected to the seed water
    body through other such cells. Unmeasured cells (``measured`` False or NaN ground) are never wet, so water cannot
    cross a lidar gap: the outcome there is unknown, reported separately."""
    import numpy as np
    from scipy.ndimage import label

    low = (np.nan_to_num(dtm, nan=np.inf) <= level_m) & measured
    lab, _ = label(low | seed)
    keep = np.unique(lab[seed & (lab > 0)])
    return np.isin(lab, keep[keep > 0]) & ~seed


def build(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    import numpy as np
    from pyproj import Transformer
    from rasterio import features
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as stransform, unary_union

    from groundtruth.geo import to_measure

    d = _def(sid)
    if d["kind"] != "connected_bathtub":
        raise ValueError(f"unsupported scenario kind {d['kind']}")
    t0 = time.time()
    created = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out = out_dir or scenario_dir(sid)
    out.mkdir(parents=True, exist_ok=True)
    wl = tuple(d["window_lonlat"]) if d.get("window_lonlat") else None
    dtm, tr, crs = _grid(d["place"], float(d["grid_m"]), wl)
    measured = _measured_mask(d["place"], float(d["grid_m"]), dtm.shape, wl)
    to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    seed_ll = _seed_polygon(d)
    seed = stransform(to_utm, seed_ll)
    lake = features.rasterize([(mapping(seed), 1)], out_shape=dtm.shape, transform=tr, fill=0, dtype="uint8").astype(bool)
    cell_m2 = float(d["grid_m"]) ** 2
    unmeasured_land = (~measured) & (~lake)
    # buildings + major roads in the scenario window (real layers, read-only)
    west, north = tr * (0, 0)
    east, south = tr * (dtm.shape[1], dtm.shape[0])
    win_ll = stransform(to_ll, shape({"type": "Polygon", "coordinates": [[(west, south), (east, south), (east, north),
                                                                          (west, north), (west, south)]]}))
    blds = _buildings(win_ll.bounds)
    roads = _roads(win_ll)
    ref = float(d["reference"]["value_m"])
    fema = _fema_fringe(win_ll.bounds, ref, seed_ll)
    levels = []
    for off in d["offsets_m"]:
        lv = ref + float(off)
        conn = connected_wet(dtm, measured, lake, lv)
        polys = [shape(g) for g, v in features.shapes(conn.astype("uint8"), mask=conn, transform=tr, connectivity=4) if v == 1]
        land = unary_union(polys).simplify(float(d["grid_m"]), preserve_topology=True) if polys else None
        land_ll = stransform(to_ll, land) if land is not None and not land.is_empty else None
        area_m2 = round(float(conn.sum()) * cell_m2, 1)
        key = level_key(off)
        recs = []
        if land_ll is not None:
            parts = list(getattr(land_ll, "geoms", [land_ll]))
            parts.sort(key=lambda g: (-to_measure(g).area, round(g.centroid.x, 6), round(g.centroid.y, 6)))
            for i, g in enumerate(parts):
                recs.append({"type": "Feature", "geometry": mapping(g), "properties": record(
                    d, f"{key}:{i:05d}", created, "water_extent", level_m=round(lv, 3), offset_m=float(off),
                    datum=d["reference"]["datum"], area_m2=round(to_measure(g).area, 1))})
        (out / f"extent_{key}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": recs}))
        hit_b = _touching(land_ll, blds)
        hit_r = _touching(land_ll, roads, measure_len=True)
        imp = {"scenario_id": sid, "level_m": round(lv, 3), "offset_m": float(off), "is_synthetic": True,
               "simulation_status": STATUS,
               "buildings_touched": [record(d, f"{key}:b:{b['id']}", created, "building_touched", building_id=b["id"],
                                            building_class=b.get("class"), real_geometry_source="Overture 2026-10-21.0")
                                     for b in hit_b],
               "road_segments_touched": [record(d, f"{key}:r:{r['id']}", created, "road_touched", osm_id=r["id"],
                                                highway=r.get("highway"), name=r.get("name"), length_in_extent_m=r["len_m"])
                                         for r in hit_r]}
        (out / f"impacts_{key}.json").write_text(json.dumps(imp))
        lvl = {"offset_m": float(off), "level_m": round(lv, 3), "key": key, "land_area_m2": area_m2,
               "parts": len(recs), "buildings_touched": len(hit_b), "road_segments_touched": len(hit_r),
               "road_length_touched_m": round(sum(r["len_m"] for r in hit_r), 1)}
        if off == 0 and fema is not None and land is not None:
            lvl["fema_consistency"] = _consistency(land, stransform(to_utm, fema))
        levels.append(lvl)
    m = {"scenario_id": sid, "version": VERSION, "is_synthetic": True, "simulation_status": STATUS, "created_at": created,
         "definition": d, "real_sources": _real_sources(d), "grid_m": d["grid_m"], "crs_measure": str(crs),
         "unmeasured_land_m2": round(float(unmeasured_land.sum()) * cell_m2, 1),
         "unmeasured_note": "land cells without a lidar ground return are never counted as wet or dry",
         "window_lonlat": list(wl) if wl else None,
         "window_note": ("computed inside this window only: water connected to the lake only through ground outside the "
                         "window is not counted") if wl else None,
         "levels": levels, "build_s": round(time.time() - t0, 1)}
    (out / "meta.json").write_text(json.dumps(m, indent=1))
    return m


def _buildings(bounds) -> list[dict[str, Any]]:
    import duckdb
    from shapely import from_wkb

    p = config.DATA / "layers" / "overture_buildings.parquet"
    w, s, e, n = bounds
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
        rows = con.execute("SELECT id, class, geometry_wkb FROM read_parquet(?) WHERE "
                           "ST_Intersects(ST_GeomFromWKB(geometry_wkb), ST_MakeEnvelope(?, ?, ?, ?)) ORDER BY id",
                           [str(p), w, s, e, n]).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "class": r[1], "geom": from_wkb(r[2])} for r in rows]


def _roads(win_ll) -> list[dict[str, Any]]:
    from shapely.geometry import shape

    p = config.DATA / "layers" / "osm_major_roads.geojson"
    if not p.exists():
        return []
    out = []
    for i, f in enumerate(json.loads(p.read_text())["features"]):
        g = shape(f["geometry"])
        if g.intersects(win_ll):
            pr = f.get("properties") or {}
            out.append({"id": str(pr.get("osm_id") or pr.get("id") or f"road-{i}"), "highway": pr.get("highway"),
                        "name": pr.get("name"), "geom": g})
    return sorted(out, key=lambda r: r["id"])


def _touching(land_ll, items, measure_len: bool = False) -> list[dict[str, Any]]:
    from shapely.prepared import prep

    from groundtruth.geo import to_measure

    if land_ll is None or land_ll.is_empty:
        return []
    pl = prep(land_ll)
    hits = []
    for it in items:
        if pl.intersects(it["geom"]):
            h = {k: v for k, v in it.items() if k != "geom"}
            if measure_len:
                h["len_m"] = round(to_measure(it["geom"].intersection(land_ll)).length, 1)
            hits.append(h)
    return hits


def _fema_fringe(bounds, ref: float, seed_ll):
    """FEMA's own 100-year zone along the lake: AE polygons whose static BFE equals the reference, minus the lake."""
    from shapely.ops import unary_union

    from groundtruth import risk as R

    polys = [g for g, pr in R.flood_polygons(bounds) if pr.get("bfe_m") is not None and abs(float(pr["bfe_m"]) - ref) < 0.05]
    if not polys:
        return None
    return unary_union(polys).difference(seed_ll)


def _consistency(land_utm, fema_utm) -> dict[str, Any]:
    """How closely the bathtub at the reference level reproduces FEMA's mapped fringe (both in EPSG:32617). A
    plausibility check of the method, not a validation of either."""
    inter = land_utm.intersection(fema_utm).area
    union = land_utm.union(fema_utm).area
    return {"iou": round(inter / union, 3) if union else None,
            "fema_fringe_m2": round(fema_utm.area, 1), "bathtub_m2": round(land_utm.area, 1),
            "share_of_fema_fringe_covered": round(inter / fema_utm.area, 3) if fema_utm.area else None,
            "note": "bathtub at FEMA's static BFE vs FEMA's AE polygons that carry that BFE (lake fringe), lake excluded"}
