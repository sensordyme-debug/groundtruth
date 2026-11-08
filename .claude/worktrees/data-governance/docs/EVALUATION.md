# Evaluation — 2026-11-08 (PARTIAL: labels are still being collected)

Index `chips224s192-g0.6-v3c8p192` · AOI `county` · NAIP 2022 · 107,770 chips · k = 24 · models: DINOv2 `dinov2-vitb14-cells8x8-pca192-v3`, OpenCLIP `openclip-vitb16-laion2b_s34b_b88k-whole-v3`, SAM `sam2.1-hiera-tiny` · 66 human labels (read-only).

**Bias warning:** 51 of the 59 swimming-pool labels sit in one 1.7 km stretch of the Lake Norman waterfront (see `scripts/label_qa.py`), so these scores mostly describe lakefront estates, not the whole county.

**How to read:** a hit counts as correct if its matched 16.8 m cell contains a hand-labelled true match *or* touches an OSM-mapped object of the concept. Unlabelled, unmapped real matches count as misses, so all precision numbers are **lower bounds**. `labels only` ignores OSM. find_all is leave-one-out from each true-match label, searched in a ~1.6 × 1.5 km box around it. Look-alike rejection = share of nearby look-alike labels that do *not* appear in the top k.

| concept | labels T/L (in index) | search_text P@k (labels+OSM / labels) | find_all P@k, leave-one-out (labels+OSM / labels) | look-alike rejection (direct) | intended meaning in 6 picks | OSM recall, top 200 in place | outline IoU vs OSM (median, n) |
|---|---|---|---|---|---|---|---|
| swimming_pool | 52/7 (52/5) | 0.583 / 0.0 | 0.492 / 0.485 (20 queries) | 2/5 (0.4) | no | Ballantyne: 14/25 (0.56) | 0.379 (14) |
| construction_site | 0/0 (0/0) | 0.125 / 0.0 | – / – (0 queries) | – | no | – | – (0) |
| warehouse | 0/0 (0/0) | 0.25 / 0.0 | – / – (0 queries) | – | yes | – | – (0) |
| parking_lot | 7/0 (7/0) | 0.292 / 0.0 | 0.512 / 0.018 (7 queries) | – | yes | Uptown: 337/407 (0.828) | 0.095 (30) |
| solar_installation | 0/0 (0/0) | 0.0 / 0.0 | – / – (0 queries) | – | no | – | – (0) |

**Reading the numbers honestly**
- *Intended meaning in 6 picks* uses the same lower-bound rule. A tile counts only if its cell is labelled or OSM-mapped, so a correct 'backyard pool' tile on an unlabelled, unmapped pool scores *no*. Check `data/out/*pick_candidates*.png` by eye.
- *find_all leave-one-out* draws its queries from the Lake Norman cluster (the bias above).
- *Look-alike rejection* tests each look-alike label directly: a box around it, searched from 3 true-match examples. The look-alikes are lake water, blue roofs and tennis courts.
- *OSM recall* counts only OSM-mapped objects in the place; most backyard pools are unmapped, so it can't measure precision. The 3 typical-size mapped objects used as examples are excluded.
- *Outline IoU* compares SAM 2.1 outlines prompted by one 16.8 m cell with OSM polygons. For pools the outline often includes the pool deck. For parking lots, one cell prompt outlines only part of a large lot, so IoU is low by design.
- By-eye top-24 counts for the re-rank (county) are in `docs/STATUS_FOR_MONISH.md` and the build log.

Reproduce: `bash scripts/gtg.sh python scripts/evaluate.py --aoi county` (raw numbers in `data/out/evaluation.json`).

## Checkpoint evaluation (2026-11-08, competition-ready checkpoint)
Index `chips224s192-g0.6-v3c8p192` (DINOv2 ViT-B/14 cells PCA-192, OpenCLIP ViT-B-16), SAM 2.1 hiera-tiny, Grounding DINO
tiny; dense `v1`; lidar 3DEP 2016. **Bias:** the hand labels sit mostly in the Lake Norman cluster (51 of 52 pool
labels); Ballantyne has none, so Ballantyne is scored against OSM pools (incomplete: OSM misses pools, and some
mapped pools are gone or covered).

