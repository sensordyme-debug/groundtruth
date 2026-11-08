"""Boundary layers for the geography resolver → data/boundaries/v1/ (new, versioned; never overwrites).

  GROUNDTRUTH_DATA=... python scripts/build_boundaries.py [--force]

Layers (EPSG:4326 GeoJSON; every feature has name, kind, source, source_date):
  county.geojson              Mecklenburg County (existing data/boundary/mecklenburg.geojson, US Census TIGERweb)
  municipalities.geojson      the 7 municipalities: OSM boundary=administrative admin_level=8 relations from the local
                              NC .osm.pbf (ODbL), cross-checked against US Census TIGERweb Incorporated Places
                              (public domain); TIGERweb is used for a town only if OSM is missing / disagrees (IoU<0.9)
  neighbourhoods_bbox.geojson conf/places.json Nominatim bboxes, kind neighbourhood_bbox, approximate=true
  neighbourhoods_osm.geojson  OSM place=neighbourhood|suburb|quarter *areas* inside the county (if any)
  landmarks.geojson           UNC Charlotte campus outline (data/layers/osm_unc_charlotte_campus.geojson)
  zcta.geojson                US Census 2020 ZCTAs (TIGERweb, public domain) clipped to the county
  water_bodies.geojson        Lake Norman / Lake Wylie / Mountain Island Lake: full OSM water polygons (not clipped to
                              the county, so distances are measured from the real shore); falls back to
                              data/risk/v1/shore/water_bodies.geojson (clipped) when the full polygon cannot be built
  outside_municipalities.json names of OSM admin_level=8 places that intersect the county envelope but are not in it
                              (so the resolver can say "outside coverage" instead of "unknown")
  meta.json                   sources, dates, licences, counts, sha256, cross-check numbers

Low memory: tag-filter the NC pbf once to a small pbf (kept in v1/work/), then assemble areas with pyosmium.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx
import osmium
import shapely
from shapely import wkb
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402

DATA = config.DATA
OUT = DATA / "boundaries" / "v1"
WORK = OUT / "work"
NC_PBF = DATA / "osm" / "north-carolina.osm.pbf"
OSM_DATE = "2026-11-06"  # Geofabrik NC extract date (see docs/SOURCES.md)
OSM_SRC = "OpenStreetMap (Geofabrik north-carolina extract)"
OSM_LIC = "ODbL 1.0 (© OpenStreetMap contributors)"
CENSUS_LIC = "Public domain (US Government work, US Census Bureau)"
TIGERWEB = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
TW_PLACES = f"{TIGERWEB}/Places_CouSub_ConCity_SubMCD/MapServer/4/query"  # Incorporated Places (current vintage)
TW_ZCTA = f"{TIGERWEB}/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/11/query"  # 2020 Census ZCTAs
UA = {"User-Agent": "Groundtruth-hackathon/0.1"}
MUNICIPALITIES = ["Charlotte", "Cornelius", "Davidson", "Huntersville", "Matthews", "Mint Hill", "Pineville"]
WATER = ["Lake Norman", "Lake Wylie", "Mountain Island Lake"]
TODAY = time.strftime("%Y-%m-%d")


def rnd(g, nd: int = 7):
    """Round coordinates to 1e-7 deg (~1 cm) to keep the files small; repair if rounding broke validity."""
    g2 = shapely.set_precision(g, 10 ** -nd)
    return g2 if g2.is_valid and not g2.is_empty else g


def feat(g, **props) -> dict:
    return {"type": "Feature", "properties": props, "geometry": mapping(rnd(g))}


def write_fc(name: str, feats: list[dict]) -> Path:
    p = OUT / name
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return p


def iou(a, b) -> float:
    am, bm = to_measure(a), to_measure(b)
    u = am.union(bm).area
    return float(am.intersection(bm).area / u) if u else 0.0


def km2(g) -> float:
    return round(to_measure(g).area / 1e6, 3)


# ---------------------------------------------------------------- OSM
def osm_filtered() -> Path:
    small = WORK / "boundaries_filtered.osm.pbf"
    if small.exists():
        return small
    WORK.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    keep = osmium.filter.TagFilter(
        ("boundary", "administrative"), ("place", "neighbourhood"), ("place", "suburb"), ("place", "quarter"),
        *[("name", n) for n in WATER])
    part = small.with_suffix(".part.pbf")
    with osmium.BackReferenceWriter(str(part), ref_src=str(NC_PBF), overwrite=True) as w:
        for obj in osmium.FileProcessor(str(NC_PBF)).with_filter(keep):
            w.add(obj)
    os.replace(part, small)
    print(f"tag-filtered NC pbf → {small.name} ({small.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s)")
    return small


def osm_areas(pbf: Path, county) -> dict:
    env = box(*county.bounds).buffer(0.25)  # ~25 km around the county envelope
    fab = osmium.geom.WKBFactory()
    out = {"admin8": [], "admin6": [], "places": [], "water": [], "place_nodes": [], "broken": []}

    class H(osmium.SimpleHandler):
        def node(self, n):
            if n.tags.get("place") in ("neighbourhood", "suburb", "quarter") and n.location.valid():
                out["place_nodes"].append((n.id, n.tags.get("name"), n.location.lon, n.location.lat))

        def area(self, a):
            t = a.tags
            name = t.get("name")
            is_admin = t.get("boundary") == "administrative"
            lvl = t.get("admin_level")
            is_place = t.get("place") in ("neighbourhood", "suburb", "quarter")
            is_water = name in WATER and (t.get("natural") == "water" or "water" in t or t.get("landuse") == "reservoir")
            if not (is_admin and lvl in ("6", "8") or is_place or is_water):
                return
            oid = f"{'way' if a.from_way() else 'relation'}/{a.orig_id()}"
            try:
                g = wkb.loads(fab.create_multipolygon(a), hex=True)
                if not g.is_valid:
                    g = shapely.make_valid(g)
            except Exception as e:  # noqa: BLE001 - broken multipolygons are recorded, not fatal
                out["broken"].append({"osm": oid, "name": name, "error": str(e)[:120]})
                return
            if g.is_empty or not env.intersects(g):
                return
            row = {"osm": oid, "name": name, "tags": {k: t.get(k) for k in ("admin_level", "place", "natural", "water",
                                                                            "border_type", "wikidata") if t.get(k)},
                   "geom": g}
            if is_water:
                out["water"].append(row)
            elif is_place:
                out["places"].append(row)
            elif lvl == "8":
                out["admin8"].append(row)
            elif lvl == "6":
                out["admin6"].append(row)

    t0 = time.time()
    H().apply_file(str(pbf), locations=True, idx="flex_mem")
    print(f"assembled OSM areas in {time.time() - t0:.0f}s: "
          + ", ".join(f"{k}={len(v)}" for k, v in out.items()))
    return out


# ---------------------------------------------------------------- TIGERweb
def tigerweb(url: str, county, where: str, fields: str) -> list[dict]:
    w, s, e, n = county.bounds
    params = {"where": where, "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "returnGeometry": "true", "outSR": 4326,
              "f": "geojson"}
    r = httpx.get(url, params=params, headers=UA, timeout=120)
    r.raise_for_status()
    fc = r.json()
    if "features" not in fc:
        raise RuntimeError(f"TIGERweb returned no features: {str(fc)[:200]}")
    return fc["features"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild layers in an existing v1 (never touches other dirs)")
    args = ap.parse_args()
    if (OUT / "meta.json").exists() and not args.force:
        sys.exit(f"{OUT} already built (meta.json exists); pass --force to rebuild, or build a v2")
    OUT.mkdir(parents=True, exist_ok=True)
    meta: dict = {"version": "v1", "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "crs": "EPSG:4326",
                  "layers": {}, "sources": [], "notes": []}

    # ---- county
    cf = json.loads((DATA / "boundary" / "mecklenburg.geojson").read_text())
    county = shape(cf["geometry"])
    cprops = cf["properties"]
    county_src = cprops.get("source", "US Census Bureau TIGERweb State_County/MapServer/1")
    county_date = cprops.get("retrieved", "2026-11-07")
    p = write_fc("county.geojson", [feat(county, name="Mecklenburg County", kind="county", source=county_src,
                                         source_date=county_date, geoid=cprops.get("geoid", "37119"),
                                         licence=CENSUS_LIC, approximate=False, area_km2=km2(county))])
    meta["layers"]["county"] = {"file": p.name, "count": 1, "source": county_src, "source_date": county_date,
                                "licence": CENSUS_LIC, "from": "data/boundary/mecklenburg.geojson"}
    meta["sources"].append({"name": "US Census Bureau TIGERweb (State_County layer 1, county 37119)",
                            "url": "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1",
                            "date": county_date, "licence": CENSUS_LIC})

    # ---- OSM areas
    osm = osm_areas(osm_filtered(), county)
    meta["sources"].append({"name": OSM_SRC, "url": "https://download.geofabrik.de/north-america/us/north-carolina.html",
                            "date": OSM_DATE, "licence": OSM_LIC,
                            "local": "data/osm/north-carolina.osm.pbf (tag-filtered to data/boundaries/v1/work/)"})
    if osm["broken"]:
        meta["notes"].append({"osm_broken_multipolygons": osm["broken"][:50]})
    cm = to_measure(county)
    osm_county = [r for r in osm["admin6"] if (r["name"] or "").startswith("Mecklenburg")]
    if osm_county:
        meta["layers"]["county"]["osm_crosscheck_iou"] = round(iou(county, osm_county[0]["geom"]), 4)

    # ---- municipalities: OSM, cross-checked against TIGERweb
    tiger: dict[str, dict] = {}
    try:
        for f in tigerweb(TW_PLACES, county, "STATE='37'", "NAME,BASENAME,GEOID,LSADC,FUNCSTAT"):
            tiger[f["properties"]["BASENAME"]] = f
        meta["sources"].append({"name": "US Census Bureau TIGERweb Incorporated Places (current vintage)",
                                "url": TW_PLACES.removesuffix("/query"), "date": TODAY, "licence": CENSUS_LIC})
    except Exception as e:  # noqa: BLE001
        meta["notes"].append(f"TIGERweb places unavailable: {e}")
    mun_feats, crosscheck, outside = [], {}, set()
    for r in osm["admin8"]:
        if r["name"] and r["name"] not in MUNICIPALITIES:
            g = r["geom"]
            if to_measure(g).intersection(cm).area / max(to_measure(g).area, 1e-9) < 0.5:
                outside.add(r["name"])
    by_name: dict[str, dict] = {}
    for r in osm["admin8"]:
        if r["name"] in MUNICIPALITIES:
            g = r["geom"]
            frac = to_measure(g).intersection(cm).area / max(to_measure(g).area, 1e-9)
            if frac >= 0.5 and (r["name"] not in by_name or to_measure(g).area > to_measure(by_name[r["name"]]["geom"]).area):
                by_name[r["name"]] = r | {"frac": frac}
    for name in MUNICIPALITIES:
        r, tf = by_name.get(name), tiger.get(name)
        tg = shape(tf["geometry"]) if tf else None
        cc = {"osm": r["osm"] if r else None, "tiger_geoid": tf["properties"]["GEOID"] if tf else None}
        if r and tg is not None:
            cc["iou_osm_vs_tiger"] = round(iou(r["geom"], tg), 4)
        use_tiger = r is None or (tg is not None and cc.get("iou_osm_vs_tiger", 1.0) < 0.9)
        if use_tiger and tg is None:
            meta["notes"].append(f"municipality {name}: no OSM and no TIGER geometry — NOT BUILT")
            continue
        g = tg if use_tiger else r["geom"]
        lsad = (tf["properties"]["NAME"].rsplit(" ", 1)[-1] if tf else ("city" if name == "Charlotte" else "town"))
        props = dict(name=name, kind="city" if name == "Charlotte" else "municipality", municipal_type=lsad,
                     source=("US Census TIGERweb Incorporated Places" if use_tiger else OSM_SRC),
                     source_date=(TODAY if use_tiger else OSM_DATE), licence=(CENSUS_LIC if use_tiger else OSM_LIC),
                     source_id=(f"census:{tf['properties']['GEOID']}" if use_tiger else f"osm:{r['osm']}"),
                     # every id this town is known by (OSM relation + Census GEOID), so the resolver can tell that a
                     # Nominatim bbox of the same OSM relation is the same entity, whichever geometry was used
                     same_as=sorted(x for x in ([f"osm:{r['osm']}"] if r else []) +
                                    ([f"census:{tf['properties']['GEOID']}"] if tf else [])),
                     approximate=False, area_km2=km2(g),
                     fraction_in_county=round(to_measure(g).intersection(cm).area / to_measure(g).area, 4))
        cc["used"] = "tiger" if use_tiger else "osm"
        crosscheck[name] = cc
        mun_feats.append(feat(g, **props))
    p = write_fc("municipalities.geojson", mun_feats)
    meta["layers"]["municipalities"] = {"file": p.name, "count": len(mun_feats), "crosscheck": crosscheck,
                                        "rule": "OSM admin_level=8 with >=50% of its area in the county; TIGERweb "
                                                "used instead if OSM is missing or IoU(OSM,TIGER) < 0.9"}
    (OUT / "outside_municipalities.json").write_text(json.dumps(sorted(outside), indent=1))
    meta["layers"]["outside_municipalities"] = {"file": "outside_municipalities.json", "count": len(outside),
                                                "source": OSM_SRC, "source_date": OSM_DATE, "licence": OSM_LIC}

    # ---- neighbourhood bboxes (places.json) and OSM neighbourhood polygons
    pj = json.loads((config.ROOT / "engine" / "groundtruth" / "conf" / "places.json").read_text())
    nb = []
    for name in sorted(pj["places"]):
        e = pj["places"][name]
        nb.append(feat(box(*e["bbox"]), name=name, kind="neighbourhood_bbox", approximate=True,
                       source="OpenStreetMap Nominatim bbox (engine/groundtruth/conf/places.json)",
                       source_date="2026-11-07", licence=OSM_LIC, source_id=f"osm:{e['osm']}",
                       osm_type=e.get("type"), display_name=e.get("display_name")))
    p = write_fc("neighbourhoods_bbox.geojson", nb)
    meta["layers"]["neighbourhoods_bbox"] = {"file": p.name, "count": len(nb), "approximate": True,
                                             "source": "engine/groundtruth/conf/places.json (Nominatim, 2026-11-07)",
                                             "licence": OSM_LIC}
    np_feats = []
    for r in sorted(osm["places"], key=lambda r: (r["name"] or "", r["osm"])):
        g = r["geom"]
        if not r["name"] or not county.intersects(g.representative_point()):
            continue
        np_feats.append(feat(g, name=r["name"], kind="neighbourhood", approximate=False, source=OSM_SRC,
                             source_date=OSM_DATE, licence=OSM_LIC, source_id=f"osm:{r['osm']}",
                             place=r["tags"].get("place"), area_km2=km2(g)))
    p = write_fc("neighbourhoods_osm.geojson", np_feats)
    nodes_in = [n for n in osm["place_nodes"] if county.contains(shapely.Point(n[2], n[3]))]
    meta["layers"]["neighbourhoods_osm"] = {"file": p.name, "count": len(np_feats), "source": OSM_SRC,
                                            "source_date": OSM_DATE, "licence": OSM_LIC,
                                            "place_nodes_in_county_without_polygon": len(nodes_in)}

    # ---- landmarks (UNC Charlotte campus)
    lm = []
    campus = DATA / "layers" / "osm_unc_charlotte_campus.geojson"
    if campus.exists():
        for f in json.loads(campus.read_text())["features"][:1]:
            lm.append(feat(shape(f["geometry"]), name="UNC Charlotte", kind="landmark", approximate=False,
                           source=OSM_SRC + " (data/layers/osm_unc_charlotte_campus.geojson)", source_date=OSM_DATE,
                           licence=OSM_LIC, source_id=f"osm:{f['properties'].get('osm_id')}", place_type="university"))
    p = write_fc("landmarks.geojson", lm)
    meta["layers"]["landmarks"] = {"file": p.name, "count": len(lm), "licence": OSM_LIC}

    # ---- ZCTA 2020 (TIGERweb query bounded by the county envelope: ~60 features, a few MB)
    try:
        zf = tigerweb(TW_ZCTA, county, "1=1", "ZCTA5,GEOID")
        zfeats, dropped = [], []
        for f in sorted(zf, key=lambda f: f["properties"]["ZCTA5"]):
            g = shapely.make_valid(shape(f["geometry"]))
            inter = g.intersection(county)
            frac = to_measure(inter).area / max(to_measure(g).area, 1e-9) if not inter.is_empty else 0.0
            in_km2 = km2(inter) if not inter.is_empty else 0.0
            if in_km2 < 0.25 and frac < 0.05:  # boundary slivers between Census layers
                dropped.append({"zcta": f["properties"]["ZCTA5"], "in_county_km2": in_km2, "fraction": round(frac, 4)})
                continue
            inter = shapely.make_valid(inter)
            polys = [x for x in getattr(inter, "geoms", [inter]) if x.geom_type in ("Polygon", "MultiPolygon")]
            inter = unary_union(polys)
            zfeats.append(feat(inter, name=f["properties"]["ZCTA5"], kind="zip", approximate=True,
                               source="US Census Bureau 2020 ZCTA (TIGERweb PUMA_TAD_TAZ_UGA_ZCTA/11), clipped to county",
                               source_date=TODAY, licence=CENSUS_LIC, source_id=f"census:zcta5:{f['properties']['GEOID']}",
                               clipped_to_county=frac < 0.999, fraction_in_county=round(frac, 4), area_km2=in_km2))
        p = write_fc("zcta.geojson", zfeats)
        meta["layers"]["zcta"] = {"file": p.name, "count": len(zfeats), "dropped_slivers": dropped,
                                  "source": "US Census Bureau 2020 ZCTAs via TIGERweb", "url": TW_ZCTA.removesuffix("/query"),
                                  "source_date": TODAY, "licence": CENSUS_LIC,
                                  "note": "ZCTAs approximate USPS ZIP delivery areas; PO-box-only ZIPs have no ZCTA. "
                                          "Only ZCTA5 + GEOID fields kept (no population/housing counts)."}
        meta["sources"].append({"name": "US Census Bureau 2020 ZCTAs (TIGERweb)", "url": TW_ZCTA.removesuffix("/query"),
                                "date": TODAY, "licence": CENSUS_LIC})
    except Exception as e:  # noqa: BLE001
        meta["layers"]["zcta"] = {"file": None, "count": 0, "status": f"NOT BUILT: {e}"}

    # ---- water bodies (full OSM polygons; fallback to the clipped shore layer)
    clipped = {f["properties"]["name"]: shape(f["geometry"]) for f in
               json.loads((DATA / "risk" / "v1" / "shore" / "water_bodies.geojson").read_text())["features"]}
    wfeats, wmeta = [], {}
    for name in WATER:
        rows = [r for r in osm["water"] if r["name"] == name]
        g = unary_union([r["geom"] for r in rows]) if rows else None
        if g is not None and not g.is_empty and (name not in clipped or g.intersects(clipped[name])):
            ids = sorted(r["osm"] for r in rows)
            wfeats.append(feat(g, name=name, kind="water_body", approximate=False, source=OSM_SRC, source_date=OSM_DATE,
                               licence=OSM_LIC, source_id="osm:" + ",".join(ids), clipped_to_county=False,
                               area_km2=km2(g), area_in_county_km2=km2(g.intersection(county))))
            wmeta[name] = {"used": "osm_full", "osm": ids, "area_km2": km2(g)}
        elif name in clipped:
            g = clipped[name]
            wfeats.append(feat(g, name=name, kind="water_body", approximate=True,
                               source="OpenStreetMap water clipped to the county (data/risk/v1/shore/water_bodies.geojson)",
                               source_date=OSM_DATE, licence=OSM_LIC, source_id="risk/v1/shore", clipped_to_county=True,
                               area_km2=km2(g)))
            wmeta[name] = {"used": "risk_v1_shore_clipped", "area_km2": km2(g)}
    p = write_fc("water_bodies.geojson", wfeats)
    meta["layers"]["water_bodies"] = {"file": p.name, "count": len(wfeats), "detail": wmeta, "licence": OSM_LIC}

    for k, v in meta["layers"].items():
        if v.get("file"):
            v["sha256"] = sha256(OUT / v["file"])
            v["bytes"] = (OUT / v["file"]).stat().st_size
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("sha256",)} for k, v in meta["layers"].items()},
                     indent=1)[:6000])


if __name__ == "__main__":
    main()
