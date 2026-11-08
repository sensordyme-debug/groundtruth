"""OSM reference set per concept → data/reference/osm_<concept>.geojson (Mecklenburg only).

  python scripts/fetch_osm_reference.py
A *weak* reference for evaluation and recall estimates: OpenStreetMap mapping of each concept. It is NOT the
human test set and is never written to data/labels/. OSM is incomplete (many backyard pools are unmapped), so treat
it as "known positives", not ground truth. Low memory: tag-filter the NC extract first, then build geometry.

Concept → OSM tags:
  swimming_pool       leisure=swimming_pool (excl. indoor=yes / location=indoor)
  parking_lot         amenity=parking with parking=surface or unset (excl. multi-storey/underground)
  warehouse           building=warehouse | building=industrial with area ≥ 2,000 m²
  solar_installation  power=plant|generator with plant:source|generator:source=solar
  construction_site   landuse=construction
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import osmium
from shapely import wkb
from shapely.geometry import mapping, shape
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402

DATA = config.DATA
OUT = DATA / "reference"
county = shape(json.loads((DATA / "boundary" / "mecklenburg.geojson").read_text())["geometry"])
cp = prep(county)


def concept_of(t) -> str | None:
    if t.get("leisure") == "swimming_pool" and t.get("indoor") != "yes" and t.get("location") != "indoor":
        return "swimming_pool"
    if t.get("amenity") == "parking" and t.get("parking", "surface") == "surface":
        return "parking_lot"
    if t.get("building") in ("warehouse", "industrial"):
        return "warehouse"
    if t.get("power") in ("plant", "generator") and "solar" in (t.get("plant:source", "") + t.get("generator:source", "")):
        return "solar_installation"
    if t.get("landuse") == "construction":
        return "construction_site"
    return None


def main() -> None:
    pbf = DATA / "osm" / "north-carolina.osm.pbf"
    small = DATA / "osm" / "reference_filtered.osm.pbf"
    if not small.exists():
        t0 = time.time()
        keep = osmium.filter.TagFilter(("leisure", "swimming_pool"), ("amenity", "parking"), ("building", "warehouse"),
                                       ("building", "industrial"), ("power", "plant"), ("power", "generator"),
                                       ("landuse", "construction"))
        with osmium.BackReferenceWriter(str(small.with_suffix(".part.pbf")), ref_src=str(pbf), overwrite=True) as w:
            for obj in osmium.FileProcessor(str(pbf)).with_filter(keep):
                w.add(obj)
        os.replace(small.with_suffix(".part.pbf"), small)
        print(f"tag-filtered → {small.name} ({small.stat().st_size/1e6:.0f} MB, {time.time()-t0:.0f}s)")
    fab = osmium.geom.WKBFactory()
    out: dict[str, list] = {}

    class H(osmium.SimpleHandler):
        def node(self, n):
            c = concept_of(n.tags)
            if c in ("swimming_pool", "solar_installation"):  # some are mapped as points
                g = shape({"type": "Point", "coordinates": [n.location.lon, n.location.lat]})
                if cp.contains(g):
                    out.setdefault(c, []).append(({"osm_id": f"node/{n.id}", "geom_type": "point"}, g))

        def area(self, a):
            c = concept_of(a.tags)
            if not c:
                return
            try:
                g = wkb.loads(fab.create_multipolygon(a), hex=True).buffer(0)
            except Exception:
                return
            if g.is_empty or not cp.intersects(g.centroid):
                return
            area_m2 = to_measure(g).area
            if c == "warehouse" and area_m2 < 2000:
                return
            oid = f"{'way' if a.from_way() else 'relation'}/{a.orig_id()}"
            out.setdefault(c, []).append(({"osm_id": oid, "geom_type": "area", "area_m2": round(area_m2, 1)}, g))

    t0 = time.time()
    H().apply_file(str(small), locations=True, idx="flex_mem")
    print(f"built geometry in {time.time()-t0:.0f}s")
    OUT.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    for c, rows in out.items():
        feats = [{"type": "Feature", "properties": {"concept": c, "source": "OpenStreetMap", **p},
                  "geometry": mapping(g)} for p, g in rows]
        (OUT / f"osm_{c}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        counts[c] = len(feats)
    meta = {"source": "OpenStreetMap (Geofabrik NC extract 2026-11-06, via ORYN cache)", "licence": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors", "built": time.strftime("%Y-%m-%d"), "counts": dict(counts),
            "note": "Weak reference (incomplete mapping). Not the human test set; never written to data/labels/."}
    (OUT / "osm_reference.meta.json").write_text(json.dumps(meta, indent=1))
    print(dict(counts))


if __name__ == "__main__":
    main()
