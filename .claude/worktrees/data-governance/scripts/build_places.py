"""Neighbourhood bboxes → engine/groundtruth/conf/places.json, plus osm_i77 / osm_i85 layers from osm_motorways.

  python scripts/build_places.py
Place names are resolved once with OSM Nominatim (ODbL; 1 req/s, generic UA) and clipped to the county.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx
from shapely.geometry import box, mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402

PLACES = {
    "Ballantyne": "Ballantyne, Charlotte, NC",
    "NoDa": "NoDa, Charlotte, NC",
    "South End": "South End, Charlotte, NC",
    "Myers Park": "Myers Park, Charlotte, NC",
    "Lake Norman": "Lake Norman",
    "University City": "University City, Charlotte, NC",
    "Matthews": "Matthews, NC",
    "Uptown": "Uptown, Charlotte, NC",
    "Cornelius": "Cornelius, NC",
    "Huntersville": "Huntersville, NC",
    "Mint Hill": "Mint Hill, NC",
    "Pineville": "Pineville, NC",
    "Davidson": "Davidson, NC",
    "Plaza Midwood": "Plaza Midwood, Charlotte, NC",
    "Dilworth": "Dilworth, Charlotte, NC",
    "SouthPark": "SouthPark, Charlotte, NC",
    "Steele Creek": "Steele Creek, Charlotte, NC",
    "UNC Charlotte": "University of North Carolina at Charlotte",
}
UA = "Groundtruth/0.1 (CrownHacks hackathon)"


def main() -> None:
    county = shape(json.loads((config.DATA / "boundary" / "mecklenburg.geojson").read_text())["geometry"])
    w, s, e, n = county.bounds
    out = {}
    with httpx.Client(timeout=30, headers={"User-Agent": UA}) as c:
        for name, q in PLACES.items():
            r = c.get("https://nominatim.openstreetmap.org/search", params={
                "q": q, "format": "jsonv2", "limit": 5, "viewbox": f"{w},{n},{e},{s}", "countrycodes": "us"})
            time.sleep(1.1)
            best = None
            for it in r.json():
                bb = it["boundingbox"]  # s, n, w, e
                rect = box(float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1]))
                inter = rect.intersection(county)
                if inter.is_empty:
                    continue
                if best is None or float(it.get("importance") or 0) > best[0]:
                    best = (float(it.get("importance") or 0), inter, it)
            if best is None:
                print("not found:", name); continue
            _, g, it = best
            if g.area < 1e-6:  # point-like result → 1.5 km box around it
                p = g.centroid
                g = box(p.x - 0.008, p.y - 0.007, p.x + 0.008, p.y + 0.007).intersection(county)
            out[name] = {"bbox": [round(v, 6) for v in g.bounds], "osm": f"{it['osm_type']}/{it['osm_id']}",
                         "display_name": it["display_name"], "type": it.get("type")}
            print(f"{name:16s} {out[name]['bbox']}  {it['type']}")
    campus = config.DATA / "layers" / "osm_unc_charlotte_campus.geojson"
    if "UNC Charlotte" not in out and campus.exists():
        fs = json.loads(campus.read_text())["features"]
        if fs:
            g = shape(fs[0]["geometry"])
            out["UNC Charlotte"] = {"bbox": [round(v, 6) for v in g.bounds], "osm": fs[0]["properties"]["osm_id"],
                                    "display_name": fs[0]["properties"]["name"], "type": "university"}
            print("UNC Charlotte from OSM campus outline", out["UNC Charlotte"]["bbox"])
    conf = config.ROOT / "engine" / "groundtruth" / "conf" / "places.json"
    conf.write_text(json.dumps({"_source": f"OpenStreetMap Nominatim (ODbL, © OpenStreetMap contributors), {date.today()}",
                                "places": out}, indent=1))
    print("wrote", conf)
    mot = json.loads((config.DATA / "layers" / "osm_motorways.geojson").read_text())
    for key in ("i77", "i85"):
        feats = [f for f in mot["features"] if f["properties"].get(f"is_{key}")]
        (config.DATA / "layers" / f"osm_{key}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        print(f"osm_{key}: {len(feats)}")


if __name__ == "__main__":
    main()
