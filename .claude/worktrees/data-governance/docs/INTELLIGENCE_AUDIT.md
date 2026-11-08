# Intelligence audit (2026-11-08)

Baseline for the move from conversational search to evidence-backed investigations.

**Repository state**
- Branch `intelligence/evidence-grounded-investigation`, created from `eaed886`. That is the local WIP head of `redesign/conversational`; `web/app-v2` exists only on that line.
- `demo-final` (`fe76009`) and its data are untouched.

**Method**
- Two read-only audits: backend contracts and tests; data, models and dependencies.
- My own runs against the live API.
- File references are to that commit.

Classification legend:
- **P** production-like
- **V** implemented but not sufficiently validated
- **E** experimental
- **X** not implemented
- **B-data / B-hw / B-lic** blocked by data, hardware or licence

## 1. Architecture (as found)

**Request path.** `POST /api/v1/agent/run` (`api/app.py:339`) calls `brain.chat` (`api/planner/brain.py:17`):
1. The people/export policy runs first (`tools._people`, `planner.EXPORT_RE`).
2. `local.llm_plan`: Qwen3-4B via llama.cpp on the laptop through an SSH tunnel. It uses a JSON-schema grammar, has a 4 s timeout, and returns `None` on any error (`local.py:131`).
3. `hybrid_plan`: the LLM's fields are filled and sanity-checked against the rules `parse()`.
4. `planner.run` → `_run` (`engine/groundtruth/planner.py:279`): one target step plus filter / compare / measure / export steps.
5. An optional LLM rewrite of the reply, with a number guard.
- Fallback: when the LLM is unreachable the rules planner answers alone.
- Weakness: errors aren't cached and there is no circuit breaker, so every request waits up to the full 4 s timeout.

**Engine.** `engine/groundtruth/tools.py` is the `Engine` class, 2,276 lines. It holds:
- 16 tools that return the `Envelope` (`engine/groundtruth/envelope.py:34`);
- a `@memo` cache keyed by the `inspect.signature` binding;
- DINOv2 / OpenCLIP / SAM 2.1 / Grounding DINO through `embed.py`, `outline.py` and `openvocab.py`;
- DuckDB Spatial (`vector.py`), Shapely, pyproj and rasterio;
- Valhalla routing.

**Frontend.** `web/app-v2` (React + TS + MapLibre, one map instance in `src/map.ts`). The classic UI is at `/classic`.

## 2. Capabilities

| Capability | Where | Class | Evidence / limit |
|---|---|---|---|
| Visual search (pool, parking, …) by text / example | `pick_candidates`, `find_all` | V | Pool P@24 0.583 vs labels+OSM. Labels cluster at Lake Norman (51 of 59 pool labels). |
| Exhaustive scan in a place | `find_all` → `scan.py` | V | Every patch is scored, but recall is not guaranteed. OSM recall in Ballantyne: 14/25 standard, 19/28 dense. |
| Dense pool mode | `dense.py`, data/dense/v1 (8 places) | V | Lake Norman labels 41 → 46 of 51. Off in Uptown (~7/50 eye precision). |
| Outlines | `outline.py` (SAM 2.1 tiny) | E | Pool median IoU 0.379 (n=14). |
| Open vocabulary | `openvocab.py` (Grounding DINO tiny) | E | 53/108 across 9 concepts. Boat docks 12/12. |
| Homes with pools → lakefront → own dock | `derived.py`, data/derived/v1 | V | **Lake Norman only.** Eye precision measured on draft0/draft1, not on v1. |
| Docks new since 2020 | build-time deck/water check | E | 0 of 69 new; eye-checked sheet. |
| Change since 2020 | `compare_years` | E | Ballantyne: 17 flagged, about 9 clearly new. |
| Flood / shore / elevation / canopy / slope / height facts | `risk.py`, `filter` predicates, `explain` | V | FEMA BFE exists on only **10 of 8,761** polygons. Canopy and slope rasters are not built (B-data). |
| Travel time | `isochrone`, Valhalla | V | Mecklenburg roads only. |
| Buildings, places | `find_buildings`, `find_place` (Overture) | P | Mapped data, not detections. |
| Find like this (click/box → similar) | `point.py`, `/point/find` | E | See §7. |
| Sessions, areas, pins | `api/sessions.py` | V | Anonymous owner header; no auth. |
| Multi-step investigations, evidence | (none before this branch) | X → built here | See INTELLIGENCE_ARCHITECTURE.md. |
| Change monitoring | (none) | X | No scheduler or data refresh. Not claimed. |
| Seam-free mosaic tiles | `scripts/heavy/build_mosaic.py` (partial) | X | See §5. |

## 3. API contracts

- **Envelope** (`envelope.py`): tool, version, status ok/partial/declined, result{geojson, metrics}, confidence, method, assumptions, limits, sources, steps, declined_reason.
- **Confidence is never calibrated.**
  - It is a mean of heuristic per-feature scores (`tools.py:205-232`), with hard-coded values such as 1.0 for filter and 0.8 for isochrone.
  - The investigation layer therefore does **not** surface it. It grades evidence strength instead (§ INTELLIGENCE_ARCHITECTURE).
- **Planner output:** `{plan, steps[{ref, tool, envelope}], actions, status, current_ref, ask}`.
  - A declined sub-step makes the whole answer `partial` without saying which constraint was dropped.

## 4. Frontend state

