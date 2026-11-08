#!/usr/bin/env bash
# Verify the slice bundle works by itself: unpack into an empty dir, run API calls against it (no repo data/).
set -euo pipefail
cd "$(dirname "$0")/.."
B=$(ls dist/groundtruth-slices-*.tar.gz | head -1)
T=$(mktemp -d)
tar -xzf "$B" -C "$T"
(cd "$T" && sha256sum -c --quiet MANIFEST.sha256 && echo "manifest ok")
GROUNDTRUTH_DATA="$T/data" GROUNDTRUTH_DEVICE=cpu GROUNDTRUTH_THREADS=4 HF_HUB_OFFLINE=1 bash scripts/gt.sh python - <<'EOF'
from fastapi.testclient import TestClient
from api.app import app
c = TestClient(app)
h = c.get("/api/v1/health").json(); print("health:", [(i["aoi"], i["chips"]) for i in h["indexes"]])
for aoi in ("south_charlotte", "unc_charlotte"):
    st = c.post("/api/v1/tools/search_text", json={"query": "swimming pool", "k": 5, "aoi": aoi}).json()
    print(aoi, "search_text", st["status"], st["result"]["metrics"]["count"])
fs = c.post("/api/v1/tools/find_similar", json={"examples": ["2022_e4472_n33675#31"], "k": 5, "aoi": "south_charlotte"}).json()
print("find_similar", fs["status"], fs["result"]["metrics"])
print("layers:", c.get("/api/v1/layers").json())
al = c.post("/api/v1/tools/add_layer", json={"name": "osm_i485"}).json()
print("add_layer osm_i485", al["status"], al["result"]["metrics"], [s["name"][:40] for s in al["sources"]][:2])
print("chip jpg:", c.get("/api/v1/chips/2022_e4472_n33675.jpg?aoi=south_charlotte").status_code)
EOF
rm -rf "$T"
