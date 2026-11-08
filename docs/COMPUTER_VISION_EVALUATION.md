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
