# Groundtruth API contract — v0.4

Every tool, whether an engine function, an HTTP route or an agent action, returns **one envelope**. Implementation:
`engine/groundtruth/envelope.py` (pydantic, validated). Example responses for every tool: `docs/fixtures/<tool>.json`.

## Transport
| | |
|---|---|
| base URL | `http://<host>:8000/api/v1` (host is announced separately; Tailscale only) |
| tool call | `POST /api/v1/tools/{tool}` with a JSON body (below) → `200` + envelope |
| health | `GET /api/v1/health` → `{ok, contract, engine_version, indexes: [{aoi, year, chips, index_version}]}` |
| tool list | `GET /api/v1/tools` → `[{tool, implemented, version}]` |
| chip image | `GET /api/v1/chips/{chip_id}.jpg?year=2022` → 224×224 JPEG (this is the `thumb_url` in features) |
| layers | `GET /api/v1/layers` → `[name]` (inputs to `add_layer`) |
| display tiles | `GET /api/v1/tiles/naip/{year}/{z}/{x}/{y}.png` (XYZ, z 10–18; USDA NAIP via Planetary Computer, cached) |
| boundary | `GET /api/v1/boundary` → the Mecklenburg County GeoJSON Feature |
| places | `GET /api/v1/places` → `{source, places: {name: [w,s,e,n]}}`: neighbourhood boxes (OSM Nominatim, ODbL) for `place` / `inside_place` |
| export files | `GET /api/v1/exports/{name}` → the file that an `export` call wrote (`metrics.download`) |
| agent stream | `GET /api/v1/events` (SSE). **Reserved**; not implemented in v0.1 |

Errors: a tool that will not answer still returns **HTTP 200** with `status: "declined"` and a `declined_reason`.
HTTP 4xx is used only for malformed requests (wrong JSON shape), and 5xx only for server faults.

## Envelope
```jsonc
{
  "tool": "search_text",                 // tool name
  "version": "0.2.0",                    // engine tool version (semver)
  "status": "ok",                        // ok | partial | declined
  "result": {
    "geojson": { "type": "FeatureCollection", "features": [] },  // always EPSG:4326
    "metrics": { "count": 24 }                                     // units in key names
  },
  "confidence": null,                    // 0..1, or null when not meaningful (similarity scores are not probabilities)
  "method": "…",                         // one line: what was computed
  "assumptions": ["…"],
  "limits": ["…"],
  "sources": [{ "name": "USDA NAIP 2022", "url": "…", "date": "2022-06-02/2022-09-27", "licence": "Public domain …" }],
  "steps": [{ "phase": "embed_query", "detail": "…", "ms": 41.2 }],   // for the work-log panel
  "declined_reason": null                // string iff status == "declined"
}
```
- `partial`: usable but incomplete. The usual cause is that only a slice is indexed, not the county; `limits` says why.
- `declined`: no result. `declined_reason` is a short code or a sentence. **`"not_implemented"`** means the route exists but the tool
  is not built yet.
- Units are in field names: `area_m2`, `area_acres`, `perimeter_m`, `dist_m_to_<layer>`. Measurements are computed in EPSG:32617.
- Deterministic: the same request on the same index version gives the same order. Ties are broken by `chip_id`.

## Ids
- **chip_id** `"{year}_e{col}_n{row}"`: a 224×224 px NAIP chip (134.4 m) on a fixed EPSG:32617 grid (stride 115.2 m).
- **cell**: 0–63, row-major from the chip's north-west corner (8×8 grid of 16.8 m cells).
- **example id**: `"chip_id"` or `"chip_id#cell"`, e.g. `"2022_e4472_n33675#30"`. Use the `example` property that
  `pick_candidates` returns. Without `#cell`, the engine guesses the cell.

## Result features (search tools)
`Polygon` = the chip footprint. Properties:
`chip_id, year, scene_id, thumb_url, chip_bbox [w,s,e,n], cell, cell_bbox [w,s,e,n], score, rank` plus tool-specific fields.
`cell_bbox` is the matched 16.8 m sub-cell. Use it to draw the hit and to prompt `outline`.

