#!/usr/bin/env bash
# Pre-cache NAIP display tiles (z12–17, 2022 + 2020) and pool outlines for the demo neighbourhoods, via the running API.
#   bash scripts/demo_warm.sh [--tiles-only|--outlines-only]
set -uo pipefail
cd "$(dirname "$0")/.."
bash scripts/gt.sh python - "$@" <<'EOF'
import json, math, sys, time
import httpx
BASE = "http://127.0.0.1:8000"
PLACES = ["Ballantyne", "NoDa", "South End", "Myers Park", "Lake Norman", "University City", "Matthews", "Uptown"]
c = httpx.Client(base_url=BASE, timeout=180)
places = c.get("/api/v1/places").json()["places"]
h = c.get("/api/v1/health").json()
mode = sys.argv[1] if len(sys.argv) > 1 else ""

def tiles(b, z):
    w, s, e, n = b
    def xy(lon, lat):
        k = 2 ** z
        return int((lon + 180) / 360 * k), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * k)
    x0, y1 = xy(w, s); x1, y0 = xy(e, n)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

if mode != "--outlines-only":
    for year in (2022, 2020):
        for p in PLACES:
            ts = [t for z in range(12, 18) for t in tiles(places[p], z)]
            t0 = time.time(); ok = 0
            for z, x, y in ts:
                r = c.get(f"/api/v1/tiles/naip/{year}/{z}/{x}/{y}.png")
                ok += r.status_code in (200, 204)
            print(f"tiles {year} {p:16s} {ok}/{len(ts)} in {time.time()-t0:.0f}s", flush=True)

if mode != "--tiles-only":
    if not any(i["aoi"] == "county" for i in h["indexes"]):
        print("county index not built yet: skipping outlines"); sys.exit(0)
    pc = c.post("/api/v1/tools/pick_candidates", json={"aoi": "county", "query": "swimming pool"}).json()
    concept = pc["result"]["metrics"]["sense_list"]
    for sense in ("backyard_pool", "community_pool"):
        ex = next((f["properties"]["example"] for f in pc["result"]["geojson"]["features"]
                   if f["properties"]["sense_id"] == sense), None)
        if not ex:
            continue
        for p in PLACES:
            t0 = time.time()
            r = c.post("/api/v1/tools/find_all", json={"aoi": "county", "examples": [ex], "place": p, "concept": concept,
                                                       "k": 200, "outline_top": 200}).json()
            m = r["result"]["metrics"]
            print(f"outlines {sense:14s} {p:16s} queued {m.get('outlines_queued')} outlined {m.get('outlined')} ({time.time()-t0:.1f}s)", flush=True)
    print("outline worker continues in the API process; re-run to see 'outlined' rise")
EOF
