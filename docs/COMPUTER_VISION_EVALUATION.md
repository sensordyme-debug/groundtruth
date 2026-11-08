# Computer vision evaluation

This page gathers the CV numbers Groundtruth has **measured** so far and the method for the new human-reviewed
evaluation (the Validation Studio, `docs/VALIDATION_STUDIO.md`). Every number below is copied from:
- `docs/EVALUATION.md`;
- `data/out/evaluation.json` (generated 2026-11-08T01:03);
- `data/out/eval_point_find.json` (2026-11-08 06:02).

Nothing is re-estimated or invented here. A number that was not measured says so.

Pipeline under test:
- **Index** `chips224s192-g0.6-v3c8p192`: NAIP 2022 0.6 m, 107,770 chips. It holds DINOv2 ViT-B/14 8×8 cells
  (PCA-192) and OpenCLIP ViT-B-16 (laion2b_s34b_b88k).
- **Outlines:** SAM 2.1 hiera-tiny.
- **Open vocabulary:** Grounding DINO tiny, followed by a CLIP crop verifier.
- **Footprints:** Overture buildings.
- **Data versions:** dense `v1`, lidar 3DEP 2016.

## 1. What is measured today (before human review)

### Reference-based scores (lower bounds)
These come from `docs/EVALUATION.md` and `data/out/evaluation.json`, with k = 24. A hit counts if its 16.8 m cell
holds a hand-labelled true match or touches an OSM-mapped object. OSM misses most backyard pools, so precision is a
**lower bound**. **Bias:** 51 of the 59 pool labels lie in one 1.7 km stretch of the Lake Norman waterfront.

| concept | labels T/L | search_text P@24 (labels+OSM) | find_all leave-one-out P@24 (labels+OSM / labels) | look-alike rejection | OSM recall, top 200 in place | outline IoU vs OSM (median, n) |
|---|---|---|---|---|---|---|
| swimming_pool | 52/7 | 0.583 | 0.492 / 0.485 (20 queries) | 2/5 (0.4) | Ballantyne 14/25 (0.56) | 0.379 (14) |
| parking_lot | 7/0 | 0.292 | 0.512 / 0.018 (7 queries) | – | Uptown 337/407 (0.828) | 0.095 (30) |
| warehouse | 0/0 | 0.25 | – | – | – | – |
| construction_site | 0/0 | 0.125 | – | – | – | – |
| solar_installation | 0/0 | 0.0 | – | – | – | – |

### Checkpoint pool numbers (`docs/EVALUATION.md`, checkpoint 2026-11-08)
| set | standard | dense |
|---|---|---|
| Ballantyne demo pick, OSM pools found | 16/28 | 19/28 |
| Ballantyne backyard-only (OSM pools ≤ 150 m²) | 8/16 | 11/16 |
| Lake Norman, hand labels found / look-alikes counted | 41/51 · 0 | 46/51 · 0 |
| Eye precision: dense top 50 / dense additions | — | ~49/50 / ~38/50 (Uptown additions ~7/50, so dense is off in Uptown) |

Open vocabulary, by eye on the top 12 (county CLIP top 500), from baseline to the adopted verifier: boat dock 12 → 12,
school bus lot 9 → 11, golf green 6 → 6, baseball diamond 6 → 6, construction crane 4 → 6, tennis court 4 → 5, water
tower 4 → 3, basketball court 2 → 3, cemetery 1 → 1, trampoline 0. Total 48 → 53/108.

Lidar vs Overture building height (Uptown, 1,652 buildings): median |Δ| 3.9 m, r 0.73.

### "Find like this" (`data/out/eval_point_find.json`, k = 24)
Setup: one click on an OSM-mapped example. Precision is measured against OSM, so it is a lower bound.

| type | place (OSM objects) | raw: count / P@24 | calibrated: count / P@24 | 3 refines, calibrated: count / P | eye, calibrated top 24 (EVALUATION.md) |
|---|---|---|---|---|---|
| swimming pool | Ballantyne (28) | 137 / 0.458 | 51 / 0.417 | 15 / 0.733 | 23/24 |
| warehouse | Steele Creek (34) | 432 / 0.417 | 154 / 0.542 | 47 / 0.542 | ~22/24 |
| parking lot | Ballantyne (148) | 70 / 0.583 | 31 / 0.375 | 1 / 1.0 (collapses) | not eye-checked |
| solar installation | South End (12) | 6 / 0.833 | 6 / 0.833 | 2 / 1.0 | n = 6 |

