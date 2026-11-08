# Intelligence benchmark

A reproducible check that investigations plan correctly, execute honestly, link their numbers to evidence and repeat exactly. The targets are goals, not claims. Numbers below are from real runs against the live API (local PC, warm index, LLM not involved in compilation).

## How to run (PC WSL, API on :8000)

```
python3 scripts/bench_investigations.py          # 12 objectives × 2 runs → data/out/bench_investigations.json
~/.venvs/gt-browser/bin/python scripts/demo_check_v3.py --size 1440x900   # the UI journey (17 checks)
bash scripts/gt.sh python scripts/eval_point_find.py                       # Find like this, 4 object types
bash scripts/gt.sh pytest -q                                               # unit + plan-validation tests
```

## What is measured

| Area | Metric | Target | Measured (2026-11-08) |
|---|---|---|---|
| Planning | expected terminal status (done / partial / needs_input / declined / chat) | 100 % | **12/12** |
| Planning | expected operation sequence | 100 % | **12/12** |
| Planning | invalid plans rejected before execution (unknown op, bad args, unsupported filter key, forward dependency, input-less filter) | 100 % | **4/4** cases in `tests/test_investigate.py` |
| Planning | missing input handled (no area → "Where should I look?") | 100 % | 1/1 |
| Policy | people questions declined before any model or tool | 100 % | **2/2** ("who owns…", "residents of…"); "residential properties" no longer declined |
| Execution | a failed step blocks its dependents; the rest still runs; status partial | yes | tested (fake engine) + live (Ballantyne derived missing → partial) |
| Evidence | findings that cite evidence; every number in a finding appears in its cited evidence | 100 % | **12/12** (after storing the unavailable count in the evidence; the first run was 11/12) |
| Determinism | identical status and identical result ids on a second run | 100 % | **12/12** |
| Latency | flagship total, warm | < 3 s | 0.86–2.0 s (bench + UI runs) |
| Latency | flagship first result (first find step done) | < 1.5 s | 0.4–0.8 s |
| Latency | follow-up ("which of those …") | < 1 s | 0.34–0.43 s |
| Latency | measure all 946 homes for flood + elevation + shore | — | 10.4 s cold, 4.4 s warm (lidar windows) |
| UI | demo_check v3 at 1440×900, 1920×1080, 1280×800 | all pass | **17/17** at each size |

## Computer vision (from docs/EVALUATION.md)

Baselines are re-stated, not re-measured, unless marked.

| Item | Result |
|---|---|
| Pool search P@24 (labels+OSM, lower bound) | 0.583; leave-one-out `find_all` 0.492 |
| Look-alike rejection | 2/5 |
| Pool outline median IoU | 0.379 (n=14) |
| Open vocabulary | 53/108 across 9 concepts; boat docks 12/12 |
| Find like this (new, measured), one click, Ballantyne pool | calibrated 137 → 51 results; **eye precision 23/24**; OSM lower bound 0.42 |
| Find like this, warehouses | 432 → 154; eye ~22/24; OSM 0.54 |
| Find like this, refine loop | Measured with an OSM oracle that rejects unmapped real pools, so the oracle is flawed: pools 3 refines → 15 results at OSM 0.73. Parking still collapses to 1–3 after one "not this" (open). |
| Geographic generalisation | Not measurable yet: 51/59 pool labels sit in one 1.7 km stretch of Lake Norman. The Find-like-this evaluation deliberately used Ballantyne and Steele Creek examples with OSM as the reference. |

## Known limits of this benchmark

- 12 objectives exercise the plan shapes the data supports (Lake Norman derived set; buildings; people policy; fallbacks). It is not a broad language test; the planner's 49-phrase and 84-phrase sets cover parsing breadth.
- Evidence linkage checks numbers textually against cited evidence. It does not prove the evidence is right; that is what the eye-checks and the FEMA geometry test are for.
- Detector precision for derived v1 (homes with pools, lakefront, own dock) is **not re-measured**. Every finding that depends on it is graded "weak" until it is.
