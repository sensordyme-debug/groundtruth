"""Risk facts per object (T4 "risk answers"). Pure functions over versioned data products; thresholds and wording in
conf/risk.json. Every fact is a per-feature property so a result can be explained (the WHY line).

  flood_zone, flood_kind ("100yr" | "500yr" | None), flood_floodway, bfe_m, flood_covered   FEMA NFHL (data/risk/<v>/flood)
  shore_dist_m, shore_name                                                                  OSM water (data/risk/<v>/shore)
  ground_elev_m, elev_above_bfe_m, elev_bfe_status                                          3DEP lidar DTM − FEMA STATIC_BFE
  height_m (lidar nDSM p90 when the feature has none), slope_deg, canopy_over_pct           3DEP lidar (+ NAIP colour rule)
  minutes_to_<category>                                                                     Valhalla isochrone bands

Geometry in EPSG:4326, measurement in EPSG:32617. Data: risk products under config.data_dir("risk"); lidar from
config.data_dir("lidar", data_version("lidar_risk")) when that folder exists (adds places), else the demo's lidar.
Nothing here is ever invented: a fact that can't be measured is None, with a status saying why.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
from shapely.geometry import box, shape

from groundtruth import config
from groundtruth.geo import to_measure, to_wgs84

_LOCK = threading.Lock()


def conf() -> dict[str, Any]:
    p = config.SENSES_PATH.parent / "risk.json"
    return _conf_cached(p.stat().st_mtime)


@lru_cache(maxsize=4)
def _conf_cached(_mtime: float) -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "risk.json").read_text())


def risk_dir() -> Path:
    return config.data_dir("risk")


def available() -> dict[str, bool]:
    d = risk_dir()
    return {"flood": (d / "flood" / "flood_zones.parquet").exists(), "shore": (d / "shore" / "shoreline.geojson").exists(),
            "lidar": bool(lidar_places())}


def _geom(f: dict[str, Any]):
    return shape(f["geometry"])


# ------------------------------------------------------------------ flood (FEMA NFHL)
_FLOOD_CACHE: "OrderedDict[tuple, list]" = OrderedDict()


def flood_meta() -> dict[str, Any]:
    p = risk_dir() / "flood" / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def flood_polygons(bounds: Sequence[float], table: str = "flood_zones") -> list[tuple[Any, dict[str, Any]]]:
    """Accurate (unsimplified) FEMA polygons whose bbox overlaps ``bounds`` (EPSG:4326): [(geometry, props)].
    DuckDB range query on the bbox columns of data/risk/<v>/flood/<table>.parquet (no full load into RAM)."""
    import duckdb
    from shapely import from_wkb

    p = risk_dir() / "flood" / f"{table}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"flood layer not built: {p} (scripts/build_risk_layers.py flood)")
    key = (str(p), table, tuple(round(v, 3) for v in bounds))
    with _LOCK:
        if key in _FLOOD_CACHE:
            _FLOOD_CACHE.move_to_end(key)
            return _FLOOD_CACHE[key]
    w, s, e, n = bounds
    cols = "fld_ar_id, fld_zone, zone_subty, sfha, static_bfe, len_unit, v_datum, geometry_wkb" + (
        ", kind, floodway, bfe_m" if table == "flood_zones" else "")
    con = duckdb.connect()
    try:
        rows = con.execute(f"SELECT {cols} FROM read_parquet(?) WHERE xmax >= ? AND xmin <= ? AND ymax >= ? AND ymin <= ? "
                           "ORDER BY fld_ar_id", [str(p), w, e, s, n]).fetchall()
        names = [c.strip() for c in cols.split(",")]
    finally:
        con.close()
    out = []
    for r in rows:
        d = dict(zip(names, r))
        g = from_wkb(d.pop("geometry_wkb"))
        out.append((g, d))
    with _LOCK:
        _FLOOD_CACHE[key] = out
        while len(_FLOOD_CACHE) > 32:
            _FLOOD_CACHE.popitem(last=False)
    return out


@lru_cache(maxsize=1)
def _county_prepared(boundary_json: str):
    from shapely.prepared import prep

    return prep(shape(json.loads(boundary_json)["geometry"]))


def flood_facts(feats: Sequence[dict[str, Any]], boundary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Per feature: flood_zone (FEMA FLD_ZONE of the most severe zone it touches), flood_kind (100yr | 500yr | None),
    flood_floodway, bfe_m (FEMA STATIC_BFE in metres where published, else None), flood_overlap_pct (share of the
    footprint inside that zone), flood_covered (inside the mapped area: Mecklenburg DFIRM 37119C)."""
    from shapely.strtree import STRtree

    if not feats:
        return []
    gs = [_geom(f) for f in feats]
    b = np.array([g.bounds for g in gs])
    bounds = (b[:, 0].min() - 1e-4, b[:, 1].min() - 1e-4, b[:, 2].max() + 1e-4, b[:, 3].max() + 1e-4)
    polys = flood_polygons(bounds)
    tree = STRtree([p[0] for p in polys]) if polys else None
    cov = _county_prepared(json.dumps(boundary)) if boundary else None
    out = []
    for g in gs:
        fact = {"flood_zone": None, "flood_kind": None, "flood_floodway": False, "bfe_m": None, "flood_overlap_pct": 0.0,
                "flood_covered": bool(cov.intersects(g)) if cov is not None else True}
        hits = [polys[int(j)] for j in tree.query(g, predicate="intersects")] if tree is not None else []
        if hits:
            def rank(h):
                k = h[1]["kind"]
                return (0 if k == "100yr" else 1, -g.intersection(h[0]).area, h[1]["fld_ar_id"] or "")

            hits.sort(key=rank)
            best = hits[0]
            gm = to_measure(g)
            inter = to_measure(g.intersection(best[0])) if gm.area > 0 else None
            fact.update(flood_zone=best[1]["fld_zone"], flood_kind=best[1]["kind"],
                        flood_floodway=any(h[1]["floodway"] for h in hits if h[1]["kind"] == best[1]["kind"]),
                        flood_overlap_pct=round(100 * inter.area / gm.area, 1) if inter is not None and gm.area else 100.0)
            bfes = [h[1]["bfe_m"] for h in hits if h[1]["kind"] == "100yr" and h[1]["bfe_m"] is not None]
            if bfes:  # the zone it touches publishes a static BFE (lake / ponding zones); the highest is conservative
                fact["bfe_m"] = round(max(bfes), 2)
        out.append(fact)
    return out


