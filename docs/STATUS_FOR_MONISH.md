# Status for Monish

## Try it now (branch `redesign/conversational`, API on the PC)
- **http://localhost:8000/** is the new conversational app. Click **Houses with pools**, then the pills **in Lake Norman** →
  **with lake and dock access** → **which got docks since 2020**. Click a thumbnail to open the detail sheet. Drag the
  2020/2022 slider. Try **Not this**, then ⋯ → **Export CSV**.
- **New search (+)** → **Swimming pools**. Pick a tile, **Continue**, then **in Lake Norman**.
- Type **who owns these houses**: it is declined in the chat.
- The old UI is at **http://localhost:8000/classic**.
- Check (WSL, worktree root): `~/.venvs/gt-browser/bin/python scripts/demo_check_v2.py`. Compare screenshots: `bash scripts/compare_shots.sh`.

## CHECKPOINT A (MEGA BUILD Phase 1): waiting for your look at the compare screenshots
- PHASE 1: done (Phase 6 not started; it waits for your approval)
- **Try it:** see above.
- **Numbers:**
  - demo_check v2: 31/31 at 1440×900 and at 1920×1080.
  - Chain, warm, per message (time until the reply is on screen):

    | message | time |
    |---|---|
    | houses with pools | 281 ms |
    | in Lake Norman | 186 ms |
    | with lake and dock access | 71 ms |
    | docks since 2020 | 15 ms |
    | decline | 150 ms |
    | picked pools in Lake Norman | 404 ms |

  - Lake Norman (real, derived v1): 946 houses with a pool → 63 lakefront with their own dock → none of their 69 docks is new since 2020.
  - "Not this" gives 63 → 62, and the CSV has 62 rows.
- **Files:**
  - `docs/media/v2/compare/<state>_{mockup,build}_<size>.png`. States: landing, transition, step1–4, sheet, pick.
  - The differences are listed in `docs/media/v2/compare/DIFF.md`: 6 fixed, 10 remaining. The remaining ones are real imagery or real numbers, or the data does not support them yet.
  - `data/out/dock_change_sheet.jpg` is the 2020/2022 dock contact sheet.
- **Broken / still wrong:**
  - Step 4 has nothing new to show, because no dock is new since 2020. The mockup's "31 docks added" is not true for these houses.
  - Eye-check precision for the v1 derived concepts is not re-measured yet (Phase 2.3).
  - Windows free RAM is 2.6 GB with the API warm (rule: ≥ 4 GB). This is Phase 4.3, next after Phase 2.
  - The 90 s recording is not made yet.
- **Decisions I made:**
  - "Docks new since 2020" is a build-time imagery check: the dock footprint is deck in 2022 and water in 2020. Thresholds are in `conf/derived.json`. I eye-checked the 24 most change-like docks: all existed in 2020, so the answer is "none".
  - The LLM does not rewrite the hero-chain replies or the questions back to the user, because it garbled them ("Look for areas…"). Other replies are still rewritten.
  - "Not this" re-runs the request behind that card without the house, so the count is a real recount, not a client-side subtraction.
  - Imagery loads only after the first question. Local tiles up to z15 are disk-cached (`data/cache/tiles/local/v1`), and the demo views are pre-rendered (`scripts/prerender_tiles.py`).
  - Step 2+ flies to the densest cluster at z14.3, so the rings show as in the design. Zoom out to see all the dots.
- **Needs me:** look at the compare screenshots (approve Phase 1, or list what to change) before Phase 6.
- **Free RAM:** PC Windows 2.6 GB, WSL 14 GB available; laptop 41 GB available; VRAM 4.7 / 8.2 GB.


## Competition-ready checkpoint (2026-11-08): see docs/HANDOFF.md for the full picture
- Feature work is STOPPED at `demo-final`. demo_check passes cold, warm ×2 and offline; the typed chain runs < 1 s per
  step warm; declines 49/49; Windows free with the API warm 4.9 GB; manifest + rollback tested.
- Decisions closed by you: PC .wslconfig unchanged (memory fixed in code); PDAL → laspy + lazrs; Grounding DINO +
  Florence-2 accepted with the legal-review note; Phase 8 paused; Phase 7 inventory not merged (still to eye-check).