The refine numbers use a simulated user who rejects results that OSM does not map. For pools, those are mostly real
backyard pools, so the refine numbers show that the mechanism works, not the quality a real user would get.

### What these numbers cannot tell us
- **True precision for backyard pools:** OSM is incomplete, and the hand labels are clustered at Lake Norman.
- **Precision for parking lots, docks and solar** outside a few eye checks of 12–50 items.
- **Precision in the south and centre of the county** measured independently of the Lake Norman cluster.
- **Calibration:** what a tool's confidence means as a probability. All confidences are heuristic.
- **Recall** against a complete reference. OSM-based recall only counts OSM-mapped objects.

## 2. New method: human-reviewed evaluation (Validation Studio)
Full design is in `docs/VALIDATION_STUDIO.md`. In short:

1. **Samples.** Real predictions from the running API (the same calls the app makes), stratified by (class, region).
   The cap is 20 per stratum for pool and building and 12 for parking, dock and solar, drawn with a fixed seed
   (20261108). Samples are frozen in `data/validation/v1/samples.jsonl` with sha256 checksums.
2. **Holdout by geography.**
   - The groups are north (Lake Norman, Cornelius, Huntersville), south (Ballantyne, Pineville, Matthews) and central
     (Myers Park, University City).
   - Regions are disjoint, and a 1 km buffer separates the groups.
   - No south or central sample lies in Lake Norman.
   - Samples inside the Lake Norman label cluster are flagged so they can be excluded.
3. **Human decisions only.** The decisions are accept, reject, uncertain, needs_review, or corrected (with a drawn
   polygon). The latest review wins and the history is kept. Reviewers are anonymous ids. The original labels in
   `data/labels/` are never touched (enforced by a test that hashes them).
4. **Metrics** (`GET /api/v1/validation/metrics`, `scripts/validation_metrics.py` → `data/out/validation_metrics.json`):
   - **Precision** = (accept + corrected) / (accept + corrected + reject), with a Wilson 95 % interval. Uncertain is
     never positive. Reported per class, region, holdout group and geometry kind (outline vs un-outlined box).
   - **Population-weighted precision.**
   - **False discovery rate.**
   - **IoU** of corrected outlines, measured in EPSG:32617.
   - **Score calibration table.**
   - **Recall**, only inside census tiles where a reviewer drew every real object. Otherwise it is reported
     explicitly as **"not measurable"**.
   - **False-positive rate** (FP/(FP+TN)) is not defined for detection review and is reported as such.

### Sample counts built (v1, 2026-11-08 07:25)
The set has **498 samples** (452 stratified + 46 census-tile predictions) and 6 census tiles. Region breakdown and
populations are in `docs/VALIDATION_STUDIO.md` and `data/validation/v1/manifest.json`.

| class | samples | north / south / central |
|---|---|---|
| pool | 190 | 71 / 69 / 50 |
| building | 176 | 66 / 66 / 44 |
| parking | 60 | 12 / 24 / 24 |
| solar | 60 | 12 / 24 / 24 |
| dock | 12 | 12 / – / – |

8 samples fall inside the Lake Norman label cluster: 3 pool, 1 building, 4 dock.

### Results
**0 human reviews so far.** Precision, IoU, calibration and recall from the Validation Studio are therefore not yet
available. The metrics endpoint returns `null` precision with `n_judged = 0` and `recall: "not measurable"` for every
class. No review has been entered by the engineer who built the tool; reviews must come from people using the Studio.


## 3. Find like this: refinement after the negatives change (2026-11-08, end of sprint)

`scripts/eval_point_find.py`, calibrated runs. "Not this" examples are now applied after the scan, result by result, instead of inside the scan:

| Type | 0 refines: count / OSM P@24 | 1 refine | 3 refines |
|---|---|---|---|
| parking lot | 31 / 0.375 | 24 / 0.417 | 17 / 0.471 |
| warehouse | 154 / 0.542 | 134 / 0.542 | 72 / 0.625 |
| swimming pool | 51 / 0.417 | 45 / 0.375 | 18 / 0.611 |

What changed:
- **Before** this change, a single "not this" collapsed parking to 1–3 results (and pools 137 → 3 with hard negatives).

