# ORYN inventory (read-only pass, 2026-11-07)

What in ORYN (`/home/orynt/oryn`, WSL) could help Groundtruth. ORYN was only read: no writes, no git, no DB
connections, no secrets or people data opened. Imported items are listed in `docs/PRIOR_WORK.md`.

## 1. Mecklenburg data on disk
| item | path | size | coverage | source · licence | decision |
|---|---|---|---|---|---|
| OSM North Carolina extract | `/home/orynt/oryn-tools/valhalla/osm/north-carolina-261106.osm.pbf` | 431 MB | NC, 2026-11-06 | Geofabrik · ODbL | **imported** → `data/osm/` |
| NC basemap PMTiles | `/home/orynt/oryn-data/basemap/nc-20261106.pmtiles` | 798 MB | NC | probably Protomaps/OSM (source not recorded) | not imported (source unconfirmed) |
| US counties pack | `oryn/src/data/local_data/us_census_counties/counties.json` | 1.5 MB | US, 1:5M, quantised | Census · PD | not needed (we use TIGERweb full-resolution) |
| OSM roads/waterways, Huntersville only | Postgres `osm_features` | – | small bbox | ODbL | not needed (full county from the PBF) |
| FEMA NFHL, Huntersville only (250 polygons) | Postgres `flood_zone_cache` | – | small bbox | PD | not needed (full county fetched from FEMA) |
| Overture parquet | none | – | – | – | fetched fresh |
| Mecklenburg County GIS / Charlotte layers | not cached; gated by `MECK_GIS_COMMERCIAL_CLEARED` (default false) | – | – | **licence unresolved**; Charlotte open data is CC BY-NC-SA | **not used** |
| NAIP chips / COGs | none on disk; scene lists only in logs | – | – | PD | not needed (our own STAC check) |

## 2. Embeddings (Postgres only: `imagery_tiles.embedding vector(768)`, HNSW cosine)
| property | ORYN | Groundtruth |
|---|---|---|
| model | DINOv2 ViT-B/14 `facebook/dinov2-base` @ `f9e44c81…`, CLS (`last_hidden_state[:,0]`) | same model and revision; CLS after the final layer norm (`pooler_output`) |
| chip | 224 px, 0.6 m, 134.4 m, **no overlap** | 224 px, 0.6 m, 134.4 m, **192 px stride (19.2 m overlap)** |
| grid | EPSG:26917, `cell_key=26917:134.4:col:row` | EPSG:32617, `chip_id={year}_e{col}_n{row}` |
| normalisation | /255, ImageNet mean/std; **not L2-normalised** | /255, ImageNet mean/std; L2-normalised |
| years usable for Mecklenburg | **2022: 91,272 · 2020: 90,611** (2018 ≈41k, partial; 2019/21/23 are SC quads) | 2022, 2020 |
| OpenCLIP | **none** | needed for `search_text` |

**Can they replace the Step 5 precompute?** Only partly. `search_text` needs OpenCLIP vectors, and ORYN has none, so
every pixel must still be read and run through OpenCLIP. Importing ORYN's DINOv2 vectors would remove the DINOv2
pass, roughly half the embedding compute. It would also need: (a) a read-only `COPY` export of about 182k rows from the
production Postgres, and (b) moving Groundtruth to ORYN's grid (EPSG:26917, no overlap) so the vectors line up 1:1. The
measured saving is in the Step 7 report. We have **not** exported anything from the DB; that is the owner's call.

## 3. Change-detection outputs (Postgres only: `imagery_change_candidates`)
NAIP 2020→2022: 47,655 scored pairs, p90 threshold 0.1925, 4,766 "surface change" candidates. Each row has
`water_fraction` (NDWI flag), `in_sfha`, `flood_zone_code`, `nearest_major_road_m` and `nearest_waterway_m`. The
2021→2023 pairs are South Carolina ground. These could power "search across time" if exported. Not imported.

## 4. Model weights
- DINOv2 base (346 MB) is in the shared `~/.cache/huggingface`, so Groundtruth uses it directly.
- There are no OpenCLIP, SAM 2 or Florence-2 weights.

## 5. Helper code
ORYN's helpers are mostly TypeScript (PC SAS signing, STAC paging, COG reads, NFHL/ArcGIS, Overpass, TIGERweb,
geocoding). Groundtruth's engine is Python, so **no helper code was copied**. The equivalents were written new in
`engine/groundtruth/providers/public.py` and `scripts/`. ORYN has no DuckDB/Overture code and no TiTiler config
(only a `titiler` venv).

## 6. Design tokens
Source: `web/src/app/tokens.css` (Tailwind v4 `@theme`) and `docs/design/DEVIN_STUDY.md`. The values are adapted into
`web/shared/tokens.css`:
- Fonts: Inter and IBM Plex Mono.
- Neutral light surfaces `#f4f4f2`/`#fff` and dark surfaces `#0e0e0e`/`#171717`.
- Accent `#191919`; map accent `#ff6a2b`.
- Radii: 8 px control, 14 px panel, 20 px chip.
- Weight 400 throughout; type scale 12–64 px.

## 7. Licence registry
- `oryn/DATA_SOURCES.md` and `docs/sprint/DATA_REGISTER.md` agree with ours: NAIP via PC is public domain and needs
  the "Microsoft Planetary Computer (preview)" wording; FEMA NFHL is public domain; Geofabrik OSM is ODbL.
- Mecklenburg GIS and Charlotte data are blocked or unresolved there.
- No DINOv2 licence entry exists in ORYN. Ours: Apache-2.0.

## 8. Not copied
- Search ranking/engine (`web/src/server/search/*`).
- The indexing and change pipeline (`web/scripts/oryn/search/*`, `imagery-ml/*`, `imagery-ingest/*`, `analytics/change.ts`).
- Auth, tenants, billing and credits.
- Leads and everything people-related.
- Secrets.
- Parcels (`nc-parcels.ts`, licence-blocked).
