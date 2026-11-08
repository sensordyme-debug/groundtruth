# Validation Studio

## Purpose
The Validation Studio lets people review what the AI found: pools and buildings first, then parking lots, boat docks
and solar. Each reviewer accepts, rejects or corrects one prediction at a time. From those decisions Groundtruth
reports precision with honest intervals, broken down by class, region and geographic holdout group.

The reviews are a **separate, versioned evaluation dataset** (`data/validation/v1/`):
- It is never mixed with the hand labels in `data/labels/`. Those stay the sacred labelling test set, and nothing here
  writes to them.
- It replaces "OSM as ground truth" with human decisions. OSM misses most backyard pools, so OSM-based numbers are
  lower bounds.
- It reduces the Lake Norman label bias (51 of 59 pool labels sit in one 1.7 km stretch; see `docs/EVALUATION.md`).

Files:

| file | what |
|---|---|
| `engine/groundtruth/validation.py` | store, validation rules, metrics (engine, typed, deterministic) |
| `api/validation_api.py` | `APIRouter(prefix="/api/v1")`; the lead mounts it with `app.include_router(validation_api.router)` |
| `scripts/build_validation_samples.py` | builds the sample set from the running API (seeded) |
| `scripts/validation_metrics.py` | writes `data/out/validation_metrics.json` |
| `tests/test_validation.py` | journal, atomic snapshot, labels untouched, metrics, API |

## Data model
`data/validation/v1/`:

| file | mutability | content |
|---|---|---|
| `samples.jsonl` | **immutable** (the builder refuses to overwrite; sha256 in the manifest, checked on load → `progress.integrity`) | one prediction per line |
| `census_tiles.json` | immutable | tiles where every prediction of a class was sampled (for recall) |
| `manifest.json` | immutable | seed, caps, regions, holdout groups, label-cluster bbox, per-stratum populations, every API call made, versions, checksums |
| `reviews.jsonl` | append-only journal (source of truth) | `{"op":"review","review":{…}}` and `{"op":"census","census":{…}}` |
| `reviews_latest.json` | rewritten atomically (tmp + fsync + `os.replace`) after each event | latest review per sample, latest census per tile, counts |
| `backups/reviews_<utc>.json` | copy of every snapshot, newest 200 kept | |

The write path copies `groundtruth/labels.py`:
1. Append the event to the journal, then flush and fsync.
2. Rewrite the snapshot atomically.
3. Copy the snapshot to `backups/`.

State is rebuilt from the journal on start, and a torn last line is skipped. If the process crashes during the
snapshot write, the old snapshot stays intact and the journal still holds the review (tested).

**Sample record:**

| field | content |
|---|---|
| `id` | `<class>-` + first 12 hex of sha1 of `class`, the source feature id and the centroid (some tools reuse feature ids per chip, so the centroid is part of the key) |
| `class` | `pool \| building \| parking \| dock \| solar` |
| `region` | place name from `conf/places.json` |
| `holdout_group` | `north \| south \| central` |
| `geometry`, `geometry_kind` | the prediction: SAM outline, box or footprint, EPSG:4326 |
| `centroid`, `area_m2` | `area_m2` measured in EPSG:32617 |
| `source_tool`, `source_tool_version`, `source_feature_id`, `source_request`, `source_sources` | provenance; the exact request body makes the sample reproducible |
| `model`, `index_version`, `data_versions` | model and data versions behind the prediction |
| `score` | the tool's confidence |
| `score_kind` | always says "heuristic … uncalibrated" |
| `raw_similarity` | |
| `imagery_year` (2022), `scene_id`, `acquisition_date` | NAIP scene footprint lookup from `data/naip/items_2022.json`; 2022 scenes were flown 2022-06-02, 06-22 or 09-27. Where two scenes flown on different dates overlap, the date is `null` rather than guessed |
| `crop_bbox`, `crop_url` | `/api/v1/crop?...`, square, ≥ 60 m |
| `in_label_cluster` | inside the Lake Norman label-cluster bbox |
| `dist_to_nearest_label_m` | distance to the nearest human label point |
| `census_tile_id` | set when the sample belongs to a census tile |
| `sampling` | `design`, `stratum`, `stratum_eligible`, `stratum_sampled`, `weight`, `seed` |
| `built_at` | build time |

**Review:**