What this does and doesn't show:
- **OSM is a lower bound.** The simulated user is an oracle that rejects the top results OSM doesn't map. For pools, many of those are real backyard pools, so the pool rows understate quality and the oracle itself is flawed.
- **These are not human-verified improvements.** The human-reviewed refine benchmark is pending Validation Studio reviews.


## 4. Solar (2026-11-08): root cause and rule-based verifier

**Status: EXPERIMENTAL.** No human reviews exist (Validation Studio solar: 60 samples, 0 reviewed). Every "precision"
below is an **engineer eye-check** of a contact sheet: it is not a human ground-truth review and not a measured precision.
Config: `engine/groundtruth/conf/concepts.json` → `solar_installation` (with `_doc`). Tests: `tests/test_solar.py`.

### Root cause (measured)
`find_all(concept=solar_installation)` with the sampler's sense (`rooftop_solar_res`) returned **34,178** objects in
Huntersville and **22,745** in Matthews. The planner's default sense (`rooftop_solar_comm`) returned 5,948 and 7,630.
1. **The colour rule detected shadows.** The old panel colour (H 190–250°, S ≥ 30, V ≤ 110: "dark blue") matched
   **0.0%** of the pixels of known NAIP 2022 rooftop/carport arrays (OSM polygons, outside the tuning area) and 0.8% of
   ground-farm pixels, but **7.0% of all pixels** (400 random Huntersville chips). It is anti-selective: in NAIP 2022,
   panels are mid-bright blue (rooftop, carport) or bright grey (farms), and dark bluish pixels are shadows. The scan's
   colour prefilter therefore kept 15,430 of 16,325 Huntersville chips. `pick_candidates` also used this colour, so the
   picked examples were themselves shadows: `rooftop_solar_res` = shaded forest canopy, `rooftop_solar_comm` = a building
   shadow on pavement, `solar_farm` = lake water, `solar_carport` = a dark road.
2. **99.4% of the count was never verified.** Only objects with a cached SAM outline went through the object check, and
   only the top 300 are outlined per call. In Huntersville, 33,981 of 34,178 objects were unverified 8.4 m patch boxes.
3. **This is not duplication.** The scan's 30 m NMS works. There were 76,102 raw patch groups, giving 34,290 objects
   (2.2 raw per object), with a median group of 1 patch. The nearest-neighbour distance between objects has a median
   of 37.6 m, and 42% of objects have a neighbour within 35 m. That pattern is a lattice of distinct shadow patches
   spaced just beyond the NMS radius, across continuous canopy.

**Before eye-check** (`data/out/solar_before.png`, 48 random Huntersville objects, `rooftop_solar_res`): **0/48 solar.**
Rough shares by eye:
- shaded forest canopy ~60%;
- tree shadows on lawns and yards ~23%;
- shadows at house and roof edges ~10%;
- roads or asphalt ~4%;
- a creek edge ~2%.

A second sheet of the planner-default sense also had 0/48 solar. There the main patterns were tree and building
shadows on lawns, roads and parking, then canopy.

### Fix (all in config; the rules are generic in `Engine.object_check`)
- **Panel colour:** H 195–225°, S 30–90, V 130–210. This matches 55% of the pixels of known rooftop and carport arrays
  and 0.23% of all pixels (1.6% of roof pixels).
- **`verify_pending`** (new, generic): rules checked on every un-outlined patch box before it can count.
  - `max_green_frac` 0.3: vegetation pixels. Rejects canopy shade.
  - `any_of` (new, generic) passes if either alternative holds:
    - a rooftop array: `min_building_overlap` 0.3 (new; Overture) and `min_height_m` 2.5 (new; lidar, only where it is
      built);
    - a ≥ 1,000 m² connected array (ground mount or carport).
  - `require_outline`: a box that passes is queued for SAM but counted only after its outline passes. It is reported in
    `metrics.awaiting_outline_not_counted` and kept in `pending_outline`, so a partial result is never memoised as final.
- **`object_check`** (on the colour-refined SAM outline) keeps the area, compactness and colour rules and the same
  green and `any_of` rules, plus three new generic rules:
  - `rectangularity_min` 0.8 (outline area ÷ minimum rotated rectangle): known arrays score 0.85–0.88, whole house
    roofs 0.6–0.8.
  - `max_host_cover` 0.6 (outline ÷ the Overture footprint(s) it sits on): a blue-grey roof covers its whole footprint;
    the known arrays cover about 30%.
  - `min_host_contrast` 50: the median (saturation, brightness) distance between the object and the rest of its roof.
    Known arrays score 77–82; blue-grey roof planes and dark membrane roofs score 2–45. If the contrast can't be
    measured on a roof, the object fails.
