# Agent execution report (sprint 2026-11-08)

## How the work was organised

- **One lead plus four specialist agents (Opus).** Each agent owned disjoint files. The lead integrated their work, reviewed it, and re-ran every agent's tests before committing.
- **The 12 roles in the brief were merged:**
  - the architect, frontend, backend/platform and testing roles were held by the lead;
  - the four agents were Places, Geography, Validation backend, and Compliance/Data.
- **Why only four agents:** the PC was memory-tight (2.4 GB free in Windows at the start). Running 12 concurrent agents would have competed for RAM and for the one running API.
- **Shared rules for every agent:**
  - no edits to `api/app.py`, `investigate.py`, `planner.py` or `web/`;
  - no API restarts;
  - no commits;
  - no secrets;
  - no writes to `data/labels/`.

## Agents

| Agent | Delivered | Verified by the lead | Notes |
|---|---|---|---|
| Places (Foursquare / Yelp / Google) | `engine/groundtruth/places/*`, `api/places_api.py`, `conf/providers.json`, 3 test files (50 pass, 3 live skipped), `.env.example`, `API_PROVIDER_MATRIX.md`, `compliance/API_TERMS_REVIEW.md` | re-ran the tests; added the people-data check before any provider call plus its test; added the optional `search_providers` investigation step | no keys exist, so nothing live was tested; Google is disabled by default |
| Geography | `engine/groundtruth/geography.py`, `data/boundaries/v1` (county, 7 municipalities from OSM/Census with IoU checks, 114 neighbourhoods, 36 ZIP areas, lakes), `scripts/build_boundaries.py`, 74 tests, `GEOSPATIAL_VALIDATION_REPORT.md` | wired into `compile_plan`: text wins over the map area; exact-boundary clipping; outside-county answers. Fixed an integration bug (a radius taken from a shore phrase). Added 3 integration tests. | it found the root cause of the "Myers Park instead of the county" bug |
| Validation backend | `engine/groundtruth/validation.py`, `api/validation_api.py`, sampling + metrics scripts, `data/validation/v1` (498 samples, 6 census tiles), 10 tests, `VALIDATION_STUDIO.md`, `COMPUTER_VISION_EVALUATION.md` | built the Studio UI against its API; aligned the metrics fields; added the read-only `studio_check.py` | did not review any samples itself (correct) |
| Compliance / data / security | `conf/sources.json` (22 sources), `DATA_SOURCE_REGISTRY.md`, `compliance/{LICENSE_INVENTORY, MODEL_LICENSE_REVIEW, DATA_SOURCE_LICENSE_REVIEW, COMMERCIAL_USE_APPROVAL, APPROVAL_REQUIRED}.md`, `SECURITY_AUDIT.md` | applied the security fixes it flagged (CORS, `last_text` people check, asset path checks, export names and cap, `.env.example`); built the per-layer freshness UI on its registry | made more metadata requests than allowed (disclosed); its scratch `secrets.py` shadowed a stdlib module in the lead's scratchpad (renamed; no effect on the repo) |

## Lead's own work

- **Investigation reliability:**
  - enforced step timeouts;
  - denominator integrity;
  - provenance kinds and chips;
  - optional provider step;
  - geography integration.
- **"Find like this":** negatives applied after the scan.
- **UI:**
  - the Validation Studio;
  - freshness per layer;
  - provenance badges;
  - the sources panel.
- **Endpoints:** `/api/v1/sources` and `/api/v1/runtime`.
- **Scripts:** regression, studio check, smoke, ledger.
- **Docs:**
  - RED_GREEN_MASTER_AUDIT;
  - PERFORMANCE_REPORT;
  - this report;
  - IMPLEMENTATION_ROADMAP;
  - FINAL_EXECUTION_REPORT.
