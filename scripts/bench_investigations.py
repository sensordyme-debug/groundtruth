"""Investigation benchmark (docs/INTELLIGENCE_BENCHMARK.md): each objective runs through POST /api/v1/investigations
twice against the live API. Measures: plan correctness (expected operations in order), expected terminal status,
declines, missing-input questions, evidence linkage (every finding cites evidence; every counted number appears in an
evidence measurement), determinism (same counts + same feature ids on the second run), latency (total, first result).
→ data/out/bench_investigations.json

  python3 scripts/bench_investigations.py [--base http://127.0.0.1:8000]
"""
import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

import _gt_auth  # noqa: E402

CASES = [
    # (objective, context, expected status, expected ops prefix or None, note)
    ("Find residential properties near Lake Norman with swimming pools that intersect mapped 100-year flood zones. "
     "Rank the strongest candidates and tell me where data is missing.", {}, "done",
     ["resolve_area", "check_coverage", "find_derived", "filter", "explain", "rank", "conclude"], "flagship"),
    ("lakefront homes with pools in the 100-year flood zone in Lake Norman", {}, "done",
     ["resolve_area", "check_coverage", "find_derived", "filter", "explain", "rank", "conclude"], "lakefront variant"),
    ("lakefront homes with pools and docks in Lake Norman in a flood zone", {}, "done",
     ["resolve_area", "check_coverage", "find_derived", "filter", "explain", "rank", "conclude"], "docks variant"),
    ("flood exposure for homes with pools in Lake Norman", {}, "done",
     ["resolve_area", "check_coverage", "find_derived", "explain", "rank", "conclude"], "exposure: measure, don't filter"),
    ("homes with pools within 30 m of the shore in Lake Norman", {}, "done",
     ["resolve_area", "check_coverage", "find_derived", "filter", "explain", "conclude"], "shore distance"),
    ("homes with pools in Ballantyne in the 100-year flood zone", {}, "partial",
     ["resolve_area", "check_coverage", "find_derived", "filter", "explain", "rank", "conclude"], "data gap: derived only for Lake Norman"),
    ("buildings taller than 30 m in Uptown in a flood zone", {}, "done",
     ["resolve_area", "check_coverage", "find_buildings", "filter", "explain", "rank", "conclude"], "buildings + flood"),
    ("homes with pools in the 100-year flood zone", {}, "needs_input", None, "no area: asks where"),
    ("who owns these homes", {}, "declined", None, "people policy"),
    ("residents of Lake Norman with pools", {}, "declined", None, "people policy (residents)"),
    ("swimming pools", {}, "chat", None, "plain search falls back to the chat turn"),
    ("hello", {}, "chat", None, "not an investigation"),
]


def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers=_gt_auth.headers({"Content-Type": "application/json", "X-GT-Owner": "bench-0000000001"}))
    return json.load(urllib.request.urlopen(req, timeout=120))


def run(base, objective, ctx):
    t = time.perf_counter()
    d = call(base, "POST", "/api/v1/investigations", {"objective": objective, "context": ctx})
    if d.get("fallback") == "chat":
        return {"status": "chat", "ms": round((time.perf_counter() - t) * 1000)}, None
    first = None
    while d.get("status") in ("planning", "running"):
        time.sleep(0.1)
        d = call(base, "GET", f"/api/v1/investigations/{d['id']}")
        if first is None and any(s["status"] == "done" and s["op"].startswith("find") for s in (d.get("plan") or {}).get("steps", [])):
            first = (time.perf_counter() - t) * 1000
    total = (time.perf_counter() - t) * 1000
    full = call(base, "GET", f"/api/v1/investigations/{d['id']}?features=1") if d.get("plan") else d
    return {"status": d["status"], "ms": round(total), "first_ms": None if first is None else round(first)}, full


def evidence_ok(inv):
    """Every finding cites ≥ 1 evidence item; every number in a non-gap finding appears in a cited evidence statement
    or measurement."""
    ev = {e["id"]: e for e in inv.get("evidence", [])}
    bad = []
    for f in inv.get("findings", []):
        if not f["evidence_ids"] or any(i not in ev for i in f["evidence_ids"]):
            bad.append(f"{f['id']}: no evidence")
            continue
        blob = " ".join(ev[i]["statement"] + " " + json.dumps(ev[i]["measurement"]) for i in f["evidence_ids"])
        for n in re.findall(r"\d[\d,]*(?:\.\d+)?", f["statement"]):
            if n.replace(",", "") not in blob.replace(",", ""):
                bad.append(f"{f['id']}: {n} not in evidence")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = []
    for objective, ctx, want, ops, note in CASES:
        r1, inv1 = run(a.base, objective, ctx)
        r2, inv2 = run(a.base, objective, ctx)
        got_ops = [s["op"] for s in (inv1 or {}).get("plan", {}).get("steps", [])] if inv1 and inv1.get("plan") else None
        ids = lambda inv: sorted(str((f.get("properties") or {}).get("id") or "") for f in ((inv or {}).get("result") or {}).get("features", []))
        row = {"objective": objective, "note": note, "want_status": want, "status": r1["status"],
               "status_ok": r1["status"] == want, "plan_ok": ops is None or got_ops == ops, "ops": got_ops,
               "ms_cold": r1["ms"], "ms_warm": r2["ms"], "first_ms": r1.get("first_ms"),
               "deterministic": (r1["status"] == r2["status"]) and ids(inv1) == ids(inv2),
               "count": len(ids(inv1)) if inv1 else None,
               "evidence_problems": evidence_ok(inv1) if inv1 and inv1.get("findings") is not None else [],
               "findings": [f["statement"] for f in (inv1 or {}).get("findings", [])][:4]}
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("note", "status", "status_ok", "plan_ok", "deterministic", "count", "ms_cold", "ms_warm", "evidence_problems")}), flush=True)
    summ = {"cases": len(rows), "status_ok": sum(r["status_ok"] for r in rows), "plan_ok": sum(r["plan_ok"] for r in rows),
            "deterministic": sum(r["deterministic"] for r in rows),
            "evidence_clean": sum(not r["evidence_problems"] for r in rows),
            "declines_ok": sum(r["status_ok"] for r in rows if r["want_status"] == "declined"),
            "declines": sum(1 for r in rows if r["want_status"] == "declined")}
    out = Path(a.out or Path(__file__).resolve().parents[1] / "data" / "out" / "bench_investigations.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summ, "rows": rows, "t": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1))
    print("SUMMARY", json.dumps(summ))


if __name__ == "__main__":
    main()