- The rules were introduced in two steps, each justified by an eye-check:
  1. Colour plus the box verifier left 256 objects in Huntersville. The eye-check was 0/48: shadows, canopy, tennis
     courts and asphalt were gone, but whole blue-grey house roofs and dark flat roofs remained.
  2. The outline shape and contrast rules removed those roofs.
- Tuning:
  - **Tuning area:** Huntersville (false positives) plus the South End warehouse arrays (positives).
  - **Holdout:** Matthews and Uptown; Myers Park, Ballantyne and NoDa serve as the OSM check.

### Before / after (API, NAIP 2022, `find_all` with `place`)
| Area (role) | sense | before | after | after: rejected / awaiting outline |
|---|---|---|---|---|
| Huntersville (tuning) | res (sampler) | 34,178 | **0** | 347 / 124 |
| Huntersville (tuning) | comm (planner default) | 5,948 | **1** | 14 / 0 |
| Matthews (holdout) | res | 22,745 | **1** | 90 / 0 |
| Matthews (holdout) | comm | 7,630 | **0** | 9 / 0 |
| Uptown (holdout, central) | res | 1,193 | **0** | 78 / 0 |
| Uptown (holdout, central) | comm | 522 | **0** | 7 / 0 |
| South End (positives used in tuning) | res | – | **5** | 244 / 0 |
| Myers Park / Ballantyne / NoDa | res | 5,854 / 2,750 / – (validation build) | **0 / 0 / 0** | 36 / 149 / 24 |

**After eye-check** (`data/out/solar_after.png`: every detection left in all seven areas, both senses, 7 in total; fewer
than 48 exist): **7/7 look like panel arrays.**
- 5 are in South End, the area the positives came from.
- 1 is in Matthews (holdout).
- 1 is in Huntersville: a small array on a dark commercial roof.

This is an engineer eye-check, not a review.

**OSM reference recall** (a lower bound: OSM solar is sparse, 63 features county-wide):
- **Huntersville:** before 0/1, after 0/1. The single OSM point is not visible as an array in NAIP.
- **South End:** after **8/12** for `res` (5 arrays, each within 10 m of OSM roof polygons on one warehouse complex);
  0/12 for `comm`.
- **Myers Park:** after 0/5. These OSM polygons are bright grey residential panels (V ≈ 220, S ≈ 7), which the colour
  cannot see.
- **Ballantyne:** after 0/1, a 260 m² array off any footprint (< 1,000 m²).
- **NoDa:** after 0/1, a bright grey ground array.
- **Before**, for the four OSM areas, only an upper bound is available: a colour-stage simulation of the old rule
  covered each area with 3,500–7,200 shadow objects and "hit" 3/19 OSM features by chance (one of them a bare-ground
  polygon).
- Matthews and Uptown have no OSM solar.

**Pools are unchanged:** pools have no `verify_pending`, and their rules evaluate identically. Ballantyne
`find_all(swimming_pool)`: 364 objects before (validation build 07:25; 157 rejected) and **364 after** (157 rejected).

### Limits (why solar stays experimental)
- **Recall is very low and unmeasured.** The verifier is precision-first.
- **Bright grey panels are invisible to the colour.** That covers ground farms and many residential arrays in NAIP 2022.
- **Panels on blue-grey roofs fail the contrast rule.**
- **Only the top 300 scan objects per call are outlined.** Boxes beyond that are never counted (Huntersville `res`:
  124).
- **The examples are still wrong.** `pick_candidates` picks them from CLIP plus colour, and after the colour fix it
  picks a tennis court (`res`) and a glazed skylight (`comm`). Its "unknown" tile is a real array. This is the main
  recall issue left, and it is outside this config.
- **The thresholds rest on few sites.** The positives are 5 arrays on one South End complex plus one array tile.
- **Lidar exists only in some places.** It is not built for Huntersville or Matthews, so the height rule is skipped
  there.
- **Vegetation is judged by an RGB rule, not NDVI.**
- **Precision needs human reviews.** It will only be measured once Validation Studio reviews exist. Those samples were
  built from the old output, so they will measure the old detector.
