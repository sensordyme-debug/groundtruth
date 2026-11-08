"""Run the rules planner on the required prompts via the API; print plan, step tools and the final say."""
import sys
import time

import httpx

PROMPTS = sys.argv[1:] or ["find swimming pools", "pools within 1 km of I-485 in Ballantyne", "parking lots in Uptown",
                           "solar on warehouses", "pools in flood zones in Matthews", "who lives at 123 Main St"]
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
ok_all = True
for p in PROMPTS:
    t0 = time.perf_counter()
    r = c.post("/api/v1/agent/run", json={"text": p}).json()
    ms = (time.perf_counter() - t0) * 1000
    tools = [s["tool"] + ("✗" if s["envelope"]["status"] == "declined" else "") for s in r["steps"]]
    say = next((a["text"] for a in reversed(r["actions"]) if a["action"] == "say"), "")
    acts = [a["action"] for a in r["actions"]]
    good = r["status"] in ("ok", "declined") and bool(say)
    ok_all &= good
    print(f"{'PASS' if good else 'FAIL'} {ms:6.0f} ms  '{p}'\n   plan: concept={r['plan'].get('concept')} place={r['plan'].get('place')} "
          f"layer={r['plan'].get('layer')} within_m={r['plan'].get('within_m')} host={r['plan'].get('host_layer')}\n"
          f"   steps: {tools}\n   actions: {acts}\n   say: {say}")
print("RESULT:", "PASS" if ok_all else "FAIL")