As found:
- `App.tsx` holds all conversation, map and session state.
- Sessions persist message lists with result ids. On reopen, results are re-run from the engine caches.
- `map.ts` draws results through zoom-banded paint expressions. No DOM markers.

## 5. Data coverage

| Data | Coverage |
|---|---|
| NAIP 2022 | 56 scenes, whole county, local COGs |
| NAIP 2020 | 43 of 57 scenes cached. **14 missing**, so raw 2020 tiles and crops fail outside the demo areas. |
| Chip index v3c8p192 | County, 2022 and 2020, 107,770 chips each |
| Lidar v2 | 6 areas: Ballantyne, CLT Airport, Lake Norman, Myers Park, University City, Uptown |
| Dense | 8 places, pools, 2022 only |
| Derived homes-with-pools | Lake Norman only |
| FEMA NFHL | 8,761 regulatory polygons: 8,694 AE (100-year), 67 shaded X (500-year); BFE on 10 |
| Shoreline | Lake Norman, Lake Wylie, Mountain Island Lake, Catawba |
| Routing | Mecklenburg |
| Openvocab cache | 1 query |

Manifest gaps:
- `DEMO_MANIFEST.json` protects demo-final's data, including lidar v1.
- `derived/v1`, `risk/v1` and `lidar/v2` are **not** in the manifest.

Seams:
- Scenes come from different flights (2022: 06-02, 06-22, 09-27), so tiles show colour steps, worst on Lake Norman water.
- The mosaic build is partial. Measured overlap mismatch is small (median 0.54 grey levels). The visible band is a low-frequency brightness gradient that the gain/offset fit does not model.
- Status: **not fixed** (`docs/MOSAIC.md`).

## 6. Tests (baseline 35, now 43)

Existing tests cover:
- envelope validators;
- the label store;
- `parse()`;
- rerank helpers;
- the tiler and provider;
- counts;
- sessions.

Never tested, according to the audit:
- `_run` and multi-step plans;
- partial results;
- `brain.chat`, LLM fallback, the number guard;
- risk predicates and `explain`;
- point find;
- any HTTP route.

Added on this branch: `tests/test_investigate.py` (8 tests: plans, validation, dependency blocking, evidence, report, policy).

## 7. Find like this (the reported 7,137-match problem)

**Root cause** (`scan.py:116-174`, `rerank.py:32`, `concepts.json:3`):
- With `concept=None` there is no verifier.
- A chip passes at cosine ≥ 0.5 against the mean of the example cells. Patches pass at score ≥ 0.7.
- The "closer to the negatives" test runs only when negatives exist. One click gives none.
- The click in that test was on a house, not a pool.

**Fix** (`engine/groundtruth/point.py: calibrate`, `api/point_api.py`):
- Per-click background calibration.
  - Sample 3,000 background patches from the same area (seeded).
  - Keep only candidates above the 99.5th percentile of their similarity to the examples.
  - Keep the scan's order.
- "Not this" clicks become soft scan negatives plus a patch-level rejection. Before, they were hard negatives, which emptied the set (137 → 3).

**Measured** (`scripts/eval_point_find.py`, details in EVALUATION.md):
- Lake Norman click: 7,137 → 1,160.
- Ballantyne pool click: 137 → 51. Eye precision of the calibrated top 24 is **23/24**; OSM, a lower bound, gives 0.42.
- Warehouses: 432 → 154. Eye precision about 22/24.
- Latency: a cold first scan of a 100 km² area takes 21 s; warm it takes 0.2–0.8 s.
- Parking lots still collapse after one "not this" (open issue).

## 8. Performance bottlenecks

- Cold scans: the first `find_all` in a large area takes 3–21 s, while the index pages in.
- The 4 s LLM timeout when the laptop is unreachable.
- Windows free RAM is 2.4 GB with the API warm (rule: ≥ 4 GB). The WSL VM keeps about 13 GB of page cache.
- Engine methods share caches without locks (the `_busy` counter is not atomic).

## 9. Security and privacy

- **Fixed on this branch:** the people policy matched substrings, so "resident" matched "residential" and the flagship prompt was declined. It now matches whole words, with tests.
- **Open:**
  - CORS is `*` and there is no authentication. The service relies on tailnet-only exposure (`tailscale serve`, no Funnel).
  - Sessions trust an anonymous header.
  - `/tools/export` writes files with no rate limit.
  - The client-supplied `last_text` reaches the LLM without a people check.
- Record of the policy: people data is never processed. Homes are buildings, not owners.

## 10. Licensing

- **Grounding DINO** (Apache weights; training data partly research-only) carries a legal-review flag before any ORYN commercial use (LICENCE_GATE D1).
- **Ultralytics YOLO** is AGPL and forbidden.
- Nothing new was added on this branch except **lucide-react (ISC)** for the UI icons, recorded in SOURCES.

## 11. Highest-value opportunities, and what this branch did

1. **Investigations:** typed plans, dependency-aware execution, an evidence ledger, graded findings. **Built.**
2. **Policy false positives** on "residential". **Fixed.**
3. **Find like this calibration.** **Built and measured.** The refine loop still needs better negative handling for large flat classes such as parking.
4. **Re-measure derived v1 precision**, which would lift the flagship's evidence from "weak" to graded. **Not yet.**
5. **Seam mosaic** (dodging step). **Not done.**
6. **Canopy and slope rasters** for the "trees over pools" questions. **Not built.**
7. **Register derived/risk/lidar v2 in DEMO_MANIFEST.** **Not done.**
