# Write-up material (for Lucas)

Every number here comes from a real run logged in `docs/BUILD_LOG.md`, `docs/EVALUATION.md` or `data/out/`. Items marked
**PENDING** are not measured yet; don't use them.

## 150-word summary
Groundtruth lets anyone teach a map what to look for. You type "swimming pools"; because words are ambiguous from above,
Groundtruth shows six image tiles, each a different meaning: backyard pool, community pool, stormwater pond, blue tarp, lake.
You pick what you meant and mark "not this". It then finds every match across Mecklenburg County's 0.6 m USDA NAIP imagery,
outlines each one with SAM 2, counts them and measures their area. As you accept or reject results it re-learns live
(about 0.1 s per judgement after the first), and every answer says how it was computed, how confident it is and what it cannot know.
Context layers (I-485, light rail, FEMA flood zones) and "new since 2020" turn results into decisions. The engine is a typed
API with one honest response envelope, so an AI agent (via MCP) can drive the same tools a person does.

## How it's built
- **Imagery:** USDA NAIP 2022 and 2020, 0.6 m, public domain, via Microsoft Planetary Computer. The county is cut into
  107,770 overlapping 224 px chips per year on a fixed EPSG:32617 grid.
- **Vectors:**
  - DINOv2 ViT-B/14 (Apache-2.0) patch tokens, pooled to 8×8 cells of 16.8 m and compressed to 192-d with a fixed PCA basis
    (91.9% variance), for "find more like this".
  - OpenCLIP ViT-B-16 laion2b (MIT), for text.
  - FAISS (MIT) fp16 indexes.
- **Pick what you meant:**
  - The query is expanded into sense prompts (config, not code).
  - Each candidate goes to its best sense; tiles are chosen for visual diversity on DINOv2 cells.
  - The object cell is localised with OpenCLIP crops.
- **Small objects:** the top 400 are re-ranked with full-resolution DINOv2 patches (8.4 m) plus a per-concept colour cue
  (config).
- **Live learning:** a per-session logistic regression on cell vectors. Each result explains itself via its nearest accepted
  example.
- **Outlines:** SAM 2.1 hiera-tiny (Apache-2.0), prompted by the matching cell, lazy and cached. Areas are measured in
  EPSG:32617.
- **Context:** OpenStreetMap (ODbL), FEMA NFHL (public domain), Overture places/buildings (CDLA-P-2.0 / ODbL), US Census
  boundary.
- **Licences:** MIT/BSD/Apache dependencies only; no non-commercial models; no data about people. Full list in
  `docs/SOURCES.md`.
- **Contract:** every tool returns one envelope: status, confidence, method, assumptions, limits, sources, timed steps.
  Versioned (`contract-v0.3`) with fixtures.

## Checkpoint numbers (2026-11-08, `demo-final`; supersede older numbers below where they differ)
- **Count, not a top-k:** "find all" in a place scores every chip and cell (Ballantyne: 1,318 chips / 84,352 cells), then
  dense mode re-checks every pool-coloured blob in a window centred on it. Ballantyne: **373 pools**.
- **Recall gains (real runs):** Ballantyne OSM pools found 16/28 → **19/28** with dense; backyard-only 8/16 → **11/16**;
  Lake Norman hand labels 41/51 → **46/51**, no look-alikes counted.
- **Precision by eye:** top 50 ~49/50; objects added by dense ~38/50; whole Ballantyne set estimated ~87%.
- **Heights:** USGS 3DEP lidar (public domain) gives height above ground at 1 m: a pool must sit at ground level, which
  removed 119 roof false positives (561 → 442) at equal recall. Uptown building heights: median 3.9 m from Overture's,
  Bank of America Corporate Center 253 m (published 265 m incl. spire).
- **Find anything:** Grounding DINO + SAM 2.1 for concepts never configured; boat docks 12/12 and school bus lots 11/12
  by eye; most other concepts are labelled low confidence (< 7/12).
- **Drive time:** Valhalla (MIT) on OpenStreetMap roads: "houses within 10 minutes of Ardrey Kell High School" →
  13,166 houses.
- **Typed demo chain:** 10 typed requests + 3 refines, each < 1 s warm (p95 0.97 s for the drive-time step);
  declines 49/49 on the planner test set.
- **Fully local:** imagery tiles from local NAIP files: p95 **1,172 ms → 88 ms**; the whole demo passes with Planetary
  Computer blocked.

