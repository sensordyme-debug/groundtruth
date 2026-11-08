"""Platform audit probe: does the planner build DIFFERENT workflows for different objectives? Runs a varied set of
objectives through the live API (POST /investigations) and records, per objective: status, the plan's ops in order,
the resolved scope, the headline, timing. Output: data/out/audit_planner.json + a table on stdout.
Usage: python scripts/audit_planner.py [--base URL]"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _gt_auth  # noqa: E402

OBJECTIVES = [
    "Find residential properties near Lake Norman with swimming pools that intersect mapped 100-year flood zones",
    "Which homes in Myers Park have pools under heavy tree canopy?",
    "Find houses with docks on Lake Norman that were built since 2020",
    "Show buildings in University City within 500 m of a light rail station",
    "Find restaurants in South End",
    "Which pools in Ballantyne sit on steep slopes?",
    "Find solar panels in Huntersville",
    "Compare pools in Ballantyne between 2020 and 2022",
    "How many buildings are in the floodway in Charlotte?",
    "Find parking lots in Uptown",
    "Find pools within 30 m of the shore of Lake Norman",
    "Who lives in the house at 123 Main St?",
    "Find pools in Atlanta",
    "What is the weather today?",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    H = _gt_auth.headers({"Content-Type": "application/json", "X-GT-Owner": "audit-000000001"})

    def call(m, p, b=None):
        r = urllib.request.Request(a.base + "/api/v1" + p, method=m, headers=H,
                                   data=None if b is None else json.dumps(b).encode())
        return json.load(urllib.request.urlopen(r, timeout=300))

    rows = []
    for obj in OBJECTIVES:
        t = time.perf_counter()
        try:
            d = call("POST", "/investigations", {"objective": obj, "context": {}})
            while d.get("status") in ("planning", "running"):
                time.sleep(0.15)
                d = call("GET", f"/investigations/{d['id']}")
        except Exception as e:  # noqa: BLE001
            rows.append({"objective": obj, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        plan = d.get("plan") or {}
        rows.append({"objective": obj, "status": d.get("status"), "ms": round((time.perf_counter() - t) * 1000),
                     "ops": [s["op"] for s in plan.get("steps", [])],
                     "step_status": [s["status"] for s in plan.get("steps", [])],
                     "scope": (plan.get("scope") or {}).get("label"), "targets": plan.get("targets"),
                     "summary": (d.get("summary") or d.get("declined_reason") or d.get("question") or "")[:260]})
    out = Path("/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data/out/audit_planner.json")
    out.write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(f"- {r['objective'][:70]}\n    {r.get('status')} · {r.get('ms')} ms · scope={r.get('scope')} · ops={r.get('ops')}"
              f"{' · steps=' + str(r.get('step_status')) if r.get('step_status') else ''}\n    {r.get('summary') or r.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
