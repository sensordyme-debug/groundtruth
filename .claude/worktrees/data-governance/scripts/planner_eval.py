"""Planner test set (tests/planner/phrases.json): plan accuracy (every listed field matches) and decline accuracy
(people-data phrases declined, all others not). No engine needed: parse() + the people-data guard."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth.planner import parse  # noqa: E402
from groundtruth.tools import _people  # noqa: E402

T = json.loads((ROOT / "tests" / "planner" / "phrases.json").read_text())["phrases"]
plan_ok = plan_n = dec_ok = 0
fails = []
for t in T:
    want_decline = bool(t.get("decline"))
    got_decline = _people(t["text"])
    dec_ok += got_decline == want_decline
    if got_decline != want_decline:
        fails.append(f"DECLINE {'missed' if want_decline else 'wrong'}: {t['text']}")
    if "expect" in t:
        plan_n += 1
        p = parse(t["text"]).__dict__
        bad = {k: (p.get(k), v) for k, v in t["expect"].items() if p.get(k) != v}
        plan_ok += not bad
        if bad:
            fails.append(f"PLAN {t['text']!r}: {bad}")
print(f"plan accuracy {plan_ok}/{plan_n} ({100 * plan_ok / max(plan_n, 1):.0f}%) · decline accuracy {dec_ok}/{len(T)} "
      f"({100 * dec_ok / len(T):.0f}%)")
for f in fails:
    print("  " + f)
sys.exit(0 if dec_ok == len(T) else 1)
