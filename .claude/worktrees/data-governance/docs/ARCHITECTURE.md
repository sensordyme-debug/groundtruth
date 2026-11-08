# Architecture

```
            ┌──────────── web/ (thin demo) ────────────┐     ┌── web/label (labelling page) ──┐
            │ Vite + React + TS + MapLibre GL           │     │ static HTML + MapLibre         │
            └───────────────┬───────────────────────────┘     └──────────────┬─────────────────┘
                            │ JSON envelopes                                 │
            ┌───────────────▼──────────────┐                 ┌───────────────▼────────────────┐
            │ api/app.py (FastAPI, :8000)  │                 │ api/label_server.py (:8765)    │
            │ one route per engine tool    │                 │ long-lived, never auto-reloads │
            └───────────────┬──────────────┘                 └───────────────┬────────────────┘
                            │                                                │
   ┌────────────────────────▼─────────────── engine/groundtruth ─────────────▼─────────────────┐
   │ tools: search_text · pick_candidates · find_similar · outline · refine · add_layer ·       │
   │        filter · measure · export          → every tool returns envelope.Envelope           │
   │ tiler (chips) · embed (DINOv2, OpenCLIP) · index (FAISS cosine) · geo (4326↔32617)          │
   │ labels.LabelStore (journal + atomic snapshot + backups)                                    │
   │                 ▲ all data access through                                                  │
   │ providers.base.DataProvider ── PublicDataProvider (Census, NAIP/PC, OSM, FEMA, Overture)    │
   │                             └─ (later) OrynDataProvider                                    │
   └───────────────────────────────────────────────────────────────────────────────────────────┘
```

## Principles
- **Engine = product.** API and UI are adapters. The engine has no web framework imports.
- **Providers isolate I/O.** Engine logic receives a `DataProvider`; tests use a fake provider.
- **Envelope everywhere** (`docs/CONTRACT.md`) so the UI and the future agent consume one shape.
- **Precompute once, query fast.** Chips + embeddings + FAISS indices are built offline
  (`scripts/precompute_county.py`); queries are vector lookups (milliseconds).
- **Deterministic & versioned.** Seeds fixed, ties broken by `chip_id`, each tool and index carries a version.

## Data flow (search)
1. Tiler cuts the county into 224×224 px chips at 0.6 m (≈134 m) with overlap; each chip records
   `chip_id, bbox, year, scene_id`.
2. `embed` computes DINOv2 (image↔image) and OpenCLIP (text↔image) vectors, L2-normalised.
3. FAISS inner-product index per embedding type (= cosine).
4. `search_text` embeds the query with the CLIP text tower → top-k chips.
5. `pick_candidates` clusters the top 200 into 6 groups (k-means on DINOv2 vectors), one representative each,
   so the user can say which meaning they intended.
6. `find_similar` uses DINOv2: mean(positives) − λ·mean(negatives) → top-k, excluding already-judged chips.

## Model cascade (master build queue, v0.5)
```
 text ──► senses.json concept? ──yes──► pick_candidates (OpenCLIP senses, colour-checked tiles)
   │                                     └► find_all: DINOv2 cells → colour prefilter → DINOv2 patches + concept colour
   │                                          → components → dedupe → SAM 2.1 outline → object check (area/compact/colour)
   │                                          └► DENSE (8 places): colour blobs in centred windows → DINOv2 token + SAM 2.1
   │                                               + same verifier (precomputed) → matched to examples → merged
   ├─ place category? ──► find_place (Overture places, DuckDB spatial)
   ├─ building kind? ───► find_buildings (Overture footprints, DuckDB) + heights (lidar nDSM ▸ Overture)
   └─ else ─────────────► find_anything: OpenCLIP chip ranking → Grounding DINO boxes → CLIP crop verifier
                              → SAM 2.1 mask (clipped to box) → area check → dedupe
 constraints: filter (distance to layers · inside layers · travel time via Valhalla isochrones · min height)
```

## Environments (each heavy dependency pinned in its own env when it could conflict)
| env | path | what | used by |
|---|---|---|---|
| CPU main | `~/.venvs/groundtruth` | engine + API (torch CPU), duckdb 2.0, pmtiles, titiler.core/rio-tiler, pyvalhalla, laspy+lazrs | tests, scripts, lidar build |
| GPU main | `~/.venvs/groundtruth-gpu` | same + torch cu126 | API (`GROUNDTRUTH_GPU=1`), precompute, dense, open-vocab pre-test |
| routing | `~/.venvs/gt-routing` | pyvalhalla 3.9 + pyosmium (graph build only) | `scripts/build_routing.py` |
| browser | `~/.venvs/gt-browser` | Playwright (headless Chromium) | `scripts/ui_bench.py` |
| system | apt | tippecanoe 2.49 | `scripts/build_pmtiles.py` |

## Local data (all under `data/`, gitignored; rebuilt by scripts)
| path | built by | notes |
|---|---|---|
| `~/.cache/groundtruth/naip/<year>/` | `scripts/fetch_cogs.py` | NAIP COGs: 2022 county (56 scenes, 23.9 GB), 2020 for the 8 places (43, 20.5 GB) |
| `data/tiles/*.pmtiles` | `scripts/build_pmtiles.py` | vector tiles for context layers (`conf/vector_tiles.json`) |
| `data/routing/` | `scripts/build_routing.py` | OSM roads clipped to county + 5 km, Valhalla tiles (121 MB) |
| `data/dense/<place>/` | `scripts/dense_build.py` | dense proposals (tokens, outlines, verifier) |
| `data/lidar/<place>/` | `scripts/build_lidar.py` | DTM / DSM / nDSM at 1 m (3DEP 2016) |