| field | content |
|---|---|
| `id` | |
| `sample_id` | |
| `reviewer` | the anonymous `X-GT-Owner` browser id, 8–64 of `[A-Za-z0-9-]`; never a name |
| `decision` | `accept \| reject \| uncertain \| needs_review \| corrected` |
| `polygon` | EPSG:4326 Polygon; required for `corrected` |
| `note` | ≤ 2000 characters |
| `t` | server UTC time |

The latest review wins (journal order). The full history is kept and returned by `GET /validation/samples/{id}`. A
client-supplied `review_id` makes retries idempotent.

**Census:** `{id, tile_id, reviewer, polygons[], complete, note, t}`. In it, a reviewer draws every real object of the
tile's class inside the tile. Only censuses with `complete=true` count toward recall.

## Sampling and holdout design
`scripts/build_validation_samples.py` calls the running API the same way the app does, one call at a time, with a
memory guard that stops the run if less than 3 GB is available:

| class | how predictions are produced | regions |
|---|---|---|
| pool | `pick_candidates "swimming pool"`, then backyard sense + unpicked senses as soft negatives, then `find_all` (exhaustive scan, dense where built) | Lake Norman, Cornelius, Huntersville, Ballantyne, Pineville, Matthews, Myers Park, University City |
| building | `find_buildings` (Overture) in 4 seeded random 400 m boxes per region | same as pool |
| parking | `pick_candidates "parking lot"` (surface_lot_full), then `find_all` | Huntersville, Ballantyne, Pineville, Myers Park, University City |
| solar | `pick_candidates "solar panels"` (rooftop_solar_res), then `find_all` | Huntersville, Ballantyne, Matthews, Myers Park, University City |
| dock | `find_anything "boat docks"` | Lake Norman (docks exist only on the lakes, so dock samples are north-only) |

Rules:
- **Disjoint regions.** A prediction belongs to the first listed region whose bbox holds its centroid. Cornelius and
  Huntersville overlap Lake Norman, so their samples are "Cornelius minus Lake Norman", and so on.
- **Strata = (class, region).** Candidates are sorted by id, then drawn with `random.Random("20261108|class|region")`.
  The cap is 20 per stratum for pool and building, and 12 for parking, dock and solar. Each sample carries its stratum
  population, so a population-weighted precision can be estimated.
- **Holdout groups by geography:**
  - north = Lake Norman, Cornelius, Huntersville, Davidson
  - south = Ballantyne, Pineville, Matthews
  - central = Myers Park, University City, UNC Charlotte, NoDa, South End, Uptown, Plaza Midwood, Dilworth, SouthPark
- **Separation buffer.** A prediction within 1 km of any region of another group is dropped. The closest pair is
  University City and Huntersville, about 0.5 km apart. The builder asserts that no south or central sample lies
  inside the Lake Norman bbox or the label cluster.
- **Lake Norman label cluster.** The bbox of the 51 human labels inside Lake Norman, padded by 200 m, is read only from
  `data/labels/labels.geojson`. Samples inside it are flagged `in_label_cluster`, so evaluation can exclude them:
  `GET /validation/metrics?exclude_label_cluster=true`.
- **Census tiles (for recall).** Each holdout group gets one pool tile (400 m) and one building tile (200 m). Every
  prediction that intersects a tile becomes a sample (`sampling.design = "census_tile"`).
  - Pool tiles are centred on a random Overture building from the building draw, so tile choice does not depend on
    the pool detector.
  - Building tiles are centred on a random point.
  - A tile must lie wholly inside its region, outside earlier regions, and away from other groups, so no prediction
    inside it is missing from the candidate list.
- **Outline state.** For pool, parking and solar, `find_all` is called again while background SAM outlines are still
  making progress. The final state is recorded in `manifest.calls`. Outline completion can change a later rebuild.
  This is why samples are frozen once written: rebuild into `data/validation/v2/`, never over v1.

### Built sample set (v1)
Built 2026-11-08 07:25 from the running API: engine 0.2.0, index `chips224s192-g0.6-v3c8p192`, NAIP 2022.
`samples.jsonl` sha256 starts `f948370fc2f1`. **498 samples**: 452 stratified + 46 census-tile predictions.
**0 reviews.**

Samples per region, with eligible predictions in brackets:

| class | per region: samples (eligible predictions) | total |
|---|---|---|
| pool | Lake Norman 20 (1,365) · Cornelius 31 (202) · Huntersville 20 (1,610) · Ballantyne 20 (348) · Pineville 20 (246) · Matthews 29 (1,616) · Myers Park 30 (535) · University City 20 (581) | 190 |
| building | Lake Norman 20 (280) · Cornelius 26 (31) · Huntersville 20 (107) · Ballantyne 20 (268) · Pineville 20 (136) · Matthews 26 (179) · Myers Park 20 (402) · University City 24 (127). Eligible = footprints in the 4 random 400 m boxes | 176 |
| parking | Huntersville 12 (1,593) · Ballantyne 12 (343) · Pineville 12 (607) · Myers Park 12 (126) · University City 12 (959) | 60 |
| solar | Huntersville 12 (33,024) · Ballantyne 12 (2,499) · Matthews 12 (22,131) · Myers Park 12 (5,451) · University City 12 (10,034) | 60 |
| dock | Lake Norman 12 (428) | 12 |

- **Region counts above 20** include census-tile samples.
- **By holdout group:**

  | class | north | south | central |
  |---|---|---|---|
  | pool | 71 | 69 | 50 |
  | building | 66 | 66 | 44 |
  | parking | 12 | 24 | 24 |
  | solar | 12 | 24 | 24 |
  | dock | 12 | – | – |

- **Geometry kind:**
  - pool: 118 outline, 72 box (not yet SAM-outlined);
  - parking: 56 box, 4 outline;
  - solar: 59 box, 1 outline;
  - building and dock: all outline.
- **In the Lake Norman label cluster:** 3 pool, 1 building and 4 dock samples.
- **Acquisition date:**
  - 2022-09-27: 249;
  - 2022-06-02: 245;
  - 2022-06-22: 3;
  - unknown (overlapping scenes flown on different dates): 1.
- **Census tiles:**

  | group | pool (400 m) | building (200 m) |
  |---|---|---|
  | north | Cornelius, 12 predictions | Cornelius, 6 |
  | south | Matthews, 9 | Matthews, 6 |
  | central | Myers Park, 10 | University City, 4 |

- **Solar finding:** solar `find_all` returns very large raw counts (34,179 objects in Huntersville, 22,745 in
  Matthews). The concept is marked experimental in `conf/concepts.json`. These are raw output counts, not a precision
  measurement; the solar reviews will measure precision.
- **Score ranges:**
  - pool 0.456–0.972;
  - parking 0.428–0.803;
  - solar 0.582–0.85;
  - dock 0.271–0.431;
  - building is a constant 0.9, so its calibration table is uninformative.

Reproduce:
```
bash scripts/gt.sh python scripts/build_validation_samples.py --dry-run
```
Do this against a warm API. The script refuses to overwrite v1; use `--out .../validation/v2` for a new set.

## API
All routes are under `/api/v1` and return plain JSON (no envelope). Writes need the `X-GT-Owner` header (anonymous id).

| method + path | does | errors |
|---|---|---|
| `GET /validation/samples?cls=&region=&status=unreviewed\|reviewed\|all\|<decision>&limit=50&offset=0` | `{total, offset, limit, samples:[sample + review (latest or null) + n_reviews + status]}`; default status `unreviewed` | 400 bad cls/status |
| `GET /validation/samples/{id}` | sample + latest review + `history` | 404 |
| `POST /validation/reviews` body `{sample_id, decision, polygon?, note?, review_id?}` | appends a review; returns `{review, sample}` | 400 missing/bad `X-GT-Owner`; 404 unknown sample; 422 bad decision, `corrected` without polygon, polygon not a valid Polygon in lon/lat, area ≥ 1 km², or > 1 km from the sample |
| `GET /validation/census` | census tiles + their latest census | |
| `POST /validation/census` body `{tile_id, polygons:[Polygon…], complete:true, note?}` | records a full census of a tile | 400 / 404 / 422 as above |
| `GET /validation/progress` | `{samples, reviewed, by_decision, by_class_region:{cls:{region:{samples, reviewed, accept, …}}}, census_tiles, census_complete, journal_events, integrity}` | |
| `GET /validation/metrics?exclude_label_cluster=false` | the metrics below | |
| `GET /validation/export?format=geojson\|csv` | the evaluation labels | 422 bad format |

