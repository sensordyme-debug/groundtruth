"""Step 3: county reference layers → data/layers/ (+ data/layers/_sources.json for envelopes).

  python scripts/fetch_layers.py [--only osm,fema,overture]

- OSM: from a Geofabrik North Carolina extract (copied from the ORYN cache if present, else downloaded),
  clipped to Mecklenburg with pyosmium: roads (I-485 / I-77 / I-85 flagged), LYNX light-rail stations,
  water (polygons + waterways), UNC Charlotte campus outline.
- FEMA NFHL flood hazard zones (MapServer layer 28) clipped to the county.
- Overture places + buildings (release pinned below) via DuckDB over the public S3 bucket, clipped to the county.
No people data: only names/categories of places; no phone, email, website or address fields are kept.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import httpx
from shapely import wkb
from shapely.geometry import box, mapping, shape
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402

DATA = config.DATA
LAYERS = DATA / "layers"
TODAY = date.today().isoformat()
GEOFABRIK = "https://download.geofabrik.de/north-america/us/north-carolina-latest.osm.pbf"
ORYN_PBF = Path("/home/orynt/oryn-tools/valhalla/osm/north-carolina-261106.osm.pbf")
NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
OVERTURE_RELEASE = "2026-10-21.0"

county = shape(json.loads((DATA / "boundary" / "mecklenburg.geojson").read_text())["geometry"])
county_p = prep(county)
W, S, E, N = county.bounds


def write_fc(name: str, feats: list[dict]) -> None:
    LAYERS.mkdir(parents=True, exist_ok=True)
    p = LAYERS / f"{name}.geojson"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    os.replace(tmp, p)
    print(f"  {name}: {len(feats)} features → {p} ({p.stat().st_size/1e6:.1f} MB)")


def record_sources(new: list[dict]) -> None:
    p = LAYERS / "_sources.json"
    cur = json.loads(p.read_text()) if p.exists() else []
    names = {s["name"] for s in new}
    cur = [s for s in cur if s["name"] not in names] + new
    p.write_text(json.dumps(cur, indent=1))


def clip(geom):
    if not county_p.intersects(geom):
        return None
    return geom if county_p.contains(geom) else geom.intersection(county)


# ---------------- OSM ----------------
def osm() -> None:
    import osmium

    pbf_dir = DATA / "osm"
    pbf_dir.mkdir(parents=True, exist_ok=True)
    pbf = pbf_dir / "north-carolina.osm.pbf"
    origin = GEOFABRIK
    if not pbf.exists():
        if ORYN_PBF.exists():
            print(f"copying OSM extract from ORYN cache {ORYN_PBF}")
            shutil.copyfile(ORYN_PBF, pbf.with_suffix(".part"))
            os.replace(pbf.with_suffix(".part"), pbf)
        else:
            print("downloading", GEOFABRIK)
            with httpx.stream("GET", GEOFABRIK, timeout=600, follow_redirects=True) as r, \
                    open(pbf.with_suffix(".part"), "wb") as f:
                for b in r.iter_bytes(1 << 20):
                    f.write(b)
            os.replace(pbf.with_suffix(".part"), pbf)
    snapshot = "2026-11-06 (Geofabrik north-carolina, via ORYN cache)" if ORYN_PBF.exists() else TODAY

    # Pass 1 (low memory): tag-filter the NC extract to only what we need, plus referenced nodes/ways
    # (pyosmium BackReferenceWriter = `osmium tags-filter`). Geometry is built from the small file afterwards.
    small = pbf_dir / "mecklenburg_filtered.osm.pbf"
    if not small.exists():
        t0 = time.time()
        keep = osmium.filter.TagFilter(
            ("highway", "motorway"), ("highway", "motorway_link"),
            ("railway", "light_rail"), ("railway", "station"), ("railway", "halt"), ("railway", "stop"),
            ("natural", "water"), ("landuse", "reservoir"),
            ("waterway", "river"), ("waterway", "stream"), ("waterway", "canal"), ("waterway", "riverbank"),
            ("amenity", "university"))
        with osmium.BackReferenceWriter(str(small.with_suffix(".part.pbf")), ref_src=str(pbf), overwrite=True) as w:
            for obj in osmium.FileProcessor(str(pbf)).with_filter(keep):
                w.add(obj)
        os.replace(small.with_suffix(".part.pbf"), small)
        print(f"  tag-filtered NC extract → {small.name} ({small.stat().st_size/1e6:.0f} MB, {time.time()-t0:.0f}s)")
    pbf = small

    roads, stations, water, campus, rail = [], [], [], [], []
    road_classes = {"motorway", "motorway_link"}
    fab = osmium.geom.WKBFactory()
    bbox = box(W - 0.01, S - 0.01, E + 0.01, N + 0.01)

    def interstates(ref: str) -> dict:
        refs = {r.strip().upper().replace(" ", "-") for r in ref.split(";")}
        return {"is_i485": "I-485" in refs, "is_i77": "I-77" in refs, "is_i85": "I-85" in refs}

    class H(osmium.SimpleHandler):
        def node(self, n):
            t = n.tags
            if t.get("railway") in ("station", "halt", "stop") and (
                    t.get("station") == "light_rail" or t.get("light_rail") == "yes"):
                g = shape({"type": "Point", "coordinates": [n.location.lon, n.location.lat]})
                if county_p.contains(g):
                    stations.append({"type": "Feature", "geometry": mapping(g), "properties": {
                        "osm_id": f"node/{n.id}", "name": t.get("name", ""), "network": t.get("network", "")}})

        def way(self, w):
            t = w.tags
            hw, ww = t.get("highway"), t.get("waterway")
            if t.get("railway") == "light_rail":
                try:
                    g = clip(wkb.loads(fab.create_linestring(w), hex=True))
                except Exception:
                    return
                if g is not None and not g.is_empty:
                    rail.append({"type": "Feature", "geometry": mapping(g), "properties": {
                        "osm_id": f"way/{w.id}", "name": t.get("name", ""), "service": t.get("service", "")}})
                return
            if hw not in road_classes and ww not in ("river", "stream", "canal"):
                return
            try:
                g = wkb.loads(fab.create_linestring(w), hex=True)
            except Exception:
                return
            if not bbox.intersects(g):
                return
            g = clip(g)
            if g is None or g.is_empty:
                return
            if hw in road_classes:
                ref = t.get("ref", "")
                roads.append({"type": "Feature", "geometry": mapping(g), "properties": {
                    "osm_id": f"way/{w.id}", "highway": hw, "name": t.get("name", ""), "ref": ref,
                    **interstates(ref)}})
            else:
                water.append({"type": "Feature", "geometry": mapping(g), "properties": {
                    "osm_id": f"way/{w.id}", "kind": ww, "name": t.get("name", "")}})

        def area(self, a):
            t = a.tags
            is_water = t.get("natural") == "water" or t.get("landuse") == "reservoir" or t.get("water")
            is_campus = t.get("amenity") == "university" and "charlotte" in t.get("name", "").lower() and (
                "north carolina" in t.get("name", "").lower() or "unc" in t.get("name", "").lower())
            if not (is_water or is_campus):
                return
            try:
                g = wkb.loads(fab.create_multipolygon(a), hex=True)
            except Exception:
                return
            if not bbox.intersects(g):
                return
            g = clip(g.buffer(0))
            if g is None or g.is_empty:
                return
            oid = f"{'way' if a.from_way() else 'relation'}/{a.orig_id()}"
            if is_campus:
                campus.append({"type": "Feature", "geometry": mapping(g), "properties": {
                    "osm_id": oid, "name": t.get("name", "")}})
            else:
                water.append({"type": "Feature", "geometry": mapping(g), "properties": {
                    "osm_id": oid, "kind": t.get("water") or t.get("natural") or "water", "name": t.get("name", "")}})

    t0 = time.time()
    print("scanning OSM extract (one pass)…")
    H().apply_file(str(pbf), locations=True, idx="flex_mem")
    print(f"  done in {time.time()-t0:.0f}s")
    write_fc("osm_motorways", roads)
    write_fc("osm_i485", [f for f in roads if f["properties"]["is_i485"]])
    write_fc("osm_light_rail", rail)
    write_fc("osm_light_rail_stations", stations)
    write_fc("osm_water", water)
    write_fc("osm_unc_charlotte_campus", campus)
    record_sources([{"name": f"OpenStreetMap {k}", "url": "https://www.openstreetmap.org/copyright",
                     "date": snapshot, "licence": "ODbL 1.0 — © OpenStreetMap contributors"}
                    for k in ("motorways", "light rail", "light rail stations", "water", "UNC Charlotte campus")])


# ---------------- FEMA NFHL ----------------
def fema() -> None:
    PAGE = 200
    feats, offset = [], 0
    fields = "DFIRM_ID,FLD_AR_ID,FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,SOURCE_CIT"
    with httpx.Client(timeout=180, headers={"User-Agent": "Groundtruth/0.1 (CrownHacks)"}) as c:
        while True:
            # County-wide envelope queries return HTTP 500; the Mecklenburg DFIRM (37119C) pages reliably.
            params = {"where": "DFIRM_ID='37119C'", "outFields": fields, "outSR": 4326, "f": "geojson",
                      "resultOffset": offset, "resultRecordCount": PAGE, "orderByFields": "OBJECTID"}
            for attempt in range(4):
                try:
                    r = c.get(NFHL, params=params)
                    r.raise_for_status()
                    page = r.json()
                    break
                except Exception as e:
                    print("  NFHL retry", attempt + 1, e)
                    time.sleep(3 * (attempt + 1))
            else:
                raise RuntimeError("NFHL fetch failed")
            fs = page.get("features", [])
            for f in fs:
                if not f.get("geometry"):
                    continue
                g = clip(shape(f["geometry"]).buffer(0))
                if g is None or g.is_empty:
                    continue
                p = f["properties"]
                feats.append({"type": "Feature", "geometry": mapping(g), "properties": {
                    "fld_ar_id": p.get("FLD_AR_ID"), "fld_zone": p.get("FLD_ZONE"), "zone_subty": p.get("ZONE_SUBTY"),
                    "sfha": p.get("SFHA_TF") == "T", "dfirm_id": p.get("DFIRM_ID"), "static_bfe": p.get("STATIC_BFE")}})
            if offset % 5000 < PAGE:
                print(f"  NFHL offset {offset}: kept {len(feats)}")
            if not fs:
                break
            offset += len(fs)
    write_fc("fema_flood_zones", feats)
    record_sources([{"name": "FEMA NFHL flood hazard zones (layer 28)",
                     "url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28",
                     "date": TODAY, "licence": "Public domain (US Government work, FEMA)"}])


# ---------------- Overture ----------------
def overture() -> None:
    import duckdb

    con = duckdb.connect()
    for s in ("INSTALL spatial", "LOAD spatial", "INSTALL httpfs", "LOAD httpfs", "SET s3_region='us-west-2'"):
        con.execute(s)
    base = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}"
    bb = f"bbox.xmin < {E} AND bbox.xmax > {W} AND bbox.ymin < {N} AND bbox.ymax > {S}"
    cwkt = county.wkt
    out = DATA / "layers"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    con.execute(f"""
        COPY (SELECT id, names.primary AS name, basic_category AS category, taxonomy.primary AS taxonomy_primary,
                     confidence, operating_status, sources[1].dataset AS source_dataset,
                     sources[1].license AS source_licence, ST_AsWKB(geometry::GEOMETRY) AS geometry_wkb
              FROM read_parquet('{base}/theme=places/type=place/*', hive_partitioning=1)
              WHERE {bb} AND ST_Intersects(geometry::GEOMETRY, ST_GeomFromText('{cwkt}')))
        TO '{out}/overture_places.parquet' (FORMAT parquet)""")
    n = con.execute(f"SELECT count(*) FROM '{out}/overture_places.parquet'").fetchone()[0]
    print(f"  overture_places: {n} rows ({time.time()-t0:.0f}s)")
    for row in con.execute(f"SELECT source_dataset, source_licence, count(*) FROM '{out}/overture_places.parquet' "
                           "GROUP BY ALL ORDER BY 3 DESC").fetchall():
        print("    places source:", row)
    t0 = time.time()
    con.execute(f"""
        COPY (SELECT id, height, num_floors, class, subtype, roof_shape, sources[1].dataset AS source_dataset,
                     ST_AsWKB(geometry::GEOMETRY) AS geometry_wkb
              FROM read_parquet('{base}/theme=buildings/type=building/*', hive_partitioning=1)
              WHERE {bb} AND ST_Intersects(geometry::GEOMETRY, ST_GeomFromText('{cwkt}')))
        TO '{out}/overture_buildings.parquet' (FORMAT parquet)""")
    n = con.execute(f"SELECT count(*) FROM '{out}/overture_buildings.parquet'").fetchone()[0]
    print(f"  overture_buildings: {n} rows ({time.time()-t0:.0f}s)")
    for row in con.execute(f"SELECT source_dataset, count(*) FROM '{out}/overture_buildings.parquet' "
                           "GROUP BY ALL ORDER BY 2 DESC").fetchall():
        print("    buildings source:", row)
    url = "https://docs.overturemaps.org/attribution/"
    record_sources([
        {"name": f"Overture Maps places ({OVERTURE_RELEASE})", "url": url, "date": OVERTURE_RELEASE,
         "licence": "CDLA Permissive 2.0 (Meta, Microsoft, others); AllThePlaces CC0 1.0"},
        {"name": f"Overture Maps buildings ({OVERTURE_RELEASE})", "url": url, "date": OVERTURE_RELEASE,
         "licence": "ODbL 1.0 (includes OpenStreetMap; © OpenStreetMap contributors, Overture Maps Foundation)"},
    ])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="osm,fema,overture")
    a = ap.parse_args()
    for step in a.only.split(","):
        print(f"== {step}")
        {"osm": osm, "fema": fema, "overture": overture}[step]()
