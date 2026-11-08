"""Local LLM vs rules on the planner phrase set (tests/planner/phrases.json + phrases_llm.json).
Plan accuracy (listed fields match), declines (policy layer BEFORE any LLM call), schema-invalid count, <think> leaks,
plan latency p50/p95 (PC → tunnel → laptop). Calls the LLM directly (no plan cache).
  python scripts/llm_eval.py [--out data/out/llm_eval.json]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))
from api.planner.llm import local  # noqa: E402
from groundtruth.planner import parse  # noqa: E402
from groundtruth.tools import _people  # noqa: E402

P = json.loads((ROOT / "tests" / "planner" / "phrases.json").read_text())["phrases"] + \
    json.loads((ROOT / "tests" / "planner" / "phrases_llm.json").read_text())["phrases"]


def rules_plan(text, prev):
    p0 = parse(text)
    if prev and not p0.target_kind:
        p0 = parse(f"{prev} {text}")
    return p0.__dict__


def norm(v):
    if isinstance(v, list):
        return sorted(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v))  # 1609 vs 1609.34 m
    return v.lower() if isinstance(v, str) else v


def check(plan, expect):
    return {k: (plan.get(k), v) for k, v in expect.items() if norm(plan.get(k)) != norm(v)}


res = {"rules": {"ok": 0, "n": 0}, "llm": {"ok": 0, "n": 0, "invalid": 0, "errors": 0, "think_leaks": 0}, "declines": {"ok": 0, "n": 0}}
ms, fails = [], []
for t in P:
    want_dec = bool(t.get("decline"))
    if want_dec or "expect" not in t:
        res["declines"]["n"] += 1
        res["declines"]["ok"] += _people(t["text"]) == want_dec
        if _people(t["text"]) != want_dec:
            fails.append(f"DECLINE {t['text']}")
        continue
    prev = t.get("previous")
    res["rules"]["n"] += 1
    bad_r = check(rules_plan(t["text"], prev), t["expect"])
    res["rules"]["ok"] += not bad_r
    res["llm"]["n"] += 1
    raw, info = local._llm_plan(t["text"], prev)
    ms.append(info["ms"])
    if info.get("think_leak"):
        res["llm"]["think_leaks"] += 1
    if info.get("error"):
        res["llm"]["errors"] += 1
        fails.append(f"LLM ERROR {t['text']!r}: {info['error']}")
        continue
    if raw is None:
        res["llm"]["invalid"] += 1
        fails.append(f"LLM INVALID {t['text']!r}")
        continue
    p = local.hybrid_plan(raw, t["text"], prev)[0].__dict__
    bad = check(p, t["expect"])
    res["llm"]["ok"] += not bad
    if bad:
        fails.append(f"LLM {t['text']!r}{' (prev ' + repr(prev) + ')' if prev else ''}: {bad}")
    if bad_r:
        fails.append(f"RULES {t['text']!r}: {bad_r}")
res["llm"]["plan_ms_p50"] = round(float(np.percentile(ms, 50)))
res["llm"]["plan_ms_p95"] = round(float(np.percentile(ms, 95)))
res["phrases"] = len(P)
print(json.dumps(res, indent=1))
for f in fails:
    print("  " + f)
out = ROOT / "data" / "out" / "llm_eval.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"result": res, "fails": fails, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1))
