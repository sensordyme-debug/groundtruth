"""p50 / p95 per tool over the last N demo_check runs (data/out/demo_check_*.json)."""
import json, sys
from pathlib import Path
import numpy as np
n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
runs = sorted(Path("data/out").glob("demo_check_*.json"), key=lambda p: p.stat().st_mtime)[-n:]
by = {}
for r in runs:
    for row in json.loads(r.read_text())["rows"]:
        t = row["tool"] if not row["tool"].startswith("agent:") else row["tool"]
        by.setdefault(t, []).append(row["ms"])
print(f"{len(runs)} runs: {[p.name for p in runs]}")
for t, v in by.items():
    print(f"{t[:70]:70s} n={len(v)} p50 {np.percentile(v, 50):6.0f} ms  p95 {np.percentile(v, 95):6.0f} ms  max {max(v)}")
