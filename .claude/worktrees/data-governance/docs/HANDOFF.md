# Groundtruth: handoff (competition-ready checkpoint, 2026-11-08)

**Never break `demo-final`. Work on branches.** The tag and the data it reads (`data/DEMO_MANIFEST.json`) are frozen;
`scripts/rollback_demo.sh` restores them. Only one Claude Code session on the PC at a time.

## What it does
"Teach the map anything" for Mecklenburg County, NC, on public NAIP imagery (0.6 m, 2022 + 2020). Type a request or a
concept; pick what you meant from 6 tiles; Groundtruth finds, outlines, counts and measures every match in a place,
refines from ✓/✗ clicks, and filters by distance, flood zones, drive/walk time, height and change since 2020. A
deterministic rules planner turns typed requests into real tool calls (no LLM), declines anything about people.

## Run it
| what | where | how |
|---|---|---|
| API + UI | PC, WSL `Ubuntu-24.04`, `127.0.0.1:8000` | `bash scripts/restart_and_warm.sh` (healthy after ~2 min: models, index, hero flow + typed chain; other places warm in the background) |
| Demo check | PC | `bash scripts/demo_check.sh` (manifest, hero flow, typed chain, budgets, count invariants) |
| Rollback | PC | `bash scripts/rollback_demo.sh` (checkout demo-final, pin data versions, restart, demo_check) |
| Label server | PC, `:8765/label` | `scripts/run_label_server.sh` (never restart without asking) |
| Heavy CPU jobs | laptop `quantcompute`, `~/groundtruth-heavy` | `bash scripts/heavy/laptop_job.sh start <job>` · `status` · `follow`; results: `bash scripts/heavy/sync.sh pull <path>` |
| Demo access | tailnet only | `docs/DEMO_DAY.md` (`scripts/tailnet_serve.sh`, never Funnel) |

Envs: `~/.venvs/groundtruth` (CPU) and `~/.venvs/groundtruth-gpu` (API), `gt-routing`, `gt-browser`; laptop
`~/groundtruth-heavy/.venv` (CPU torch, pinned to the PC). See `docs/ARCHITECTURE.md`.

## Architecture + folders
```
web/ (React + MapLibre, thin) → api/app.py (FastAPI: one route per tool, /agent/run, tiles, vector tiles)
  → engine/groundtruth/ (the product): tools.py (envelope tools) · scan.py (exhaustive place scan) · dense.py ·
    openvocab.py (Grounding DINO) · inventory.py · planner.py (rules) · lidar.py · vector.py (DuckDB) ·
    providers/public.py (ALL data access: NAIP COGs, layers, Overture, routing) · conf/*.json (every threshold)
scripts/ build + check + bench · scripts/heavy/ laptop jobs · tests/ pytest + tests/planner/phrases.json
data/ (gitignored): precompute/<INDEX_VERSION>/county/<year>/ · dense|lidar|tiles|routing/<vN>/ (conf/data_versions.json)
  · cache/openvocab/<vN>/ · layers/ · labels/ (human test set: read-only) · DEMO_MANIFEST.json
```
Model cascade: `docs/ARCHITECTURE.md` (diagram). Contract: `docs/CONTRACT.md` (v0.4 + v0.5 additions).

## Tools (all return the envelope `{tool, version, status, result{geojson, metrics}, confidence, method, assumptions, limits, sources, steps}`)
pick_candidates · find_all (exhaustive in a place; dense where built; soft vs hard negatives) · refine (re-scores the
find_all objects; keeps dense outlines) · outline · compare_years · filter (distance, inside layer, travel time, area,
confidence) · measure (area, distance, lidar height/volume) · export (csv/geojson/pdf) · find_place · isochrone ·
find_buildings (+min height) · canopy · find_anything (open vocabulary) · search_text · find_similar · add_layer.