## Tools

Common optional body fields for the search tools: `year` (default = primary year, 2022) and `aoi` (default = `"county"` when
built, otherwise the largest indexed slice).

| tool | status | request body | result |
|---|---|---|---|
| `search_text` | ✅ | `{query: str, k?: int=50}` | ranked chips for a text query; `metrics.count, indexed_chips, score_max` |
| `pick_candidates` | ✅ | `{query: str, n?: int=6}` | `n` tiles, one per meaning. Properties add `sense_id, sense_label, example, raw_score, group_members[]`; `metrics.senses` |
| `find_similar` | ✅ | `{examples: [id], exclude?: [id], k?: int=50, bbox?: [w,s,e,n], rerank?: bool=false, concept?: str}` | chips whose best 16.8 m cell matches mean(examples) − 0.5·mean(exclude); overlapping duplicates removed. `rerank` (v0.3): the top ~400 are re-scored with full-resolution DINOv2 patches (8.4 m) + the concept's colour (`conf/concepts.json`); `properties.rerank {patch_sim, colour}` |
| `find_all` | ✅ (v0.3) | `{examples: [id], exclude?: [id], k?: int=200, bbox?\|place?: str, concept?: str, rerank?: bool=true, outline_top?: int=200}` | **the core tool.** Ranked, deduped matches (county, bbox or named place). `geometry` = outline polygon when cached, else the matching cell box. Properties: `example, rank, score, confidence, geometry_kind ("outline"\|"cell"), area_m2, area_acres` + the chip fields. Metrics: `count_detected` (confidence ≥ `confidence_cut`), `count_returned, outlined, outlines_queued, area_acres_total, median_area_m2`. Outlines for the top `outline_top` are computed in the background; poll `outline` for those still `cell` |
| `refine` | ✅ | `{session_id?: str, accept?: [id], reject?: [id], k?: int=50, bbox?\|place?, concept?}` | re-ranked hits from a per-session logistic regression on cell vectors; `metrics.session_id, refine_ms`. Send the returned `session_id` back to accumulate feedback. v0.3 adds `properties.confidence` (= P(match)) and `properties.why {nearest_accepted, similarity}`; < 100 ms after the first call |
| `compare_years` | ✅ (v0.3) | `{examples: [id], from_year?: int=2020, to_year?: int (= year), concept?: str}` | per example: `properties.change` = `new \| existing \| unsure \| no_data`, `change_sim, colour_now, colour_then, thumb_url_then`; metrics = counts per class. Declined when the `from_year` index for that `aoi` isn't built |
| `measure` | ✅ | `{geojson: FeatureCollection, to_layer?: str, to_layers?: [str]}` | same features with `area_m2, area_acres, perimeter_m, dist_m_to_<layer>`; metrics `count, area_acres_total, median_area_m2, min_dist_m_to_<layer>, median_dist_m_to_<layer>` |
| `add_layer` | ✅ | `{name: str, bbox?: [w,s,e,n]}` | the reference layer (see `/layers`: `osm_i485, osm_i77, osm_i85, osm_motorways, osm_light_rail, osm_light_rail_stations, osm_water, osm_unc_charlotte_campus, fema_flood_zones`) |
| `filter` | ✅ | `{geojson, where: {within_m_of?: {layer, m} \| [..], inside_place?: str, min_area_acres?, max_area_acres?, min_confidence?, min_score?, intersects_layer?, layer_where?, not_intersects_layer?}}` | features passing all predicates (ANDed); `metrics.count_in, count_out`. `within_m_of` / `inside_place` are new in v0.3 |
| `export` | ✅ | `{geojson, format: "geojson"\|"csv"\|"pdf", title?, summary?: {}}` | `metrics.file, download, bytes, content` (inline for text < 2 MB). `pdf` (v0.3) is a one-page summary with thumbnails, sources and limits |
| `outline` | ✅ (v0.2) | `{examples: [id with #cell], max?: int=12}` | one polygon per id (SAM 2.1 hiera-tiny, prompt = cell box + 5 m + centre point). Properties: `area_m2, area_acres, sam_score, ms, cached`; `metrics.area_acres_total, ms_per_outline`. **Lazy:** call it only for results on screen. Cost: ~1.2 s per new outline on CPU, ~4 ms cached. Pools often include part of the deck |

