"""Warm latency on the running API: pick, find_all (county-wide and in a place), refine. Run 2 rounds; report round 2."""
import sys
import time

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=300)
place = sys.argv[1] if len(sys.argv) > 1 else "Ballantyne"


def t(tool, body):
    t0 = time.perf_counter()
    e = c.post(f"/api/v1/tools/{tool}", json={"aoi": "county", **body}).json()
    return e, (time.perf_counter() - t0) * 1000


for rnd in (1, 2):
    pc, ms_pick = t("pick_candidates", {"query": "swimming pool"})
    ex = next(f["properties"]["example"] for f in pc["result"]["geojson"]["features"] if f["properties"]["sense_id"] == "backyard_pool")
    fa_c, ms_fac = t("find_all", {"examples": [ex], "concept": "swimming_pool", "k": 200, "outline_top": 50})
    fa_p, ms_fap = t("find_all", {"examples": [ex], "concept": "swimming_pool", "k": 200, "place": place, "outline_top": 50})
    hits = fa_p["result"]["geojson"]["features"]
    sid, ms_ref = None, []
    for k in range(3):
        acc = ([ex] if sid is None else []) + [hits[k]["properties"]["example"]]
        rf, ms = t("refine", {"session_id": sid, "accept": acc, "reject": [hits[-1 - k]["properties"]["example"]],
                              "k": 200, "place": place, "concept": "swimming_pool"})
        sid = rf["result"]["metrics"]["session_id"]; ms_ref.append(round(ms))
    print(f"round {rnd}: pick {ms_pick:.0f} ms · find_all county {ms_fac:.0f} ms ({fa_c['result']['metrics']['count_detected']} detected) · "
          f"find_all {place} {ms_fap:.0f} ms ({fa_p['result']['metrics']['count_detected']} detected) · refine {ms_ref} ms")