## Data, caches, rebuilds (rebuilds always write a NEW version; switch `conf/data_versions.json` on a branch)
| data | built by | where it runs |
|---|---|---|
| chip index 2022/2020 (DINOv2 8×8 cells PCA-192, OpenCLIP) | `scripts/precompute_county.py` | PC GPU |
| local NAIP COGs (2022 county, 2020 for 8 places) | `scripts/fetch_cogs.py` | PC (network) |
| dense proposals (8 places) | `scripts/dense_build.py` | PC GPU |
| lidar DTM/DSM/nDSM (Uptown, Ballantyne, Myers Park, CLT Airport) | `scripts/build_lidar.py` | laptop |
| vector tiles | `scripts/build_pmtiles.py` | PC/laptop |
| Valhalla graph | `scripts/build_routing.py` (gt-routing) | PC/laptop |
| open-vocab answers | first query (disk cache) | PC GPU |
| inventory (Phase 7) | `scripts/build_inventory.py` via `scripts/heavy/jobs/inventory.sh` | laptop (not merged) |

## Machines + access rules
- **PC (`oryn`)**: API, UI, label server, GPU work (one GPU job at a time). Runs ORYN too: never restart WSL/Docker,
  never change `.wslconfig`, never touch ORYN.
- **Laptop (`quantcompute`)**: all heavy CPU builds; SSH as `ashur` over Tailscale with the existing key (never
  printed), then `wsl -d Ubuntu-24.04`. Smart App Control stays on; no Windows settings changes.
- **Mac mini**: hands off.

## Hard rules
No secrets in logs/commits/chat · permissive licences only (`docs/LICENCE_GATE.md`; Grounding DINO + Florence-2:
weights Apache/MIT, training data partly research-only, legal review before ORYN commercial use) · no people data ·
no Google data · never write `data/labels/` · no Funnel, no force-push · honest status.

## Evaluation (real runs; details `docs/EVALUATION.md`)
| feature | result |
|---|---|
| Pools, Ballantyne, demo pick (OSM pools found) | standard 16/28 → **dense 19/28**; backyard-only (≤ 150 m²) 8/16 → **11/16**; 373 objects |
| Pools, precision by eye | top 50 ~49/50; dense additions ~38/50 (Ballantyne); whole set est. ~87% |
| Pools, Lake Norman, your labels | standard 41/51 → **dense 46/51**, 0 look-alikes counted |
| Pools, evaluate.py (county) | search_text P@24 0.58 · find_all LOO P@24 0.49 · look-alike rejection 2/5 · OSM recall Ballantyne 14/25 (eval set) · outline IoU 0.38 |
| Lidar height verifier | Ballantyne dense 561 → 442 objects at equal recall (roofs removed) |
| New since 2020 (Ballantyne) | 17 "new": ~9 clearly new, ~6 ambiguous at chip scale, 1 building, 1 duplicate |
| Boat docks (open vocab) | 12/12 by eye |
| Open vocabulary, 9 concepts | 53/108 by eye (boat dock 12, school bus lot 11, golf green 6, baseball 6, crane 6, tennis 5, water tower 3, basketball 3, cemetery 1) |
| Building height | lidar vs Overture on 1,652 Uptown buildings: median diff 3.9 m, r 0.73; BofA Corporate Center 253 m (p90) vs 265 m published |
| Drive time | Valhalla free-flow on OSM roads: not validated against a travel-time reference |
| Planner | plan accuracy 35/35, declines 49/49 (100%) on `tests/planner/phrases.json` |

## Known weaknesses
1. Open vocabulary is weak for most concepts (< 7/12) and slow on a first query (~2 min).
2. Solar and parking stay EXPERIMENTAL (solar ~0/12; parking under-counts large lots).
3. Dense mode is off in Uptown (tall-building shadows; ~7/50 right there).
4. "New since 2020" mixes true new pools with ambiguous ones; there is no human label set for change.
5. First-compute latency for uncached requests: county-wide scan ~26 s, houses within 10 min ~5.5 s, Uptown heights
   ~2.6 s. The demo chain is pre-warmed (each step < 1 s).

## Post-checkpoint queue (on branches; never on demo-final)
Phase 7 inventory (built on the laptop; merge only after its eye-check) · Phase 8 Florence-2 check (PAUSED for PC
memory) · Phase 9 embedding bake-off (laptop) · Phase 10 deck.gl rendering · Phase 11 MCP server (`mcp_server/`) ·
Phase 12 local LLM planner (llama.cpp, Qwen3-4B).
