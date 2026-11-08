"""End-to-end smoke of the v0.3 tools through HTTP (TestClient): find_all → refine ×3 → filter → measure → export."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine")); sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

AOI = sys.argv[1] if len(sys.argv) > 1 else "south_charlotte"
c = TestClient(app)


def call(tool, body):
    t0 = time.perf_counter()
    r = c.post(f"/api/v1/tools/{tool}", json={"aoi": AOI, **body})
    ms = (time.perf_counter() - t0) * 1000
    env = r.json()
    print(f"{tool:16s} {r.status_code} {env.get('status'):8s} {ms:7.0f} ms  "
          f"{json.dumps(env['result']['metrics'])[:220] if env.get('status') != 'declined' else env.get('declined_reason')}")
    return env


pc = call("pick_candidates", {"query": "swimming pool"})
ex = next(f["properties"]["example"] for f in pc["result"]["geojson"]["features"] if f["properties"]["sense_id"] == "community_pool")
concept = pc["result"]["metrics"]["sense_list"]
fa = call("find_all", {"examples": [ex], "concept": concept, "k": 120, "outline_top": 40})
time.sleep(8)  # let the outline worker fill the cache
fa2 = call("find_all", {"examples": [ex], "concept": concept, "k": 120, "outline_top": 40})
hits = fa2["result"]["geojson"]["features"]
sid = None
for n in range(3):
    acc = [hits[n]["id"]]
    rej = [hits[-1 - n]["id"]]
    rf = call("refine", {"session_id": sid, "accept": ([ex] if sid is None else []) + acc, "reject": rej, "k": 100,
                         "concept": concept})
    sid = rf["result"]["metrics"]["session_id"]
print("why example:", rf["result"]["geojson"]["features"][0]["properties"].get("why"))
flt = call("filter", {"geojson": fa2["result"]["geojson"], "where": {"within_m_of": {"layer": "osm_i485", "m": 1000}}})
ms = call("measure", {"geojson": flt["result"]["geojson"], "to_layers": ["osm_i485", "fema_flood_zones"]})
call("export", {"geojson": flt["result"]["geojson"], "format": "csv"})
pdf = call("export", {"geojson": flt["result"]["geojson"], "format": "pdf", "title": "Pools within 1 km of I-485",
                      "summary": {k: ms["result"]["metrics"].get(k) for k in ("count", "area_acres_total", "median_area_m2")}})
print("pdf:", pdf["result"]["metrics"]["download"], c.get(pdf["result"]["metrics"]["download"]).status_code)
print("places:", list(c.get("/api/v1/places").json()["places"])[:8])
print("limits:", fa2["limits"][-3:])