### Added in v0.5 (master build queue)
| tool | request body | result |
|---|---|---|
| `find_all` | adds `dense?: bool` (null = auto when a dense build exists for the place), `within_layer?: str` | with dense: `metrics.dense=true, dense_matched, dense_added, dense_replaced_boxes, dense_proposals, dense_passed_verifier, dense_built`; dense objects carry `dense_id` and the same `chip#cell:patch` `example` ids |
| `find_place` | `{query: str, bbox?\|place?, limit?: int=5000}` | Overture places by category word (`conf/places_categories.json`) or name. Points with `name, category, confidence` (Overture's). `metrics.count, match ("category"\|"name"), truncated` |
| `isochrone` | `{origin: {lon,lat}\|str, minutes?: 10, mode?: "drive"\|"walk"\|"bike"}` | one polygon (Valhalla on OSM roads). `metrics.origins[], minutes, mode, area_km2` |
| `find_buildings` | `{kind?: "building"\|"house"\|"apartment"\|..., bbox?\|place?, min_height_m?, limit?}` | Overture footprints with `class, height_m, height_source ("lidar"\|"overture"), area_m2`. `metrics.count, kind, classes` |
| `find_anything` | `{text: str, bbox?\|place?}` | open vocabulary (Grounding DINO + SAM 2.1). Same object properties as find_all plus `detector_score`; `metrics.mode="open_vocabulary", chips_searched, raw_boxes, eye_precision_12, low_confidence` |
| `filter` | `where.within_minutes_of: {origin: {lon,lat}\|name\|category word, minutes, mode}` | `metrics.travel {origins[], minutes, mode, area_km2}` |

Other v0.5 endpoints: `GET /api/v1/vt` (vector-tile layers) and `GET /api/v1/vt/{layer}/{z}/{x}/{y}.pbf` (gzip MVT,
source-layer = layer). NAIP tiles add the `X-Tile-Source` header (`local-cog` \| `tile-cache` \| `planetary-computer` \|
`offline-miss`). `GROUNDTRUTH_OFFLINE=1` blocks every Planetary Computer fetch. `POST /api/v1/agent/run` plans now carry
`target_kind (concept|place_category|buildings|open), target, minutes, mode, origin, min_height_m`.

## Open fields (may change before v1)
- `/api/v1/events` (agent SSE): reserved; the event shape is not defined yet.
- `confidence` is `null` for the search tools. Calibrated probabilities are planned once labels exist.
- `aoi`: `"county"` appears when the full-county index lands. Until then only slices (`unc_charlotte`, `south_charlotte`).

## v0.4 semantics (read this first)
- **One counting unit.** `find_all`, `refine` (with `candidates`), `filter` and `export` all count *deduped objects that
  were not rejected by the object check*. `features` is exactly that set, so `len(features) == metrics.count_detected`, and
  filter output ≤ its input (asserted in `tests/test_counts.py` and `scripts/demo_check.sh`).
- **Ids** can carry a patch: `chip_id#cell:patch` (patch 0–255 = 14 px / 8.4 m). Use the `example` field as given.
- **`find_all` with `place`/`bbox` is an exhaustive scan.** Every chip and every 16.8 m cell in the box is scored. Every
  object above the concept threshold (`conf/concepts.json` → `detect`) is counted, with negatives = the `exclude` ids
  (send the unpicked senses). Metrics add `mode: "exhaustive_scan"`, `chips_in_box`, `cells_scanned`,
  `chips_prefiltered`, `raw_detections`, `outlined`, `pending_outline`, `rejected_by_object_check`, `count_unit`.
  Without a place/bbox it is `mode: "ranked_top_k"` (not exhaustive; said in `limits`).
- **Object check** (after SAM): area range, compactness and concept-colour fraction (`conf/concepts.json` → `object_check`).
  For pools the outline is refined to SAM mask ∩ pool-cyan pixels, so `area_m2` is the water area (`sam_area_m2` keeps
  the raw SAM area). Rejected objects drop out of every count. Features carry `object_check {pass, reason, …}`.
- **`outline`** gains `concept` (applies the object check; `metrics.rejected` lists ids to drop) and accepts patch ids.
- **`refine`** gains `candidates` (the find_all object ids). It then re-scores exactly those objects: accepted → kept,
  rejected → removed, others counted when P ≥ 0.5. Features carry `confidence` (= P) and `why`.
- **`pick_candidates`**: colour senses must show their colour; otherwise the tile is
  `{no_clear_example: true, reason, geometry: null}`. Tiles carry `cell_colour` and `confidence`.
- **Every envelope states `confidence`** (per-tool heuristics, explained in `method`).
- **`GET /health`** adds `warming` and `warmup {ms, error, place}`. `ok` stays false until the warm-up has run pick +
  find_all + refine.
- **`POST /api/v1/agent/run {text}`**: the deterministic **rules planner** (no LLM). It returns
  `{planner, plan, status, steps: [{ref: "step_N", tool, envelope}], actions: [map actions]}` using the map-action schema
  (`fly_to`, `show_candidates`, `add_results`, `add_layer`, `set_filter`, `show_metrics`, `offer_download`, `say`).
  People-data requests are declined.

## Changelog
- **v0.4 (2026-11-07)**: semantics above. **Breaking:** `find_all.features` now holds only counted objects (it used to
  hold top-k candidates plus a separate `count_detected`). New: exhaustive place scan, object check, patch ids, `refine.candidates`,
  `outline.concept`, `no_clear_example`, confidence everywhere, warm-up health, `/agent/run`.
- **v0.3.1 (2026-11-07)**: additive only.
  - `outline` gains `cached_only: bool` (returns cached outlines plus `metrics.pending`, never runs SAM; for polling), and its
    default `max` is now 24.
  - `refine` features now carry `example`, `geometry_kind`, `area_m2`, `area_acres`, plus the outline geometry when cached.
  - `/health` indexes include `bbox`.
  - `thumb_url` includes `?aoi=` so chips from any index resolve.
  - Method and limits text corrected to the 8×8 (16.8 m) format.
- **v0.3 (2026-11-07)**: new tools `find_all` (core: pick → county/place results → dedupe → background outlines →
  count/acres/median) and `compare_years` (new / existing / unsure since 2020, answering Donovan's request).
  `find_similar` gains `bbox, rerank, concept`; `refine` gains `bbox/place/concept`, `properties.confidence` and `why`;
  `filter` gains `within_m_of`, `inside_place`, `min_confidence`; `measure` gains `to_layers` and median/min distances;
  `export` gains `pdf` and `metrics.download`. New routes `GET /places`, `GET /exports/{name}`. All additions are optional
  fields or new tools; v0.2 requests still work unchanged.
- **v0.2.1 (2026-11-07)**: `thumb_url` now points to the real route `/api/v1/chips/{id}.jpg` (it was `/api/chips/...`).
  Added `docs/fixtures/declined.json`. No other changes.
- **v0.2 (2026-11-07)**: `outline` implemented (was `declined: not_implemented`). No other request/response changes.
  The `/health` `contract` field is now `"0.2"`. Also added `GET /api/v1/tiles/naip/{year}/{z}/{x}/{y}.png` (display tiles) and
  `GET /api/v1/boundary` (county outline).
- **v0.1.1 (2026-11-07)**: fixtures only, no API change. `add_layer.json` shrank from 1.67 M lines (raw FEMA geometry)
  to 7 KB. Fixture geometries are simplified for readability (`_fixture.geometry_simplified_deg`). `add_layer` serves
  the simplified web build of heavy layers by default (FEMA: regulatory 1% + 0.2% zones, ~3 m simplification).
- **v0.1 (2026-11-07)**: first published contract.
  - Envelope as above; routes `/api/v1/tools/{tool}`, `/health`, `/tools`, `/chips`, `/layers`; `/events` reserved.
  - Ids `chip_id` and `chip_id#cell` on an 8×8 cell grid (index format v3, `chips224s192-g0.6-v3c8p192`).
  - `outline` declined with `not_implemented`.


## Investigations (v0.6, additive; tool envelopes are unchanged)

Plain JSON (not an envelope): an investigation aggregates many tool envelopes. Full design: docs/INTELLIGENCE_ARCHITECTURE.md.

| Route | Body / params | Returns |
|---|---|---|
| `POST /api/v1/investigations` | `{objective, context}`; header `X-GT-Owner` (anonymous browser id) | the Investigation; or `{fallback: "chat", id}` for a plain search (use `/agent/run`) |
| `GET /api/v1/investigations` | header `X-GT-Owner` | this browser's investigations: `[{id, objective, status, updated, scope}]` |
| `GET /api/v1/investigations/{id}` | `?features=1` (only once finished) | the Investigation (poll while `status` is planning or running) |
| `POST /api/v1/investigations/{id}/cancel` | — | cooperative: the running step finishes, later steps are `skipped` |
| `GET /api/v1/investigations/{id}/features/{fid}/evidence` | — | `{found, why, properties, evidence[], missing[]}` for one result |
| `POST /api/v1/investigations/{id}/feedback` | `{feature_id, verdict: confirm/reject, note}` | records human evidence + appends `data/app/v1/feedback/feedback.jsonl` |
| `GET /api/v1/investigations/{id}/report.md` | — | the Markdown report |

`context` keys (all optional):
- `area`: `{place}` / `{bbox}` / `{geojson}`
- `year`
- `mode`
- `rejected`: feature ids
- `previous`: the investigation id that a follow-up starts from

**Investigation**
- Fields: `{id, status, objective, plan, question, suggestions, events[], evidence[], findings[], contradictions[], summary, result{features, metrics}, result_step, previous, versions{investigate, data}, declined_reason}`.
- `status` is one of planning, running, done, partial, failed, cancelled, declined, needs_input.

**plan**
- Fields: `{objective, scope, targets[], constraints[], time, questions[], steps[], limitations[], planner}`.
- Each step: `{id, op, args, depends_on[], label, why, status: queued|running|done|failed|blocked|skipped, ms, detail, error, count}`.
- `op` must be one of the `OPS` keys in `investigate.py`; arguments are validated per operation before anything runs.

**Evidence**
- Fields: `{id, step_id, kind, statement, source{name, url, date, licence}, method, measurement{…units in keys}, feature_ids[], strength, confidence_note, limitations[]}`.
- `kind` is one of scope, coverage, detection, association, spatial_intersection, measurement, temporal, ordering, human.

**Finding**
- Fields: `{id, statement, nature, strength, why_strength, evidence_ids[], feature_ids[]}`.
- `nature` is one of computed, model_prediction, observed, inference, gap.
- `strength` is one of strong, moderate, weak, insufficient. It is the weakest strength along the finding's evidence chain; it is never a probability.

## Point at anything (v0.6)

`POST /api/v1/point/find`:
- Body: `{positives: [{lon, lat} | {bbox} | {id}], negatives: [...], area, year, k, outline_top, calibrate=true}`.
- Returns a `find_all` envelope (tool `point_find`).
- `metrics` adds `examples`, `negatives`, `resolved`, `calibration{threshold, bg_quantile, bg_samples, before, after, ms}`, `count_before_calibration`, `exhaustive`.
- Each kept feature carries `sim_example` / `sim_threshold` (uncalibrated cosine similarities).
