"""Derived concepts: houses with pools, lakefront houses, houses with their own dock (pure geometry over verified objects).

house_with_pool  an Overture building that is residential where classed (else a small, non-commercial footprint) with a
                 verified pool within pool_to_house_max_m; the pool must not sit on the building (footprint overlap; the
                 lidar height rule already removed roof-top false pools). Each pool goes to its NEAREST qualifying house.
lakefront        a house within max_dist_m of a large lake (OSM water polygon ≥ lake_min_area_km2) with no major road
                 (OSM highway ≥ secondary) crossing the segment house → nearest shore point.
has_dock         a verified boat dock touching the lake, within max_dist_from_shore_point_m of the house's shore point;
                 each dock goes to its nearest lakefront house (one dock → one house).
All geometry in EPSG:32617; features out in EPSG:4326. Thresholds: conf/derived.json.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import numpy as np
from shapely.geometry import LineString, mapping, shape
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from groundtruth import config
from groundtruth.geo import SQM_PER_ACRE, to_measure, to_wgs84


def conf() -> dict[str, Any]:
    return json.loads((config.SENSES_PATH.parent / "derived.json").read_text())


def _is_house(props: dict[str, Any], area_m2: float, c: dict[str, Any]) -> bool:
    cls = props.get("class")
    if area_m2 < c["min_footprint_m2"]:
        return False
    if cls in c["exclude_classes"]:
        return False
    if cls in c["residential_classes"]:
        return True
    return cls is None and area_m2 <= c["unclassed_max_footprint_m2"]


def houses_with_pools(pools: Sequence[dict[str, Any]], buildings: Sequence[dict[str, Any]],
                      water=None) -> list[dict[str, Any]]:
    """pools / buildings: GeoJSON features (EPSG:4326); water: union of mapped water (EPSG:32617) — a "pool" in the lake
    is a boat or a dock roof. → one feature per house (building geometry) with its pools."""
    c = conf()["house_with_pool"]
    bg = [to_measure(shape(b["geometry"])) for b in buildings]
    keep = [k for k, g in enumerate(bg) if _is_house(buildings[k].get("properties") or {}, g.area, c)]
    if not keep:
        return []
    hg = [bg[k] for k in keep]
    tree = STRtree(hg)
    all_tree = STRtree(bg) if bg else None
    by_house: dict[int, list[tuple[dict[str, Any], float]]] = {}
    for p in pools:
        pg = to_measure(shape(p["geometry"]))
        if water is not None and pg.intersects(water):
            continue
        if all_tree is not None:  # pool on a building (any building, incl. non-houses): not a backyard pool
            on = sum(pg.intersection(bg[j]).area for j in all_tree.query(pg, predicate="intersects"))
            if on / max(pg.area, 1e-9) > c["pool_on_building_max_frac"]:
                continue
        cand = tree.query(pg.buffer(c["pool_to_house_max_m"]), predicate="intersects")
        if not len(cand):
            continue
        j = int(min(cand, key=lambda q: (hg[int(q)].distance(pg), int(q))))
        by_house.setdefault(j, []).append((p, float(hg[j].distance(pg))))
    out = []
    for j, ps in sorted(by_house.items(), key=lambda t: keep[t[0]]):
        b = buildings[keep[j]]
        pa = sum(float(p["properties"].get("area_m2") or to_measure(shape(p["geometry"])).area) for p, _ in ps)
        props = {"id": (b.get("properties") or {}).get("id") or f"b{keep[j]}", "kind": "house_with_pool",
                 "building_class": (b.get("properties") or {}).get("class"),
                 "footprint_m2": round(hg[j].area, 1), "pools": len(ps), "pool_area_m2": round(pa, 1),
                 "pool_area_acres": round(pa / SQM_PER_ACRE, 4), "pool_dist_m": round(min(d for _, d in ps), 1),
                 "pool_examples": [p["properties"].get("example") for p, _ in ps],
                 "pool_geometries": [p["geometry"] for p, _ in ps],
                 "confidence": round(float(np.mean([float(p["properties"].get("confidence") or 0.7) for p, _ in ps])), 3)}
        out.append({"type": "Feature", "id": props["id"], "geometry": b["geometry"], "properties": props})
    return out


def lakes(water: Sequence[dict[str, Any]]) -> Any:
    """Union of large water polygons (EPSG:32617)."""
    c = conf()["lakefront"]
    gs = [to_measure(shape(w["geometry"])) for w in water if w.get("geometry")
          and shape(w["geometry"]).geom_type in ("Polygon", "MultiPolygon")]
    big = [g.buffer(0) for g in gs if g.area >= c["lake_min_area_km2"] * 1e6]
    return unary_union(big) if big else None


def mark_lakefront(houses: list[dict[str, Any]], lake, major_roads: Sequence[dict[str, Any]],
                   buildings: Sequence[dict[str, Any]] = ()) -> None:
    """Adds lakefront (bool), dist_shore_m, shore_point (EPSG:4326 [lon, lat]) to each house in place. Lakefront = within
    max_dist_m of the lake, no major road AND no other building on the segment house → nearest shore point (a
    second-row house is not lakefront)."""
    c = conf()["lakefront"]
    roads = [to_measure(shape(r["geometry"])) for r in major_roads if r.get("geometry")]
    rtree = STRtree(roads) if roads else None
    bgs = [to_measure(shape(b["geometry"])) for b in buildings if b.get("geometry")]
    btree = STRtree(bgs) if bgs else None
    for h in houses:
        p = h["properties"]
        if lake is None:
            p.update(lakefront=False, dist_shore_m=None)
            continue
        hg = to_measure(shape(h["geometry"]))
        d = float(hg.distance(lake.boundary)) if not hg.intersects(lake) else 0.0
        p["dist_shore_m"] = round(d, 1)
        if d > c["max_dist_m"]:
            p["lakefront"] = False
            continue
        a, s = nearest_points(hg, lake.boundary)  # from the house's own edge to the shore
        seg = LineString([a, s])
        road = bool(rtree is not None and len(rtree.query(seg, predicate="intersects")))
        blocked = False
        if btree is not None and seg.length > 0.5:
            for j in btree.query(seg, predicate="intersects"):
                other = bgs[int(j)]
                if other.equals(hg) or other.intersection(hg).area > 0.5 * min(other.area, hg.area):
                    continue
                if other.intersection(seg).length > 1.0:
                    blocked = True
                    break
        p["lakefront"] = not road and not blocked
        p["road_between"] = road
        p["building_between"] = blocked
        sp = to_wgs84(s)
        p["shore_point"] = [round(sp.x, 7), round(sp.y, 7)]


def assign_docks(houses: list[dict[str, Any]], docks: Sequence[dict[str, Any]], lake,
                 homes: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Each verified dock touching the lake → its nearest lakefront HOME (any residential building on the shore, pool or
    not; ``homes`` = lakefront-marked features), measured from the dock to each home's shore point. A pool house has a
    dock only when that dock's nearest lakefront home is itself (a neighbour's dock is not yours).
    Adds has_dock, dock_count, dock_ids to houses; returns link lines (house centroid → dock) as features."""
    c = conf()["has_dock"]
    for h in houses:
        h["properties"].update(has_dock=False, dock_count=0, dock_ids=[])
    cand = [h for h in (homes or houses) if h["properties"].get("lakefront") and h["properties"].get("shore_point")]
    if lake is None or not cand:
        return []
    from groundtruth.geo import to_measure as tm
    from shapely.geometry import Point

    by_id = {h["properties"]["id"]: h for h in houses}
    sps = [tm(Point(*h["properties"]["shore_point"])) for h in cand]
    links = []
    for d in docks:
        dg = tm(shape(d["geometry"]))
        if dg.distance(lake) > c["dock_touch_water_m"] and not dg.intersects(lake):
            continue
        dist = np.array([dg.distance(s) for s in sps])
        j = int(np.argmin(dist))
        if dist[j] > c["max_dist_from_shore_point_m"]:
            continue
        h = by_id.get(cand[j]["properties"]["id"])
        if h is None:  # the dock belongs to a lakefront home without a pool
            continue
        hg_ = tm(shape(h["geometry"]))
        if (h["properties"].get("dist_shore_m") or 0) > c.get("max_house_shore_m", 1e9) or \
                hg_.distance(dg) > c.get("max_house_dock_m", 1e9):  # too far back from the water to own this dock
            continue
        p = h["properties"]
        p["has_dock"] = True
        p["dock_count"] += 1
        did = d["properties"].get("example") or d.get("id")
        p["dock_ids"].append(did)
        p.setdefault("dock_geometries", []).append(d["geometry"])
        hc = tm(shape(h["geometry"])).centroid
        line = to_wgs84(LineString([hc, dg.centroid]))
        links.append({"type": "Feature", "id": f"link:{p['id']}:{did}", "geometry": mapping(line),
                      "properties": {"kind": "dock_link", "house_id": p["id"], "dock_id": did,
                                     "dist_m": round(float(hc.distance(dg.centroid)), 1)}})
    return links