# ------------------------------------------------------------------ shore (OSM water)
@lru_cache(maxsize=2)
def _shore(path: str, _mtime: float):
    from shapely.geometry import LineString
    from shapely.strtree import STRtree

    fc = json.loads(Path(path).read_text())
    segs, names = [], []
    for f in fc["features"]:
        g = to_measure(shape(f["geometry"]))
        for part in getattr(g, "geoms", [g]):
            cs = list(part.coords)
            for k in range(0, len(cs) - 1, 8):  # short pieces → a fast nearest query
                piece = LineString(cs[k: min(k + 9, len(cs))])
                if piece.length > 0:
                    segs.append(piece)
                    names.append(f["properties"]["name"])
    return segs, names, STRtree(segs)


def shore_facts(feats: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per feature: shore_dist_m (metres from the object's outline to the shoreline of the main water bodies; 0 when
    it touches or crosses the shore) and shore_name. None beyond 20 km."""
    p = risk_dir() / "shore" / "shoreline.geojson"
    if not p.exists():
        raise FileNotFoundError(f"shoreline not built: {p} (scripts/build_risk_layers.py shore)")
    segs, names, tree = _shore(str(p), p.stat().st_mtime)
    out = []
    for f in feats:
        gm = to_measure(_geom(f))
        idx, dist = tree.query_nearest(gm, max_distance=20000, return_distance=True, all_matches=False)
        if len(idx):
            out.append({"shore_dist_m": round(float(dist[0]), 1), "shore_name": names[int(idx[0])]})
        else:
            out.append({"shore_dist_m": None, "shore_name": None})
    return out


# ------------------------------------------------------------------ lidar rasters (3DEP 2016 + NAIP colour rule)
def lidar_root() -> Path:
    d = config.data_dir("lidar", config.data_version("lidar_risk")) if _has_key("lidar_risk") else None
    return d if d is not None and d.exists() else config.data_dir("lidar")


def _has_key(k: str) -> bool:
    try:
        return k in json.loads((config.SENSES_PATH.parent / "data_versions.json").read_text())
    except OSError:
        return False


@lru_cache(maxsize=4)
def _lidar_places(root: str, risk: str) -> tuple[tuple[str, tuple[float, ...], str], ...]:
    """(place, bounds in EPSG:32617, folder) for every lidar place with an nDSM."""
    import rasterio

    out = []
    r = Path(root)
    if not r.exists():
        return ()
    for d in sorted(r.iterdir()):
        if not (d / "ndsm.tif").exists():
            continue
        with rasterio.open(d / "ndsm.tif") as s:
            b = tuple(s.bounds)
        out.append((d.name.replace("_", " "), b, str(d)))
    return tuple(out)


def lidar_places() -> tuple[tuple[str, tuple[float, ...], str], ...]:
    return _lidar_places(str(lidar_root()), str(risk_dir()))


def _raster_path(place: str, kind: str) -> Path | None:
    folder = place.replace(" ", "_")
    if kind in ("dtm", "ndsm", "dsm"):
        p = lidar_root() / folder / f"{kind}.tif"
    else:  # slope, canopy (risk products)
        p = risk_dir() / "lidar" / folder / f"{kind}.tif"
    return p if p.exists() else None


def lidar_meta(place: str) -> dict[str, Any]:
    p = lidar_root() / place.replace(" ", "_") / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def raster_values(feats: Sequence[dict[str, Any]], kind: str, reduce: Callable[[np.ndarray], float | None],
                  buffer_m: Callable[[dict[str, Any]], float] | float = 0.0, tile_m: float = 1000.0) -> tuple[list, list]:
    """Per feature: reduce(values of raster ``kind`` inside the (buffered) footprint) → (values, places).
    Features are grouped per lidar place and ~1 km tile; ONE windowed read per group (no whole-raster arrays in RAM).
    None where no raster covers the feature."""
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    from rasterio.windows import transform as wtransform

    places = lidar_places()
    vals: list = [None] * len(feats)
    where: list = [None] * len(feats)
    groups: dict[tuple, list[tuple[int, Any]]] = {}
    for k, f in enumerate(feats):
        gm = to_measure(_geom(f))
        bm = buffer_m(f) if callable(buffer_m) else buffer_m
        if bm:
            gm = gm.buffer(bm)
        c = gm.centroid
        pl = next((p for p in places if p[1][0] <= c.x <= p[1][2] and p[1][1] <= c.y <= p[1][3]), None)
        if pl is None:
            continue
        where[k] = pl[0]
        groups.setdefault((pl[0], int(c.x // tile_m), int(c.y // tile_m)), []).append((k, gm))
    for (pl, _tx, _ty), items in sorted(groups.items(), key=lambda t: (t[0][0], t[0][1], t[0][2])):
        path = _raster_path(pl, kind)
        if path is None:
            continue
        xs = np.array([g.bounds for _, g in items])
        bb = (xs[:, 0].min() - 2, xs[:, 1].min() - 2, xs[:, 2].max() + 2, xs[:, 3].max() + 2)
        with rasterio.open(path) as src:
            win = from_bounds(*bb, transform=src.transform).round_offsets().round_lengths()
            win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
            arr = src.read(1, window=win)
            tr = wtransform(win, src.transform)
            nod = src.nodata
        for k, gm in items:
            m = ~geometry_mask([gm], out_shape=arr.shape, transform=tr)
            if not m.any():  # smaller than a cell: the cell under the centroid
                col, row = ~tr * (gm.centroid.x, gm.centroid.y)
                row, col = int(row), int(col)
                if not (0 <= row < arr.shape[0] and 0 <= col < arr.shape[1]):
                    continue
                v = arr[row:row + 1, col:col + 1].ravel()
            else:
                v = arr[m]
            if nod is not None:
                v = v[v != nod]
            if v.dtype.kind == "f":
                v = v[np.isfinite(v) & (v > -9000)]
            vals[k] = reduce(v) if len(v) else None
    return vals, where


def _pct(q: float):
    return lambda v: round(float(np.percentile(v.astype(np.float32), q)), 2)


def ground_facts(feats: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """ground_elev_m: lowest adjacent grade proxy = the 10th percentile of the bare-earth DTM in the footprint grown by
    conf flood.lowest_grade_ring_m (metres, NAVD88 like FEMA's BFE)."""
    fc = conf()["flood"]
    v, _ = raster_values(feats, "dtm", _pct(fc["lowest_grade_percentile"]), fc["lowest_grade_ring_m"])
    return [{"ground_elev_m": None if x is None else round(x, 2)} for x in v]


def elevation_facts(feats: Sequence[dict[str, Any]], flood: Sequence[dict[str, Any]] | None = None,
                    boundary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """elev_above_bfe_m = ground_elev_m − bfe_m where FEMA publishes a static BFE for the 100-year zone the object
    touches. elev_bfe_status: ok | no_bfe (zone without a published BFE) | not_in_100yr (BFE doesn't apply) | no_lidar."""
    flood = list(flood) if flood is not None else flood_facts(feats, boundary)
    ground = ground_facts(feats)
    out = []
    for fl, gr in zip(flood, ground):
        d = {"ground_elev_m": gr["ground_elev_m"], "elev_above_bfe_m": None}
        if fl.get("flood_kind") != "100yr":
            d["elev_bfe_status"] = "not_in_100yr"
        elif fl.get("bfe_m") is None:
            d["elev_bfe_status"] = "no_bfe"
        elif gr["ground_elev_m"] is None:
            d["elev_bfe_status"] = "no_lidar"
        else:
            d["elev_above_bfe_m"] = round(gr["ground_elev_m"] - fl["bfe_m"], 2)
            d["elev_bfe_status"] = "ok"
        out.append(d)
    return out


def canopy_buffer(f: dict[str, Any], concept: str | None = None) -> float:
    b = conf()["canopy"]["buffer_m"]
    p = f.get("properties") or {}
    key = concept or p.get("concept") or ("pool" if p.get("kind") == "pool" else None)
    return float(b.get(key or "default", b["default"]))


def canopy_facts(feats: Sequence[dict[str, Any]], concept: str | None = None) -> list[dict[str, Any]]:
    """canopy_over_pct: % of the footprint (grown by conf canopy.buffer_m for the concept: pools +2 m, because an outline
    is the visible water) whose 1 m cell is canopy (lidar nDSM ≥ 3 m AND the NAIP 2022 pixel is green)."""
    def red(v):
        v = v[v != 255]
        return round(100.0 * float((v == 1).mean()), 1) if len(v) else None

    v, _ = raster_values(feats, "canopy", red, lambda f: canopy_buffer(f, concept))
    return [{"canopy_over_pct": x} for x in v]


def slope_facts(feats: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    v, _ = raster_values(feats, "slope", lambda a: round(float(np.median(a[a != 255])), 1) if (a != 255).any() else None)
    return [{"slope_deg": x} for x in v]


def height_facts(feats: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """height_m: the feature's own height_m when it has one (find_buildings: lidar or Overture), else the 90th
    percentile of the lidar nDSM inside the footprint; height_source says which."""
    need = [k for k, f in enumerate(feats) if (f.get("properties") or {}).get("height_m") is None]
    v, _ = raster_values([feats[k] for k in need], "ndsm", _pct(90)) if need else ([], [])
    got = dict(zip(need, v))
    out = []
    for k, f in enumerate(feats):
        p = f.get("properties") or {}
        if p.get("height_m") is not None:
            out.append({"height_m": p["height_m"], "height_source": p.get("height_source") or "feature"})
        elif got.get(k) is not None:
            out.append({"height_m": round(float(got[k]), 1), "height_source": "lidar"})
        else:
            out.append({"height_m": None, "height_source": None})
    return out


# ------------------------------------------------------------------ drive time bands (Valhalla isochrones)
def minutes_facts(eng, feats: Sequence[dict[str, Any]], category: str, minutes: float, mode: str = "drive"
                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """minutes_to_<category>: the smallest band (conf/risk.json travel.bands_min + the asked minutes) whose union of
    isochrones from the nearest places of that category reaches the object; None = beyond the largest band.
    → (facts, info {bands, origins, mode})."""
    from shapely.ops import unary_union
    from shapely.prepared import prep

    from groundtruth.vector import store

    tc = conf().get("travel", {"bands_min": [5, 10, 15], "max_origins": 12})
    bands = sorted({float(b) for b in tc["bands_min"]} | {float(minutes)})
    pc = eng._pc()
    rc = eng.resolve_category(category)
    if rc is None:
        raise KeyError(f"unknown place category {category!r}")
    cat_key, cat = rc
    costing = pc["modes"].get(str(mode).lower(), "auto")
    speed = {"auto": 25.0, "bicycle": 6.0, "pedestrian": 1.5}[costing]
    fu = to_measure(unary_union([_geom(f) for f in feats]))
    reach = speed * max(bands) * 60
    fb = fu.bounds
    bb = to_wgs84(box(fb[0] - reach, fb[1] - reach, fb[2] + reach, fb[3] + reach)).bounds
    fc, _ = store().places(bb, cat.get("category", []), taxonomy=cat.get("taxonomy", []))
    cand = sorted(((to_measure(shape(f["geometry"]).centroid), f["properties"].get("name") or cat_key, f["id"])
                   for f in fc["features"]), key=lambda c: (fu.distance(c[0]), c[2]))[: int(tc["max_origins"])]
    if not cand:
        raise KeyError(f"no '{cat_key}' places within reach")
    areas = []
    for b in bands:
        polys = []
        for c, _nm, _id in cand:
            ll = to_wgs84(c)
            polys.append(to_measure(shape(eng.provider.isochrone(ll.x, ll.y, b, costing)["geometry"])))
        areas.append((b, prep(unary_union(polys).buffer(0))))
    out = []
    for f in feats:
        gm = to_measure(_geom(f))
        k = next((j for j, (_b, a) in enumerate(areas) if a.intersects(gm)), None)
        d = {f"minutes_to_{cat_key}": None if k is None else bands[k],
             f"minutes_to_{cat_key}_from": None if not k else bands[k - 1]}
        if k is None:
            d[f"minutes_to_{cat_key}_over"] = bands[-1]
        out.append(d)
    info = {"category": cat_key, "bands_min": bands, "mode": costing,
            "origins": [{"name": nm, "id": i} for _c, nm, i in cand]}
    return out, info


# ------------------------------------------------------------------ WHY line + insights
def _fmt(t: str, **kw) -> str:
    try:
        return t.format(**kw)
    except (KeyError, ValueError, IndexError):
        return ""


def why_line(p: dict[str, Any], label: str | None = None, facts: Sequence[str] = ()) -> str:
    """Plain words from the feature's facts: its basic fact (size / height) + every risk fact it carries."""
    w = conf()["why"]
    P = w["parts"]
    parts: list[str] = []
    lab = (label or "").strip()
    if p.get("kind") == "house_with_pool" and p.get("pool_area_m2") is not None:
        parts.append(_fmt(P["pool_area_m2"], v=float(p["pool_area_m2"])))
    elif p.get("kind") == "lakefront_home" and p.get("footprint_m2") is not None:
        parts.append(_fmt(P["footprint_m2"], v=float(p["footprint_m2"])))
    elif p.get("height_m") is not None and ("height" in facts or p.get("class") is not None or lab.lower().startswith("build")):
        parts.append(f"{(lab or 'Building').capitalize()} " + _fmt(P["height_m"], v=float(p["height_m"])))
    elif p.get("area_m2") is not None:
        parts.append(_fmt(P["area_m2_object"], label=(lab or "Object").capitalize(), v=float(p["area_m2"])))
    if p.get("flood_kind") == "100yr" and p.get("flood_zone"):
        parts.append(_fmt(P["flood_zone"], v=p["flood_zone"]))
    elif p.get("flood_kind") == "500yr":
        parts.append(P["flood_kind_500yr"])
    elif "flood" in facts and "flood_kind" in p:
        parts.append(P["no_flood"])
    if p.get("lakefront") and "lakefront" in facts:
        parts.append(P["lakefront"])
    if p.get("has_dock"):
        parts.append(P["has_dock"])
    if p.get("shore_dist_m") is not None and ("shore" in facts or p.get("lakefront")):
        parts.append(P["shore_dist_zero"] if float(p["shore_dist_m"]) < 0.5 else _fmt(P["shore_dist_m"], v=float(p["shore_dist_m"])))
    if p.get("elev_above_bfe_m") is not None:
        v = float(p["elev_above_bfe_m"])
        parts.append(_fmt(P["elev_above_bfe_pos"] if v >= 0 else P["elev_above_bfe_neg"], v=abs(v)))
    elif p.get("elev_bfe_status") == "no_bfe" and "elevation" in facts:
        parts.append(P["elev_above_bfe_null"])
    if p.get("canopy_over_pct") is not None and "canopy" in facts:
        parts.append(_fmt(P["canopy_over_pct"], v=float(p["canopy_over_pct"])))
    if p.get("slope_deg") is not None and "slope" in facts:
        parts.append(_fmt(P["slope_deg"], v=float(p["slope_deg"])))
    for k, v in sorted(p.items()):
        if k.startswith("minutes_to_") and not k.endswith(("_over", "_from")):
            lbl = k[len("minutes_to_"):].replace("_", " ")
            lo = p.get(f"{k}_from")
            if v is not None and lo is not None:
                parts.append(_fmt(P["minutes_band"], lo=float(lo), hi=float(v), label=lbl))
            elif v is not None:
                parts.append(_fmt(P["minutes_to"], v=float(v), label=lbl))
            else:
                over = p.get(f"{k}_over")
                if over is not None:
                    parts.append(_fmt(P["minutes_to_over"], v=float(over), label=lbl))
    for k, v in sorted(p.items()):
        if k.startswith("dist_m_to_") and v is not None and "distance" in facts:
            parts.append(_fmt(P["dist_m_to"], v=float(v), label=LAYER_WORDS.get(k[len("dist_m_to_"):], k[len("dist_m_to_"):])))
    if p.get("change") == "new" and p.get("from_year"):
        parts.append(_fmt(P["new_since"], v=p["from_year"]))
    return w["sep"].join(x for x in parts if x)


LAYER_WORDS = {"osm_light_rail": "the light rail", "osm_i485": "I-485", "osm_i77": "I-77", "osm_i85": "I-85"}


def median(xs: Sequence[float | None]) -> float | None:
    v = [float(x) for x in xs if x is not None]
    return round(float(np.median(v)), 1) if v else None


def insights(summary: dict[str, Any], topics: Sequence[str], unit: str) -> list[str]:
    """1–3 short strings built only from ``summary`` (which goes into the same envelope's metrics)."""
    I = conf()["insights"]
    n = int(summary.get("count") or 0)
    out = []
    base = summary.get("of_count")
    if base is not None and base != n:
        pct = round(100 * n / base) if base else 0
        out.append(f"{n:,} of {base:,} {unit} ({pct}%)")
    else:
        out.append(_fmt(I["count"], n=f"{n:,}", unit=unit))
    if "flood" in topics and n:
        n100, n500 = summary.get("in_100yr", 0), summary.get("in_500yr_only", 0)
        if base is not None and base != n and summary.get("filter_kind") in ("100yr", "500yr", "any"):
            pass  # the count line already says how many are in the zone
        else:
            out.append(_fmt(I["in_100yr"], n=f"{n100:,}", pct=round(100 * n100 / n)))
            if n500 and len(out) < 3 and "elevation" not in topics:
                out.append(_fmt(I["in_500yr"], n=f"{n500:,}", pct=round(100 * n500 / n)))
    if "elevation" in topics and n:
        m = summary.get("median_elev_above_bfe_m")
        nk = summary.get("bfe_known", 0)
        n100 = summary.get("in_100yr", 0)
        if m is None:
            out.append(_fmt(I["bfe_unavailable"], n_missing=f"{n100 - nk:,}", n=f"{n100:,}") if n100 else
                       "height above the flood elevation: " + I["unavailable"])
        else:
            s = _fmt(I["median_above_bfe"] if m >= 0 else I["median_below_bfe"], v=abs(m))
            if nk < n100:
                s += f" ({_fmt(I['bfe_partial'], n_known=nk, n=n100)})"
            out.append(s)
    if "shore" in topics and summary.get("median_shore_dist_m") is not None and len(out) < 3:
        out.append(_fmt(I["median_shore"], v=round(summary["median_shore_dist_m"])))
    if "canopy" in topics and len(out) < 3:
        m = summary.get("median_canopy_over_pct")
        out.append(_fmt(I["median_canopy"], v=round(m)) if m is not None else "tree cover: " + I["unavailable"])
    if "travel" in topics and len(out) < 3 and summary.get("travel_label"):
        m = summary.get("median_minutes")
        if m is not None:
            out.append(_fmt(I["median_minutes"], v=m, label=summary["travel_label"]))
    if "height" in topics and len(out) < 3 and summary.get("median_height_m") is not None:
        out.append(_fmt(I["median_height"], v=summary["median_height_m"]))
    return out[:3]
