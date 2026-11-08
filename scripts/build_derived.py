"""Precompute the derived concepts for a place (houses with pools → lakefront → own dock) via the running API (no second
copy of the index in RAM). → data/derived/<new version>/<place>/{houses.geojson, links.geojson, meta.json}.

  GROUNDTRUTH_DATA=… python scripts/build_derived.py "Lake Norman" [--version v1]
Pools: find_all with the demo pick (backyard sense, soft negatives, dense where built). Docks: find_anything "boat docks"
(disk-cached). Buildings: Overture (DuckDB). Water: osm_water. Roads: osm_major_roads.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from groundtruth import config  # noqa: E402
from groundtruth.derived import assign_docks, conf, houses_with_pools, lakes, mark_lakefront  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from groundtruth.vector import store  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("place")
ap.add_argument("--version", default=None)
ap.add_argument("--base", default="http://127.0.0.1:8000")
a = ap.parse_args()
t0 = time.time()
c = httpx.Client(base_url=a.base, timeout=900)
places = json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]
bb = places[a.place]["bbox"]
pc = c.post("/api/v1/tools/pick_candidates", json={"query": "swimming pool"}).json()
tiles = [f for f in pc["result"]["geojson"]["features"] if not f["properties"].get("no_clear_example")]
ch = next(f for f in tiles if f["properties"]["sense_id"] == "backyard_pool")
ex, neg = ch["properties"]["example"], [f["properties"]["example"] for f in tiles if f is not ch]
fa = c.post("/api/v1/tools/find_all", json={"examples": [ex], "soft_exclude": neg, "place": a.place,
                                            "concept": "swimming_pool", "outline_top": 500}).json()
pools = [f for f in fa["result"]["geojson"]["features"] if f.get("geometry")]
docks_env = c.post("/api/v1/tools/find_anything", json={"text": "boat docks", "place": a.place}).json()
docks = docks_env["result"]["geojson"]["features"]
print(f"{a.place}: {len(pools)} pools (dense={fa['result']['metrics'].get('dense')}), {len(docks)} docks "
      f"({time.time() - t0:.0f} s)", flush=True)
fc, _ = store().buildings(bb)
prov = PublicDataProvider(download_scenes=False)
water = prov.get_layer("osm_water", tuple(bb), detail="raw")["features"]
roads = prov.get_layer("osm_major_roads", tuple(bb), detail="raw")["features"]
lake = lakes(water)
from shapely.geometry import shape as _shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from groundtruth.derived import _is_house  # noqa: E402
from groundtruth.geo import to_measure  # noqa: E402

all_water = unary_union([to_measure(_shape(w["geometry"])).buffer(0) for w in water
                         if w.get("geometry") and _shape(w["geometry"]).geom_type in ("Polygon", "MultiPolygon")])
houses = houses_with_pools(pools, fc["features"], water=all_water)
mark_lakefront(houses, lake, roads, fc["features"])
# every lakefront home (pool or not) competes for docks, so a neighbour's dock is never "yours"
hc = conf()["house_with_pool"]
homes = []
for b in fc["features"]:
    gm = to_measure(_shape(b["geometry"]))
    if lake is not None and gm.distance(lake.boundary) <= conf()["lakefront"]["max_dist_m"] and \
            _is_house(b.get("properties") or {}, gm.area, hc):
        homes.append({"type": "Feature", "geometry": b["geometry"],
                      "properties": {"id": (b.get("properties") or {}).get("id")}})
mark_lakefront(homes, lake, roads, fc["features"])
links = assign_docks(houses, docks, lake, homes)
# docks new since 2020: footprint deck vs water in NAIP 2022 and 2020 (conf dock_new_since), one small read per dock
from shapely.geometry import box as _box  # noqa: E402

from groundtruth.derived import dock_is_new  # noqa: E402
from groundtruth.geo import to_wgs84  # noqa: E402

n_new = 0
for h in houses:
    p = h["properties"]
    p["dock_new_2020"] = []
    for dg in p.get("dock_geometries", []):
        bxw = to_wgs84(to_measure(_shape(dg)).buffer(6).envelope).bounds
        try:
            r = dock_is_new(dg, prov.get_imagery(bxw, 2022), prov.get_imagery(bxw, 2020))
        except Exception as e:  # noqa: BLE001 (no 2020 scene here: unknown, never "new")
            r = {"new": None, "why": str(e)[:80]}
        p["dock_new_2020"].append(r)
        n_new += bool(r.get("new"))
n_lf = sum(h["properties"].get("lakefront") for h in houses)
n_dock = sum(h["properties"].get("lakefront") and h["properties"].get("has_dock") for h in houses)
out = (config.DATA / "derived" / a.version if a.version else config.next_data_dir("derived")) / a.place.replace(" ", "_")
out.mkdir(parents=True, exist_ok=True)
(out / "houses.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": houses}))
(out / "links.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": links}))
meta = {"place": a.place, "bbox": bb, "pools": len(pools), "docks": len(docks), "houses_with_pools": len(houses),
        "lakefront": n_lf, "lakefront_with_dock": n_dock, "docks_new_since_2020": n_new, "lake_found": lake is not None,
        "pool_source": {"tool": "find_all", "dense": fa["result"]["metrics"].get("dense"), "mode": fa["result"]["metrics"].get("mode")},
        "dock_source": {"tool": "find_anything", "eye_precision_12": docks_env["result"]["metrics"].get("eye_precision_12")},
        "conf": conf(), "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.time() - t0)}
(out / "meta.json").write_text(json.dumps(meta, indent=1))
print(json.dumps({k: meta[k] for k in ("houses_with_pools", "lakefront", "lakefront_with_dock", "docks_new_since_2020", "lake_found")}), "→", out)
