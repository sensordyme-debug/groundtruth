"""Exposure analysis: which real features touch, or lie near, a hypothetical scenario extent, and how they group.

Pure geometry over GeoJSON-like features (EPSG:4326 in and out, measured in EPSG:32617), deterministic, no I/O.
The investigation engine (investigate.py ops ``exposure`` and ``cluster``) feeds it the scenario extent
(scenario.py, SYNTHETIC) and real layers (Overture buildings, OSM roads / bridges / facilities).

Wording rule: touching a hypothetical extent is *potential exposure under the scenario's assumptions*; lying near it
is *proximity*. Neither is a prediction of impact.
"""
from __future__ import annotations

from typing import Any, Sequence

from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from groundtruth.geo import to_measure, to_wgs84


def union_extent(extent: Sequence[dict[str, Any]]):
    """The extent features as one geometry (EPSG:4326), or None."""
    gs = [shape(f["geometry"]) for f in extent if f.get("geometry")]
    return unary_union(gs) if gs else None


def touching(extent_geom, items: Sequence[dict[str, Any]], *, measure_length: bool = False) -> list[dict[str, Any]]:
    """Features whose geometry intersects the extent; lines also get ``length_in_extent_m``. Input order kept."""
    if extent_geom is None or extent_geom.is_empty:
        return []
    pe = prep(extent_geom)
    out = []
    for f in items:
        g = shape(f["geometry"])
        if pe.intersects(g):
            p = dict(f.get("properties") or {})
            p["scenario_relation"] = "touches the hypothetical extent"
            if measure_length:
                p["length_in_extent_m"] = round(to_measure(g.intersection(extent_geom)).length, 1)
            out.append({"type": "Feature", "geometry": f["geometry"], "properties": p})
    return out


def near(extent_geom, items: Sequence[dict[str, Any]], within_m: float) -> list[dict[str, Any]]:
    """Features within ``within_m`` of the extent, with ``dist_to_extent_m`` (0 = touching), nearest first."""
    if extent_geom is None or extent_geom.is_empty:
        return []
    em = to_measure(extent_geom)
    out = []
    for f in items:
        d = em.distance(to_measure(shape(f["geometry"])))
        if d <= within_m:
            p = dict(f.get("properties") or {})
            p["dist_to_extent_m"] = round(d, 1)
            p["scenario_relation"] = "touches the hypothetical extent" if d == 0 else f"within {within_m:g} m of it (proximity, not impact)"
            out.append({"type": "Feature", "geometry": f["geometry"], "properties": p})
    out.sort(key=lambda f: (f["properties"]["dist_to_extent_m"], str(f["properties"].get("osm_id") or f["properties"].get("id") or "")))
    return out


def clusters(buildings: Sequence[dict[str, Any]], *, link_m: float = 120.0, min_size: int = 3,
             roads: Sequence[dict[str, Any]] = (), facilities: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Group buildings whose footprints lie within ``link_m`` of each other (single linkage, EPSG:32617), keep groups
    of at least ``min_size``, and describe each: building count, how many are outside FEMA's 100-year zone, road
    length touching the extent inside the group's area, the nearest facility. Ranked by a stated rule: more buildings,
    then more road length, then the lowest cluster key (deterministic). Returns cluster polygons (EPSG:4326)."""
    if not buildings:
        return []
    gm = [to_measure(shape(b["geometry"])) for b in buildings]
    tree = STRtree(gm)
    parent = list(range(len(gm)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, g in enumerate(gm):
        for j in tree.query(g.buffer(link_m)):
            j = int(j)
            if j > i and g.distance(gm[j]) <= link_m:
                a, b = find(i), find(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    groups: dict[int, list[int]] = {}
    for i in range(len(gm)):
        groups.setdefault(find(i), []).append(i)
    rm = [(to_measure(shape(r["geometry"])), r) for r in roads]
    fm = [(to_measure(shape(f["geometry"])), f) for f in facilities]
    out = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        hull_m = unary_union([gm[i] for i in members]).convex_hull.buffer(15)
        road_len = round(sum(g.intersection(hull_m).length for g, _ in rm), 1)
        outside = sum(1 for i in members if (buildings[i]["properties"].get("flood_kind") != "100yr"))
        nf = min(((g.distance(hull_m), f) for g, f in fm), key=lambda t: t[0], default=None)
        c = hull_m.centroid
        key = f"{round(c.x)}:{round(c.y)}"
        props = {"kind": "cluster", "n_buildings": len(members), "n_outside_fema_100yr": outside,
                 "road_length_in_extent_m": road_len, "area_m2": round(hull_m.area, 1), "_key": key,
                 "building_ids": sorted(str(buildings[i]["properties"].get("id")) for i in members)}
        if nf is not None:
            fp = nf[1]["properties"]
            props["nearest_facility"] = {"kind": fp.get("kind"), "name": fp.get("name"), "dist_m": round(nf[0], 1)}
        out.append({"type": "Feature", "geometry": mapping(to_wgs84(hull_m)), "properties": props})
    out.sort(key=lambda f: (-f["properties"]["n_buildings"], -f["properties"]["road_length_in_extent_m"], f["properties"]["_key"]))
    for r, f in enumerate(out, 1):
        p = f["properties"]
        p["cluster_id"], p["rank"] = f"C{r}", r
        p["rank_reason"] = (f"#{r}: {p['n_buildings']} buildings touch the hypothetical extent"
                            + (f", {p['n_outside_fema_100yr']} of them outside FEMA's 100-year zone" if p["n_outside_fema_100yr"] else "")
                            + (f"; {p['road_length_in_extent_m']:.0f} m of major road in the same area" if p["road_length_in_extent_m"] else "")
                            + ". Ranked by building count, then road length (an ordering, not a risk score).")
        p.pop("_key")
    return out