The export contains only reviewed samples:
- **Geometry:** the human polygon for `corrected` (with the prediction kept in `prediction_geometry`), else the
  prediction.
- **Properties:** the decision, the anonymous reviewer, the time, and provenance (tool, version, model, index version,
  score, imagery date).
- **Census objects:** one feature each, with `decision = "census_object"`.

The CSV holds the same rows with centroid and WKT geometry.

## Metrics definitions
Everything here is computed only from human reviews (`validation.metrics`), and is deterministic for a given journal.

| metric | definition |
|---|---|
| **precision** | (accept + corrected) / (accept + corrected + reject). `corrected` means the object is real and only the outline was wrong. `uncertain` and `needs_review` are never positive and never in the denominator; they are reported separately (`not_counted`). Reported per class, `by_region` and `by_holdout_group`, each with n and a **Wilson 95 % interval**. When the denominator is 0, precision is `null`, not 0 |
| **weighted_precision** | the same, with each judged sample weighted by stratum population / samples drawn: an estimate for the whole tool output in the sampled regions |
| **false discovery rate** | reject / judged = 1 − precision, with a Wilson interval |
| **false-positive rate** | FP / (FP + TN) needs enumerated true negatives, which a review of detections does not have, so it is **not defined**. Census tiles report unmatched predictions (false positives per tile) instead |
| **IoU** | for `corrected` samples: the prediction against the reviewer's polygon, both measured in EPSG:32617. Reported as n, median, mean and all values |
| **calibration** | score buckets [0, .5), [.5, .6) … [.9, 1] against observed acceptance among judged samples, with a Wilson interval and mean score per bucket. The tools' scores are heuristic, so this table is how we learn what they mean |
| **recall** | only inside census tiles with a complete human census. Human objects are matched one-to-one to sampled predictions (greedy by IoU, IoU ≥ 0.1). Recall = matched / human objects, with a Wilson interval and per-tile rows. Without a complete census, the field reads `"status": "not measurable"` with the reason. It is never estimated from accept/reject reviews |

## What is measurable now vs not
| | now (0 reviews) | after reviews |
|---|---|---|
| precision per class / region / holdout group | not yet: no human reviews exist | yes, with Wilson CIs; the interval width shows when n is too small |
| precision outside the Lake Norman label cluster | not yet | yes (`exclude_label_cluster=true`) |
| outline IoU | not yet | for `corrected` samples only (accepted samples are not assumed IoU = 1) |
| score calibration | not yet | yes |
| recall | **not measurable** | only inside the 6 census tiles once complete, and only for pool and building. It is a tile-level recall, not a county-wide recall |
| false-positive rate (FP/(FP+TN)) | not defined | not defined; see the false discovery rate |
| parking / solar / dock | sampled where the tools returned results | precision only; dock is north-only |

## UI
- **Where:** the rail's **Validate** button (shield icon) opens the Studio in the conversation panel. The sample is shown on the live map, so reviewers get real NAIP imagery with zoom and pan.
- **Map overlays:**
  - the AI prediction is a dashed accent outline with a light fill;
  - the reviewer's polygon is solid white with a dark halo;
  - the camera fits the sample, at up to z19.2.
- **Queue:**
  - filters for class (pool, building, parking, dock, solar), region and status (to review / reviewed / all);
  - previous / next buttons;
  - a progress bar ("N of 498 reviewed").
- **Card:**
  - class, region and holdout group, and whether the sample sits in the training-label cluster;
  - detector and model;
  - score, labelled uncalibrated;
  - NAIP flight date and scene;
  - whether the prediction is an outline or a box;
  - the current decision.
- **Decisions:**
  - Accept / Reject / Uncertain / Needs review;
  - **Draw / correct outline** (click the corners, double-click to finish), then **Save corrected outline**, which records the decision as `corrected` with the polygon;
  - an optional note.
- **Keys:** A accept · R reject · U uncertain · N needs review · D draw · J/K next / previous.
- **Metrics panel:** per class, reviewed / samples, precision and its Wilson 95 % interval, and recall, which shows "not measurable" unless a census tile is complete. Two links export GeoJSON / CSV.
- **Tests:**
  - `scripts/studio_check.py` opens the Studio, checks the prediction is on the map and that the class filter changes the queue, and reads progress. It is **read-only**: it never submits a review, because reviews must come from people.
  - The submission path is covered by the API tests (`tests/test_validation.py`).