def dock_is_new(dock_geom_wgs84: dict[str, Any], img_new, img_old, c: dict[str, Any] | None = None) -> dict[str, Any]:
    """Was this dock absent in the older imagery? Compares the dock footprint in both years (conf dock_new_since): new when
    2022 shows deck (bright pixels) and the older year shows water there (dark) with almost no deck, even after growing
    the footprint by grow_m. ``img_*`` are providers.base.Imagery windows in EPSG:32617 covering the dock."""
    from rasterio.features import geometry_mask

    c = c or conf()["dock_new_since"]
    gm = to_measure(shape(dock_geom_wgs84))

    def fracs(img, geom):
        luma = img.rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        inside = ~geometry_mask([mapping(geom)], out_shape=luma.shape, transform=img.transform)
        v = luma[inside]
        if v.size < 4:
            return None
        return {"deck": float((v >= c["bright_luma_min"]).mean()), "water": float((v <= c["water_luma_max"]).mean()),
                "px": int(v.size)}

    new, old = fracs(img_new, gm), fracs(img_old, gm.buffer(c["grow_m"]))
    if new is None or old is None:
        return {"new": None, "why": "no pixels under the dock"}
    is_new = new["deck"] >= c["deck_frac_2022_min"] and old["deck"] <= c["deck_frac_old_max"] and \
        old["water"] >= c["water_frac_old_min"]
    return {"new": bool(is_new), "deck_2022": round(new["deck"], 3), "deck_old": round(old["deck"], 3),
            "water_old": round(old["water"], 3)}
