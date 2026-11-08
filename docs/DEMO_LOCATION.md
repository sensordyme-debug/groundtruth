# Flagship demo location: Lake Norman (Mecklenburg shore)

Verified in code and data on 2026-11-08, not taken from older docs.

## Where it is defined
| What | Where | Value |
|---|---|---|
| Area | `engine/groundtruth/conf/places.json` → "Lake Norman"; `web/app-v2/src/App.tsx` `LN_BBOX` | bbox −80.958699, 35.427332, −80.844396, 35.514785 (≈ 10.4 × 9.7 km; Cornelius / Huntersville / Davidson shore of the Mecklenburg side) |
| Flagship investigation | `App.tsx` `STARTERS[0]` | "Find residential properties near Lake Norman with swimming pools that intersect mapped 100-year flood zones. Rank the strongest candidates and tell me where data is missing." |
| Point-find starter | `App.tsx` `STARTERS[1]` | flies to −80.886, 35.492 … −80.866, 35.506 (z16.5) |
| Lakefront starter | `App.tsx` `STARTERS[2]` | "Lakefront homes with pools and docks in Lake Norman" |
| Regression | `scripts/demo_check_v3.py` (17 checks × 3 sizes), `scripts/bench_investigations.py` | flagship + follow-up "which of those are less than 1 m above the flood elevation" |
| Frozen demo | tag `demo-final` (b6f3933), `data/DEMO_MANIFEST.json` | never modified |

## Real data already built for it
| Layer | Location | Notes |
|---|---|---|
| NAIP imagery 2022 + 2020 (also 2012–2018 item lists) | `data/naip/`, COG cache, display tiles `data/cache/tiles/local/v1` | 0.6 m; scene seams visible (mosaic not built: `docs/MOSAIC.md`, the sub-agent was stopped) |
| Chip index + embeddings (DINOv2, OpenCLIP) | `data/precompute/` (county, 2022 + 2020) | similarity search, point-find |
| Dense pool scan | `data/dense/v1/Lake_Norman/swimming_pool_2022` | exhaustive scan: 1,375 pools |
| Derived homes | `data/derived/v1/Lake_Norman/` | 946 homes with pools, 367 lakefront, 432 docks, 63 lakefront with a dock (Overture buildings + OSM water) |
| Lidar (USGS 3DEP 2016, published 2019) | `data/lidar/v2/Lake_Norman/{dsm,dtm,ndsm}.tif` | 1 m, 10,380 × 9,710; NW corner outside the lidar project |
| Canopy + slope | `data/canopy/v1/Lake_Norman/` | NDVI + nDSM, building-masked; 69 % of cells measured |
| FEMA flood zones | `data/risk/v1/flood` (+ `data/layers/fema_flood_zones.geojson`) | DFIRM 37119C, 100/500-year, floodway, static BFE where published |
| FEMA FIRM panels | `data/firm_panels/v1/` | 192 panels, effective 2009-03-02 … 2018-11-16; every result carries its panel + effective date |
| Shore | `data/risk/v1/shore` | OSM named water bodies |
| Boundaries | `data/boundaries/v1/` | county, municipalities, ZCTAs, neighbourhoods, water bodies |
| Buildings, places | `data/layers/overture_{buildings,places}.parquet` | Overture 2026-10-21.0 |
| OSM | `data/osm/north-carolina.osm.pbf` (2026-11-06T21:21Z), filtered Mecklenburg extracts, `data/layers/osm_*` | roads, light rail, water, warehouses |
| Routing | `data/routing/v1/valhalla_tiles` | drive-time analysis |
| Validation samples | `data/validation/v1` | 498 samples, **0 human reviews** |

## Not available (and why)
- **Parcels:** the county and city parcel layers are licence-blocked (`docs/SOURCES.md`; Charlotte open data is
  CC BY-NC-SA). Buildings (Overture footprints) stand in; the demo never shows parcel lines.
- **Demographics:** `CLAUDE.md` forbids data about people, including demographics. Aggregated community indicators
  are left out pending an owner decision (see the plan).
- **Imagery newer than 2022:** blocked on licences (APPROVAL_REQUIRED A3/A4).

## Plan (community disaster intelligence)
1. **Planner correctness (P0):** conditions the planner can't apply are reported, never dropped; distance to
   mapped layers, slope, floodway and year-comparison become real plan steps.
2. **Community infrastructure (real, OSM, no download):** facilities (hospitals, fire, EMS, police, schools as
   locations only, shelters), bridges, streams and dams, as `data/community/v1`, usable in distance conditions.
3. **Terrain products (real, lidar):** hillshade, relative elevation, terrain-RGB tiles for 3D, terrain profiles.
4. **Scenario engine (synthetic, isolated):** `data/scenarios/<id>/`, every record `is_synthetic: true` with
   assumptions. First scenario: a hypothetical water level above FEMA's base flood elevation, applied to real lidar
   ground heights ("which buildings and road segments would sit below this level"). This is a what-if, not a
   prediction: no rainfall-runoff model, depth, flow or timing.
5. **Vulnerability investigation:** exposed buildings by flood-zone relation, low ground, facilities and roads
   near hazards, clusters ranked by transparent criteria, the scenario overlay, and a briefing that labels every item
   REAL / SYNTHETIC / MODEL / HUMAN-VERIFIED / UNVERIFIED.
6. **Immersive map:** dark intelligence theme, 3D terrain + building extrusions, a scripted camera path, and a
   deterministic demo mode with reset.
7. **Storyboard, narration and rehearsal checklist** matched to what actually works.
