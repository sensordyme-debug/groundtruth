"""Community-infrastructure layers from the local OSM extract → data/community/<version>/ (new, versioned).

  GROUNDTRUTH_DATA=... python scripts/build_community.py [--version v1] [--force] [--pbf PATH]

Reads ONLY data already on disk (data/osm/north-carolina.osm.pbf, Geofabrik extract, ODbL 1.0) and
data/boundaries/v1/county.geojson. No downloads. Outputs (EPSG:4326 GeoJSON, features sorted by osm_id):

  facilities.geojson  community-serving facilities as Points (polygons → representative point, area_m2 kept).
                      Mecklenburg County plus a 10 km ring around it (``in_county`` false there) so access analysis
                      can find the nearest facility even across the county line. kind ∈ hospital, clinic,
                      fire_station, police, ambulance_station, school, college, university, shelter, townhall,
                      community_centre, library. Properties: osm_id, kind, name, ref, in_county, area_m2 (polygons),
                      has_emergency (hospitals only: emergency=yes), source, osm_timestamp. Schools: location and
                      name only. No people-related tags (no phone/email/contact/operator:person); critical utilities
                      (substations, water treatment) are not extracted.
  bridges.geojson     highway (motorway…residential, links, living_street, service) or railway ways tagged with a
                      truthy ``bridge`` value intersecting the Lake Norman demo bbox expanded by 2 km. LineStrings.
                      Properties: osm_id, kind ("bridge"), highway | railway, name, ref, length_m.
  waterways.geojson   waterway river/stream/canal/drain/ditch lines + dam/weir (own kind) intersecting the same
                      expanded bbox. Properties: osm_id, kind, name, length_m (lines) / area_m2 (dam polygons).
  meta.json           source, extract timestamp, extent + counts per file and kind, build time, peak RSS, method.

Method (low memory, no full node-location index): pass 1 streams the pbf once, keeping (a) the locations of
nodes inside the expanded demo bbox only, (b) tagged facility / waterway-structure nodes, (c) the node refs of
facility ways and of bridge/waterway ways that touch a kept node, and (d) member way ids of facility / dam
multipolygon relations. Pass 2 (only if such relations exist) reads just those member ways (C++ IdFilter).
Pass 3 reads just the still-missing node locations (C++ IdFilter). Geometries are then assembled in shapely;
lengths/areas are measured in EPSG:32617. Peak memory scales with the demo area + facility count, not the state.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from array import array
from pathlib import Path

import osmium
import shapely
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402

DATA = config.DATA
NC_PBF = DATA / "osm" / "north-carolina.osm.pbf"
COUNTY = DATA / "boundaries" / "v1" / "county.geojson"
DEMO_BBOX = (-80.958699, 35.427332, -80.844396, 35.514785)  # Lake Norman demo area (conf/places.json)
LINE_BUFFER_M = 2000.0
FACILITY_RING_M = 10000.0
SOURCE = "OpenStreetMap"
LICENCE = "ODbL 1.0"
ATTRIBUTION = "© OpenStreetMap contributors"

FACILITY_KINDS = ("hospital", "clinic", "fire_station", "police", "ambulance_station", "school", "college",
                  "university", "shelter", "townhall", "community_centre", "library")
WATER_KINDS = ("river", "stream", "canal", "drain", "ditch", "dam", "weir")
HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "motorway_link",
            "trunk_link", "primary_link", "secondary_link", "tertiary_link", "living_street", "service"}
RAILWAYS = {"rail", "light_rail", "subway", "tram", "narrow_gauge", "monorail", "preserved"}
WATER_LINES = {"river", "stream", "canal", "drain", "ditch"}
WATER_STRUCT = {"dam", "weir"}
FALSY = {"no", "0", "false"}


def facility_kind(t) -> str | None:
    """First matching kind; precedence follows FACILITY_KINDS order. Only tag *values* listed here are read."""
    am = t.get("amenity")
    if am == "hospital":
        return "hospital"
    if am == "clinic" or t.get("healthcare") == "clinic":
        return "clinic"
    if am in ("fire_station", "police"):
        return am
    if t.get("emergency") == "ambulance_station":
        return "ambulance_station"
    if am in ("school", "college", "university"):
        return am
    if t.get("social_facility") == "shelter" or t.get("emergency") == "assembly_point":
        return "shelter"
    if am in ("townhall", "community_centre", "library"):
        return am
    return None


def bridge_tag(t) -> tuple[str, str] | None:
    b = t.get("bridge")
    if not b or b.lower() in FALSY:
        return None
    if t.get("highway") in HIGHWAYS:
        return ("highway", t.get("highway"))
    if t.get("railway") in RAILWAYS:
        return ("railway", t.get("railway"))
    return None


def facility_props(t, kind: str) -> dict:
    """The ONLY facility tags carried: name, ref, and (hospitals) emergency=yes. No people-related tags."""
    p = {"kind": kind, "name": t.get("name"), "ref": t.get("ref")}
    if kind == "hospital":
        p["has_emergency"] = t.get("emergency") == "yes"
    return p


def extract_timestamp(pbf: Path) -> str:
    r = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING)
    try:
        return r.header().get("osmosis_replication_timestamp") or ""
    finally:
        r.close()


def expanded_bbox(b, m: float):
    return to_wgs84(to_measure(box(*b)).buffer(m, join_style="mitre"))


def _multipolygon(members, way_refs, locs):
    outer, inner = [], []
    for wid, role in members:
        c = [locs.get(r) for r in way_refs.get(wid, ())]
        if len(c) < 2 or any(x is None for x in c):
            continue
        (inner if role == "inner" else outer).append(LineString(c))
    if not outer:
        return None
    def poly(ls):
        u = unary_union(ls)  # nodes the member ways
        return unary_union(list(polygonize(list(getattr(u, "geoms", [u])))))
    g = poly(outer)
    if inner:
        g = g.difference(poly(inner))
    return None if g.is_empty else g


def scan(pbf: Path, line_area, log=print) -> dict:
    """The three streaming passes (see module docstring). Returns raw rows; no geometry filtering yet."""
    minx, miny, maxx, maxy = line_area.bounds
    locs: dict[int, tuple[float, float]] = {}
    fac_nodes, struct_nodes = [], []  # (id, props, (lon, lat))
    fac_ways, line_ways = [], []  # (id, props, refs)
    rels = []  # (id, props, [(way_id, role)], is_dam)
    skipped = {"facility_relations_not_multipolygon": 0}
    t0 = time.time()
    for o in osmium.FileProcessor(str(pbf)):
        if o.is_node():
            loc = o.location
            if not loc.valid():
                continue
            ll = (loc.lon, loc.lat)
            if minx <= ll[0] <= maxx and miny <= ll[1] <= maxy:
                locs[o.id] = ll
            if len(o.tags):
                t = o.tags
                k = facility_kind(t)
                if k:
                    fac_nodes.append((o.id, facility_props(t, k), ll))
                elif t.get("waterway") in WATER_STRUCT:
                    struct_nodes.append((o.id, {"kind": t.get("waterway"), "name": t.get("name")}, ll))
        elif o.is_way():
            t = o.tags
            if not len(t):
                continue
            k = facility_kind(t)
            if k:
                fac_ways.append((o.id, facility_props(t, k), array("q", (n.ref for n in o.nodes))))
                continue
            bt, wt = bridge_tag(t), t.get("waterway")
            if not bt and wt not in WATER_LINES and wt not in WATER_STRUCT:
                continue
            refs = array("q", (n.ref for n in o.nodes))
            if not any(r in locs for r in refs):
                continue
            if bt:
                line_ways.append((o.id, {"kind": "bridge", bt[0]: bt[1], "name": t.get("name"), "ref": t.get("ref")},
                                  refs))
            if wt in WATER_LINES or wt in WATER_STRUCT:
                line_ways.append((o.id, {"kind": wt, "name": t.get("name")}, refs))
        elif o.is_relation():
            t = o.tags
            k = facility_kind(t)
            is_dam = t.get("waterway") == "dam"
            if not k and not is_dam:
                continue
            if t.get("type") != "multipolygon":
                skipped["facility_relations_not_multipolygon"] += bool(k)
                continue
            props = facility_props(t, k) if k else {"kind": "dam", "name": t.get("name")}
            rels.append((o.id, props, [(m.ref, m.role) for m in o.members if m.type == "w"], is_dam and not k))
    log(f"pass 1 {time.time() - t0:.0f}s: area nodes={len(locs)} facility nodes={len(fac_nodes)} "
        f"facility ways={len(fac_ways)} line ways={len(line_ways)} relations={len(rels)}")

    member_ids = sorted({w for _, _, ms, _ in rels for w, _ in ms})
    way_refs: dict[int, array] = {}
    if member_ids:
        t0 = time.time()
        for w in osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(osmium.filter.IdFilter(member_ids)):
            way_refs[w.id] = array("q", (n.ref for n in w.nodes))
        log(f"pass 2 {time.time() - t0:.0f}s: relation member ways={len(way_refs)}/{len(member_ids)}")

    need = {r for _, _, refs in fac_ways + line_ways for r in refs if r not in locs}
    need |= {r for refs in way_refs.values() for r in refs if r not in locs}
    if need:
        t0 = time.time()
        for n in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.IdFilter(sorted(need))):
            if n.location.valid():
                locs[n.id] = (n.location.lon, n.location.lat)
        log(f"pass 3 {time.time() - t0:.0f}s: extra node locations={len(need)}")
    return {"locs": locs, "fac_nodes": fac_nodes, "struct_nodes": struct_nodes, "fac_ways": fac_ways,
            "line_ways": line_ways, "rels": rels, "way_refs": way_refs, "skipped": skipped}


def assemble(raw: dict, county_g, ring_g, line_area):
    """Raw rows → (facilities, bridges, waterways) as [(osm_id, shapely geom, props)]."""
    locs, skipped = raw["locs"], dict(raw["skipped"], broken_geometry=0)
    ring_p, line_p, county_p = prep(ring_g), prep(line_area), prep(county_g)
    fac, bridges, water = [], [], []

    def coords(refs):
        c = [locs.get(r) for r in refs]
        return None if len(c) < 2 or any(x is None for x in c) else c

    def add_fac(oid, props, g, polygon):
        pt = g.representative_point() if g.geom_type != "Point" else g
        if not ring_p.contains(pt):
            return
        p = {"osm_id": oid, **props, "in_county": bool(county_p.contains(pt))}
        if polygon:
            p["area_m2"] = round(to_measure(g).area, 1)
        fac.append((oid, Point(pt.x, pt.y), p))

    for i, props, ll in raw["fac_nodes"]:
        add_fac(f"node/{i}", props, Point(ll), False)
    for i, props, refs in raw["fac_ways"]:
        c = coords(refs)
        if c is None:
            skipped["broken_geometry"] += 1
            continue
        closed = len(c) >= 4 and c[0] == c[-1]
        g = Polygon(c) if closed else LineString(c)
        if closed and not g.is_valid:
            g = shapely.make_valid(g)
        add_fac(f"way/{i}", props, g, closed)
    for i, props, members, is_dam in raw["rels"]:
        g = _multipolygon(members, raw["way_refs"], locs)
        if g is None:
            skipped["broken_geometry"] += 1
            continue
        oid = f"relation/{i}"
        if is_dam:
            if line_p.intersects(g):
                water.append((oid, g, {"osm_id": oid, **props, "area_m2": round(to_measure(g).area, 1)}))
        else:
            add_fac(oid, props, g, True)
    for i, props, ll in raw["struct_nodes"]:
        g = Point(ll)
        if line_p.intersects(g):
            water.append((f"node/{i}", g, {"osm_id": f"node/{i}", **props}))
    for i, props, refs in raw["line_ways"]:
        c = coords(refs)
        if c is None:
            skipped["broken_geometry"] += 1
            continue
        g = LineString(c)
        if not line_p.intersects(g):
            continue
        oid = f"way/{i}"
        p = {"osm_id": oid, **props, "length_m": round(to_measure(g).length, 1)}
        (bridges if props["kind"] == "bridge" else water).append((oid, g, p))
    return fac, bridges, water, skipped


def sort_key(oid: str):
    typ, i = oid.split("/")
    return ({"node": 0, "way": 1, "relation": 2}[typ], int(i))


def round_geom(g, nd: int = 7):
    if g.geom_type == "Point":
        return Point(round(g.x, nd), round(g.y, nd))
    return shapely.set_precision(g, 10 ** -nd)


def feature_collection(rows, common: dict) -> dict:
    rows = sorted(rows, key=lambda r: sort_key(r[0]))
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "id": oid, "properties": {**props, **common},
                          "geometry": mapping(round_geom(g))} for oid, g, props in rows]}


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if path.suffix == ".geojson"
                   else json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def counts(fc, key="kind", within=None) -> dict:
    out: dict[str, int] = {}
    for f in fc["features"]:
        if within is not None and not within.intersects(shape(f["geometry"])):
            continue
        k = f["properties"].get(key)
        if k is not None:
            out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def build(pbf: Path, county_g, out: Path, demo_bbox=DEMO_BBOX, log=print) -> dict:
    """Build all outputs into ``out`` (created). Returns meta. Used by main() and by tests on a tiny fixture."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    ts = extract_timestamp(pbf)
    ring = to_wgs84(to_measure(county_g).buffer(FACILITY_RING_M))
    line_area = expanded_bbox(demo_bbox, LINE_BUFFER_M)
    raw = scan(pbf, line_area, log)
    fac, bridges, water, skipped = assemble(raw, county_g, ring, line_area)
    del raw
    common = {"source": SOURCE, "osm_timestamp": ts}
    fac_fc, br_fc, wa_fc = (feature_collection(r, common) for r in (fac, bridges, water))
    for name, fc in (("facilities.geojson", fac_fc), ("bridges.geojson", br_fc), ("waterways.geojson", wa_fc)):
        write_json(out / name, fc)
    demo = box(*demo_bbox)
    in_county = {"features": [f for f in fac_fc["features"] if f["properties"]["in_county"]]}
    meta = {
        "source": {"name": "OpenStreetMap, Geofabrik north-carolina extract", "file": f"data/osm/{pbf.name}",
                   "url": "https://download.geofabrik.de/north-america/us/north-carolina.html",
                   "osm_timestamp": ts, "licence": LICENCE, "attribution": ATTRIBUTION,
                   "registry_source_id": "community_v1"},
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "build_seconds": round(time.time() - t0, 1),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "script": "scripts/build_community.py",
        "method": __doc__.split("Method (low memory, no full node-location index): ")[1].strip(),
        "demo_bbox": list(demo_bbox),
        "files": {
            "facilities.geojson": {
                "extent": "Mecklenburg County (data/boundaries/v1/county.geojson) + 10 km ring (in_county=false)",
                "extent_bbox": [round(v, 6) for v in ring.bounds],
                "count": len(fac_fc["features"]),
                "counts_by_kind_all": counts(fac_fc),
                "counts_by_kind_in_county": counts(in_county),
                "counts_by_kind_in_demo_bbox": counts(fac_fc, within=demo)},
            "bridges.geojson": {
                "extent": "ways intersecting the demo bbox expanded by 2 km (not clipped)",
                "extent_bbox": [round(v, 6) for v in line_area.bounds],
                "count": len(br_fc["features"]),
                "counts_by_highway": counts(br_fc, "highway"), "counts_by_railway": counts(br_fc, "railway"),
                "count_in_demo_bbox": sum(counts(br_fc, within=demo).values())},
            "waterways.geojson": {
                "extent": "features intersecting the demo bbox expanded by 2 km (not clipped)",
                "extent_bbox": [round(v, 6) for v in line_area.bounds],
                "count": len(wa_fc["features"]),
                "counts_by_kind": counts(wa_fc), "counts_by_kind_in_demo_bbox": counts(wa_fc, within=demo)},
        },
        "skipped": skipped,
        "caveats": [
            "OpenStreetMap is volunteer-mapped: presence of a feature is evidence, absence is not proof of absence.",
            "A facility mapped both as a building polygon and as a node can appear twice (not de-duplicated).",
            "Facility relations that are not multipolygons (e.g. type=site) are not included (see skipped).",
            "Schools: location and name only (no ratings or demographics). No people-related tags are carried.",
            "Critical utilities (substations, water treatment) are intentionally excluded.",
        ],
    }
    write_json(out / "meta.json", meta)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default="v1")
    ap.add_argument("--pbf", type=Path, default=NC_PBF)
    ap.add_argument("--force", action="store_true", help="overwrite an existing version folder")
    a = ap.parse_args()
    out = DATA / "community" / a.version
    if out.exists() and any(out.glob("*.geojson")) and not a.force:
        sys.exit(f"{out} exists; pass --force or a new --version")
    county = shapely.union_all([shape(f["geometry"]) for f in json.loads(COUNTY.read_text())["features"]])
    meta = build(a.pbf, county, out)
    print(json.dumps({"peak_rss_mb": meta["peak_rss_mb"], "build_seconds": meta["build_seconds"],
                      **{k: v.get("counts_by_kind_in_county") or v.get("counts_by_kind") or v["count"]
                         for k, v in meta["files"].items()}}, indent=1))


if __name__ == "__main__":
    main()
