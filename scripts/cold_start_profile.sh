#!/usr/bin/env bash
# (PC WSL) cold-start profile: restart the API, time restart → healthy, print the warm-up phases (/health), then time
# the first flagship investigation. Usage: bash scripts/cold_start_profile.sh [blocking]   (blocking = the old order)
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
LOG=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data/out/api_restarts.log
echo "$(date '+%F %T') lead: cold-start profile (${1:-new order})" >> "$LOG"
if [ "${1:-}" = "blocking" ]; then export GROUNDTRUTH_WARMUP_CHAIN_BLOCKING=1; else export GROUNDTRUTH_WARMUP_CHAIN_BLOCKING=0; fi
T0=$(date +%s.%N)
bash scripts/api_branch.sh > /dev/null
UP=$(date +%s.%N)
until curl -s localhost:8000/api/v1/health | grep -q '"warming":false'; do sleep 1; done
T1=$(date +%s.%N)
python3 - "$T0" "$UP" "$T1" <<'PY'
import json, sys, time, urllib.request
t0, up, t1 = map(float, sys.argv[1:4])
h = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health"))
w = h["warmup"]
print(f"restart -> process up: {up - t0:.1f} s · restart -> healthy: {t1 - t0:.1f} s · warm-up to healthy (in-process): {(w.get('healthy_ms') or w.get('ms') or 0) / 1000:.1f} s")
print("phases (ms):", json.dumps(w.get("phases")), "| flagship:", w.get("flagship"), "| llm:", w.get("llm"))
sys.path.insert(0, "scripts")
import _gt_auth
H = _gt_auth.headers({"Content-Type": "application/json"})
def call(m, p, b=None):
    r = urllib.request.Request("http://127.0.0.1:8000/api/v1" + p, method=m, data=None if b is None else json.dumps(b).encode(), headers=H)
    return json.load(urllib.request.urlopen(r, timeout=300))
t = time.perf_counter()
d = call("POST", "/investigations", {"objective": "Find residential properties near Lake Norman with swimming pools that intersect mapped 100-year flood zones. Rank the strongest candidates and tell me where data is missing.", "context": {}})
while d.get("status") in ("planning", "running"):
    time.sleep(0.1); d = call("GET", f"/investigations/{d['id']}")
print(f"first flagship investigation after healthy: {(time.perf_counter() - t) * 1000:.0f} ms · status {d['status']}")
t = time.perf_counter()
r = call("POST", "/agent/run", {"text": "swimming pools", "context": {}})
print(f"first chat search ('swimming pools') after healthy: {(time.perf_counter() - t) * 1000:.0f} ms · {r.get('status')}")
PY