- Background: the Phase 7 inventory job on the laptop (does not touch the demo).

## Earlier phase reports (master build queue)


## Try it now
- **Demo UI:** http://localhost:8000/. The landing chips are the four measured pool requests (rules planner); solar and parking stay typeable and are marked EXPERIMENTAL.
- **New planner phrases** (agent tab, or `POST /api/v1/agent/run {"text": ...}`):
  - "houses within 10 minutes of Ardrey Kell High School"
  - "restaurants near the light rail"
  - "parking lots within 10 minutes of the airport"
  - "buildings taller than 30 m in Uptown"
  - "how tall are the trees in Myers Park"
  - "tennis courts in Ballantyne" (open vocabulary)
- **Checks (WSL, repo root):**
  - `bash scripts/demo_check.sh`
  - `bash scripts/gt.sh python scripts/planner_check.py "<phrase>" …`
  - `bash scripts/restart_and_warm.sh`

## Phase reports

### PHASE 0: done (licence gate)
- **Try it:** `docs/LICENCE_GATE.md`. A summary is appended to `docs/SOURCES.md`.
- **PASS:**
  - Tools: TiTiler / rio-tiler, PDAL, Valhalla, OSRM, deck.gl, PMTiles, tippecanoe, Planetiler, Martin, TorchGeo code, MCP SDK, llama.cpp.
  - Data: USGS 3DEP.
  - Weights: SAM 2.1, Clay v1.5, Qwen3 1.7B/4B/8B, Phi-3.5/4-mini, Mistral-7B-v0.3.
  - Overture places.
- **PASS with a note:**
  - Grounding DINO and Florence-2: permissive weights, research-only datasets in their training lineage.
  - DuckDB: bundles GEOS, which is LGPL.
  - ODbL share-alike: Overture buildings, Geofabrik.
- **UNCLEAR, so not used:** GeoAI's solar, parking and car weights; Satlas, DOFA and Panopticon weights.
- **FAIL:**
  - DINOv3 (custom licence).
  - OWLv2 (trained by Google on WebLI).
  - SAM 3, Qwen2.5-3B, Llama, Gemma.
  - CC-BY-NC TorchGeo weights.