## Evaluation numbers
- Slice measurements (real runs). Pools in the top 24 whose matched cell is on a pool, counted by eye, south-Charlotte
  residential slice:

  | case | before re-rank | after re-rank |
  |---|---|---|
  | find-similar from 1 backyard pool | 5–6 | ~18–20 |
  | 3 backyard pools | 4–5 | ~14–16 |
  | community pool | 12–13 | ~17–18 |

- Pick-what-you-meant: on both slices the 6 tiles cover ≥4 distinct meanings, including stormwater ponds.
- Sub-crop A/B (rejected): adding 2×2 CLIP sub-crops cost 2.6× embedding time and added 0 clear pools (6 vs 6 of 24).
- Latency (warm, this PC's RTX 4060 for query-time work):

  | step | time |
  |---|---|
  | pick | 0.8–0.9 s |
  | find-all | ~2.1–2.5 s (slice) |
  | refine | 45–110 ms |
  | filter | 55–76 ms |
  | measure | 0.65 s |
  | PDF | 0.2 s |

- **County (all of Mecklenburg, NAIP 2022, 107,770 chips):**
  - Re-rank from 3 backyard pools, top 24 by eye: Ballantyne ~5–7 → ~21 pools, Lake Norman ~19–21.
  - Hero flow (find pools → pick backyard → find all in Ballantyne → refine ×3 → within 1 km of I-485 → CSV), warm:
    pick 0.6–0.7 s, find-all 0.7–0.8 s, refine < 0.5 s. 27 detected, 87 within 1 km of I-485.
  - Evaluation vs hand labels + OSM (`docs/EVALUATION.md`; lower bounds; **51 of 59 pool labels are in one Lake Norman
    stretch**):

    | pools | result |
    |---|---|
    | search_text precision@24 | 0.58 |
    | find-all leave-one-out precision@24 | 0.45 |
    | look-alikes rejected | 3 of 5 |
    | OSM-mapped pools found in Ballantyne (top 200) | 16 of 25 |
    | outline IoU vs OSM (median) | 0.29 (outlines often include the pool deck) |

## What's novel
1. **Pick what you meant.** Instead of guessing, it shows the ambiguity as six visual senses with "not this". The negatives
   shape the search.
2. **Live learning in the loop.** Each ✓/✗ retrains a per-session model (45–110 ms measured after the first call) and every
   result says why it's there.
3. **The honest envelope.** Every answer carries method, confidence, limits and sources, e.g. "count = detected objects in
   ranked results, not a census"; replays are labelled as replays.
4. **Agent-ready.** The same tools are exposed through MCP and a map-action schema, so an agent can fly to Ballantyne, find
   pools within 1 km of I-485 and export a CSV, citing the envelopes it used.

## Screenshots (docs/media/, real runs on the south-Charlotte slice)
1. `01_ask.png`: the ask screen with example chips.
2. `02_pick.png`: "Which swimming pools do you mean?" (six senses with "not this").
3. `03_results.png`: results with SAM outlines, count/acres, the honest limits, and the work log.
4. `04_refine_or_layers.png`: live refine plus context layers.
5. `05_worklog.png`: every tool call's steps with milliseconds, confidence, method and limits.

Extras: `06_agent_mock.png` (labelled MOCK), `07_results_1280.png`, `08_new_since_2020.png`.

## 90-second demo script
1. (0–10 s) "Aerial imagery of all of Mecklenburg County, 0.6 m. What do you want to find?" Type *swimming pools*.
2. (10–25 s) Six tiles appear: backyard pool, community pool, stormwater pond, blue tarp, lake. "Words are ambiguous from
   above, so it asks." Pick **backyard pool**, mark the pond **not this**, press *Find all*, place **Ballantyne**.
3. (25–45 s) Results stream in with outlines; the count and acres climb. Open the work log: steps with milliseconds,
   confidence, and the limit line "count = detected objects in ranked results, not a census".
4. (45–60 s) Reject a hot tub, accept two pools; the list re-ranks instantly and each result shows "nearest accepted".
5. (60–75 s) Turn on I-485 and flood zones; filter **within 1 km of I-485**; export CSV/PDF.
6. (75–90 s) Toggle **new since 2020**: the new pools light up. Close: "The same tools are callable by an AI agent through
   MCP; every number on screen is traceable to an envelope."

## Prior work disclosure
Monish also builds ORYN, a geospatial platform. Groundtruth was built new at CrownHacks and reuses only pre-existing data
caches and helper material listed in `docs/PRIOR_WORK.md` (an OSM extract, shared DINOv2 weights and design tokens). No ORYN
product code was copied.