| set | standard | dense | notes |
|---|---|---|---|
| Ballantyne, demo pick (soft negatives), OSM pools found | 16/28 | **19/28** | 261 → 373 objects |
| Ballantyne, backyard-only (OSM pools ≤ 150 m²) | 8/16 | **11/16** | |
| Ballantyne, evaluate.py examples (3 OSM pools), OSM found | 15/25 | 20/25 | 248 → 428 objects |
| Lake Norman, your labels (true pools found / look-alikes counted) | 41/51 · 0 | **46/51** · 0 | 1,208 → 1,587 objects |
| Pick semantics A/B (Ballantyne dense): hard negatives → soft | 12/28 · backyard 6/16 | 19/28 · 11/16 | soft = unpicked senses only nudge the query |
| Lidar height verifier (median nDSM ≤ 1.5 m) | 561 objects | 442 objects | same recall; roofs removed |
| Shadow rule (dark + > 8 m within 30 m) | — | removes 2/112 (Ballantyne), 1/52 (Uptown) | < 3/50 gain: OFF |
| Eye precision: dense top 50 / dense additions | — | ~49/50 / ~38/50 | Uptown additions ~7/50 → dense OFF in Uptown |

Open vocabulary (county-wide CLIP top 500 chips, top 12 by eye, baseline → adopted CLIP crop verifier + box-clipped
masks): boat dock 12 → 12, school bus lot 9 → 11, golf green 6 → 6, baseball diamond 6 → 6, construction crane 4 → 6,
tennis court 4 → 5, water tower 4 → 3, basketball court 2 → 3, cemetery 1 → 1; trampoline 0 (below 0.6 m). 48 → 53/108.

Other: lidar vs Overture building height (Uptown, 1,652 buildings) median |Δ| 3.9 m, r 0.73 · new since 2020 in
Ballantyne: 17 "new", ~9 clearly new by eye · planner: plan 35/35, declines 49/49.


## Point at anything (2026-11-08, `scripts/eval_point_find.py`, `data/out/eval_point_find.json`)

Setup:
- One click on an OSM-mapped example per type. Places were chosen away from the Lake Norman label cluster: Ballantyne for pools and parking, Steele Creek for warehouses, and the place with the most OSM solar for solar.
- **OSM precision is a lower bound**, because OSM misses most backyard pools.
- Eye counts come from the contact sheets `data/out/t2_<type>_<k>.png`.

| Type | Raw scan: count / OSM P@24 | Calibrated: count / OSM P@24 | Eye, calibrated top 24 | Warm latency |
|---|---|---|---|---|
| swimming pool | 137 / 0.458 | 51 / 0.417 | **23/24** (1 tennis court) | 0.2–0.5 s (cold first scan 3.5 s) |
| warehouse | 432 / 0.417 | 154 / 0.542 | ~22/24 (large industrial roofs) | 0.2–0.4 s (cold 2.3 s) |
| parking lot | 70 / 0.583 | 31 / 0.375 | not eye-checked | 0.2 s (cold 1.1 s) |
| solar installation | 6 / 0.833 | 6 / 0.833 | n=6 | 0.2 s (cold 2.0 s) |

The earlier Lake Norman click (on a house) returned 7,137 objects raw and **1,160 calibrated**. A cold scan of that 100 km² area takes 21 s; warm it takes 0.8–2.3 s.

**Refining with "Not this":**
- **Before the fix,** one hard negative collapsed every set (pools 137 → 3).
- **After the fix,** "not this" is a soft scan negative plus a patch-level rejection:
  - pools: 3 refines → 15 results, OSM 0.733;
  - warehouses: 3 refines → 47, OSM 0.542.
- **The refine numbers come from a flawed oracle:** the simulated user rejects the top results that OSM doesn't map, and for pools those are mostly real backyard pools. They show the mechanism works, not the quality a real user would get.
- **Parking** still collapses to 1–3 results after one rejection, with or without calibration. **Open issue**: large flat classes need a weaker negative.
- **Next fix:** per-type negative weight, plus a human-reviewed refine benchmark.

## Investigations (2026-11-08, `scripts/bench_investigations.py`)

See docs/INTELLIGENCE_BENCHMARK.md. Results:
- 12/12 expected status, 12/12 expected plan, 12/12 deterministic.
- 2/2 people declines.
- Flagship: 0.86–1.7 s total, 0.4–0.8 s to the first result.

Flagship numbers on real data (Lake Norman):
- 946 homes with pools.
- 20 touch a FEMA 100-year flood zone (all AE).
- The height above the base flood elevation is known for 13 of the 20, median 0.5 m; 7 have no published BFE.
- Follow-up "less than 1 m above": 12 of 20.
- Lakefront-only variant: 19 of 367.
- These depend on derived v1, whose precision is **not re-measured**, so the headline finding is graded "weak".