### PHASE 1: done (1.1–1.4)
- **1.1 Local NAIP COGs + TiTiler engine:**
  - Disk: both years county-wide would be 51 GB, leaving less than 50 GB spare on C:. So I cached 2022 for the whole county (56 scenes, 23.9 GB) plus 2020 for the 8 places (43 scenes, 20.5 GB).
  - Tiles are rendered from the local COGs by rio-tiler (TiTiler's engine) inside the API. The source is reported in an `X-Tile-Source` header. Planetary Computer is the fallback only. `GROUNDTRUTH_OFFLINE=1` blocks it, for the unplugged test.
  - Tile p95, before (Planetary Computer): **1,172 ms** (p50 944 ms; 120 Ballantyne tiles, z14–17). After (local COGs): see the next update. I have not yet benchmarked after the download finished.
- **1.2 PMTiles:**
  - Built with tippecanoe: 11 layers, about 75 MB. Served at `/api/v1/vt/{layer}/{z}/{x}/{y}.pbf` in 4–6 ms per tile.
  - UI (`scripts/ui_bench.py`, median of 3, Ballantyne): before, all GeoJSON, 1,497 ms to idle, 2.12 MB of layer bytes, renderer 250 MB.
  - A FEMA-on-tiles build measured 1,092 ms, 71 kB and 204 MB. The FEMA tiles are now rebuilt from the filtered layer (1% + 0.2% zones). I still need to re-run the bench on that build.
  - New toggles: Overture buildings and places.
- **1.3 DuckDB spatial (Overture), same results:**

| query | naive pyarrow+shapely | DuckDB |
|---|---|---|
| Ballantyne buildings | 1,307 ms | 57 ms |
| Uptown buildings | 1,556 ms | 26 ms |
| Uptown restaurants | 90 ms | 3.6 ms |
| county schools | 70 ms | 7.8 ms |
| name search | 85 ms | 7.4 ms |

- **1.4 Demo scope:**
  - Landing chips are the 4 pool requests. Solar and parking carry an `EXPERIMENTAL` limit.
  - The warm-up covers pool find_all for all 8 places plus the 4 chips. It yields while real requests are active.
  - Landing chips, warm: 77–163 ms, Matthews 1.4 s.
  - Tagged `demo-stable-v2`.

### PHASE 2: partial (dense mode built for Ballantyne; the other 7 places are building)
- **Method:** every pool-colour blob in the place goes into a 224 px window centred on it, so chip edges never cut an object. Each window gets a DINOv2 token, a SAM 2.1 outline and the same verifier as standard mode. At query time, proposals are matched to the picked examples ("not this" negatives applied) and merged with the standard scan. `find_all` metrics say `dense: true`.
- **Ballantyne build:** 675 proposals, 323 pass the strict verifier, 2.2 min on the GPU.
- **A/B, OSM recall in Ballantyne** (`scripts/dense_ab.py`, `data/out/dense_ab_Ballantyne*.json`):

| examples | standard | dense, strict verifier | dense, adopted |
|---|---|---|---|
| evaluate.py set (3 typical OSM pools) | **15/25** (273 objects) | 18/25 (359) | **20/25** (583) |
| demo pick (backyard + negatives) | 10/28 (230) | 12/28 (263) | 12/28 (308) |

- **Adopted dense setting:** match without the patch-colour rule, and a relaxed dense verifier (compactness ≥ 0.2, colour fraction ≥ 0.3).
- **Eye precision, demo pick:**
  - Dense top 50: **~49/50** (`data/out/dense_Ballantyne_top50_dense.png`).
  - The 78 objects dense adds: ~41/50 right (`dense_Ballantyne_added.png`). The misses are roof shadows, tennis-court corners and dark spots in parking lots.
  - Strict-verifier additions: ~30/33.
  - Estimated precision over the whole count: ~91%.
- **Target:** Ballantyne recall ≥ 20/25 is met with the evaluation examples. Precision ≥ 44/50 is met on the top 50.
- **Still wrong:**
  - With the demo pick, recall stays 12/28: its "not this" negatives (community pool, tarp, pond) reject many matches.
  - 8 of the 28 OSM pools have no colour blob at all: they are covered, shaded, dark or green, or gone since OSM mapped them.
- **Still to do:** Lake Norman recall on your labels (`scripts/dense_labels.py`), and the UI "dense" badge.

### PHASE 3: skipped (licence) except buildings
- GeoAI's solar, parking and car weights are UNCLEAR: their training data isn't documented, and the car model is trained on 7 cm imagery. Following your rule, they aren't used.
- Solar and parking stay EXPERIMENTAL. Their next route is Phase 4 (open vocabulary) or Phase 7 (inventory).
- Buildings come from Overture footprints (DuckDB) plus lidar heights. GeoAI's BuildingFootprintExtractor is not installed yet.

### PHASE 4: done (open vocabulary)
- **Pipeline:**
  1. OpenCLIP ranks chips (county top 500, or up to 500 in a place).
  2. Grounding DINO tiny boxes the concept.
  3. A CLIP crop verifier (adopted) checks each box.
  4. SAM 2.1 masks each box, clipped to the box (adopted), then an area check.
  5. Dedupe.
- **A/B, eye-checked right out of 12:** baseline (`data/out/openvocab_*_base.png`) vs adopted (`*_B.png`). Total on the 9 re-run concepts: 48/108 → 53/108.

| concept | baseline | adopted |
|---|---|---|
| boat dock | 12 | 12 |
| school bus lot | 9 | 11 |
| golf green | 6 | 6 |
| baseball diamond | 6 | 6 |
| construction crane | 4 | 6 |
| tennis court | 4 | 5 |
| water tower | 4 | 3 |
| basketball court | 2 | 3 |
| cemetery | 1 | 1 |
| trampoline | 0 | not re-run (below 0.6 m resolution) |

- **Low confidence:** concepts below 7/12 are labelled "LOW CONFIDENCE" in limits and in the planner's answer. That is everything except boat dock and school bus lot.
- **Cost:** about 2 min per county-wide query on the GPU. Not interactive.
- The agent routes unknown concepts here automatically.

### PHASE 5: done (places + drive time)
- **Data and tools:**
  - Overture places through DuckDB, with a category map in `conf/places_categories.json`.
  - Valhalla (pyvalhalla 3.9, MIT) graph from the NC OSM extract clipped to the county + 5 km: 324k road segments, 121 MB of tiles, built in 4 min.
  - New tools: `find_place`, `isochrone`, `find_buildings`. `filter.within_minutes_of`.
- **Phrases:**

| phrase | result | time |
|---|---|---|
| houses within 10 minutes of Ardrey Kell High School | 13,166 | 12 s |
| restaurants near the light rail | 769 | 0.4 s |
| parking lots within 10 minutes of the airport | 8 (experimental concept) | — |
| pools within a 5 minute walk of a park | 3 (county-wide ranked mode) | — |
| schools in Matthews | 71 | — |

- **Declines:** people-data requests are declined, including "who owns …".
- **Limits:** "houses" means Overture house-type classes. Only 35% of buildings have a class, and this is stated in limits.

### PHASE 6: partial (lidar)
- **Rasters:** DTM, DSM and nDSM at 1 m from USGS 3DEP `NC_Phase4_Mecklenburg_2016`, read straight from the public EPT with laspy + lazrs. PDAL isn't packaged for Ubuntu 24.04, and its install was blocked (see Decisions needed).
- **Built so far:** Uptown (52M points), Ballantyne, Myers Park (115M). The airport area is building now.
- **Wired in:**
  - `measure` adds `height_m` and `volume_m3`.
  - `find_buildings` takes `min_height_m` and uses lidar heights.
  - New `canopy` tool.
  - Planner phrases: "buildings taller than 30 m in Uptown" (106 buildings, using Overture heights before the lidar landed), "how tall are the trees in Myers Park", "warehouses over 10 m tall near the airport".
- **Acceptance:**
  - Lidar vs Overture height on 1,652 Uptown buildings: median difference 3.9 m, correlation 0.73.
  - Bank of America Corporate Center: lidar 253 m (90th percentile) vs a published 265 m including the spire.
  - My coordinates for the other towers were rough, so those matches aren't reliable.
- **Still to do:** the pool verifier A/B with vs without height.

### PHASES 7–13: not started yet (in order next)

## Decisions I made
- **Licence lineage:** accepted for Grounding DINO and Florence-2. This is the same reading the repo already applies to SAM 2.1 and OpenCLIP.
- **Out:** OWLv2 (Google-trained), DINOv3, and the unclear GeoAI weights.
- **COG scope:** 2022 county + 2020 for the 8 places, because both years county-wide would leave less than 50 GB spare on C:.
- **TiTiler as an engine, not a server:** rio-tiler (TiTiler's tiling engine) runs inside the API rather than as a separate TiTiler server. It renders the same tiles with one service fewer and less RAM.
- **Lidar reader:** a laspy EPT reader instead of PDAL (see Decisions needed). Uses depth 9 (about 0.9 m) for places and depth 8 for the airport area.
- **Dense mode:** on automatically wherever a dense build exists, with the relaxed dense verifier.
- **"Near <a named place>":** read as within a 5-minute drive. "Near <a layer>" means within 500 m.
- **Open vocabulary:** capped at 500 chips in a place to bound latency.
- **duckdb:** I briefly downgraded it by mistake while installing and restored 2.0.0 right away.

## Decisions needed (max 3)
1. **PDAL:** Ubuntu 24.04 has no package, and my micromamba install (downloads a binary, then conda-forge PDAL into `~/.venvs/gt-pdal`) was blocked by the permission check. The lidar works without it (laspy + lazrs). If you want PDAL itself, run `scripts/install_lidar.sh`'s earlier micromamba version yourself, or allow it.
2. **Licence lineage:** do you accept Grounding DINO and Florence-2? If not, open vocabulary falls back to CLIP only.
3. **GeoAI solar, parking and car weights** have undocumented training data. Should I test them as internal-only baselines, or keep skipping them (current choice)?

## Free RAM / VRAM / disk
See the latest row in `docs/BUILD_LOG.md`.
