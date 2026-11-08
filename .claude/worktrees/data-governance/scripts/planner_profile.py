"""Per-step timing of planner requests (sum of each step envelope's steps[].ms)."""
import sys
import time

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=600)
for p in sys.argv[1:]:
    t0 = time.perf_counter()
    r = c.post("/api/v1/agent/run", json={"text": p}).json()
    tot = (time.perf_counter() - t0) * 1000
    parts = []
    for s in r["steps"]:
        ms = sum(x.get("ms", 0) for x in s["envelope"].get("steps", []))
        sub = ", ".join(f"{x['phase']} {x['ms']:.0f}" for x in s["envelope"].get("steps", []) if x.get("ms", 0) > 100)
        parts.append(f"{s['tool']} {ms:.0f}" + (f" [{sub}]" if sub else ""))
    print(f"{tot:6.0f} ms '{p}': " + " | ".join(parts))
