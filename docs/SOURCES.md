# Sources and licences

Every dataset and model Groundtruth uses. One-line summary per item for sign-off:
**Commercial use OK? · Attribution required?**

## Datasets

| dataset | link | retrieved | licence | commercial OK? | attribution? | notes |
|---|---|---|---|---|---|---|
| Mecklenburg County boundary (Census TIGERweb State_County, GEOID 37119) | https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1 | 2026-11-07 | US Government work, public domain | Yes | No (courtesy credit "US Census Bureau") | `data/boundary/mecklenburg.geojson`, 1,413.18 km² |
| USDA NAIP imagery (collection `naip`) via Microsoft Planetary Computer STAC | https://planetarycomputer.microsoft.com/dataset/naip | 2026-11-07 | Public domain (USDA FSA). Planetary Computer [Terms of Use](https://planetarycomputer.microsoft.com/terms) apply to the hosting/API | Yes | No (courtesy credit "USDA NAIP") | Full-county years: 2022, 2020, 2018, 2016, 2014, 2012. Primary **2022** (0.6 m, 2022-06-02…2022-09-27), secondary **2020** (0.6 m, 2020-07-11…2020-07-12) |
| NAIP display tiles: Planetary Computer data API (TiTiler-pgstac mosaic) | https://planetarycomputer.microsoft.com/api/data/v1 | 2026-11-07 | Imagery public domain; service under PC Terms of Use | Yes (imagery) | No | Proxied + cached by the labelling server (`data/cache/tiles`) |
| Place search: OpenStreetMap Nominatim | https://nominatim.openstreetmap.org | 2026-11-07 | Data © OpenStreetMap contributors, ODbL 1.0; service under the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) (≤1 req/s, identifying UA, caching) | Yes | **Yes** ("© OpenStreetMap contributors") | Used only to jump the map; results cached in `data/cache/geocode.json` |

### Not used (licence)
- Mecklenburg County GIS and City of Charlotte layers (parcels, zoning): licence unresolved; Charlotte open data is
  CC BY-NC-SA. Not used. If ever used, local demo only, never committed, marked "not cleared for commercial use".

## Software (runtime dependencies)

| package | licence |
|---|---|
| FastAPI, Uvicorn, Pydantic, httpx | MIT / BSD-3 |
| NumPy, Shapely, pyproj, Pillow | BSD-3 / BSD-3 / MIT / MIT-CMU (HPND) |
| rasterio | BSD-3 |
| pystac-client, planetary-computer | Apache-2.0 / MIT |
| MapLibre GL JS 4.7.1 | BSD-3 |

### Reference layers (Step 3; files in `data/layers/`)

| dataset | link | retrieved | licence | commercial OK? | attribution? | notes |
|---|---|---|---|---|---|---|
| OpenStreetMap: motorways (2,199 segments; I-485 = 391, I-77/I-85 flagged), LYNX light rail (207 segments, 74 station nodes), water (13,991), UNC Charlotte campus (1). Tag-filtered first (pyosmium BackReferenceWriter, 96 MB), then clipped to the county | https://download.geofabrik.de/north-america/us/north-carolina.html | extract 2026-11-06, **copied from ORYN cache** | ODbL 1.0 | Yes | **Yes** ("© OpenStreetMap contributors"); share-alike applies to derived databases | Clipped to the county with pyosmium |
| OSM reference set (weak, per concept): swimming pools 1,685 · surface parking 6,278 · warehouses ≥ 2,000 m² 348 · construction 465 · solar 63 | https://www.openstreetmap.org/copyright | extract 2026-11-06 (via ORYN cache) | ODbL 1.0 | Yes | **Yes** ("© OpenStreetMap contributors") | `data/reference/osm_<concept>.geojson` (`scripts/fetch_osm_reference.py`). Known positives for recall checks only: incomplete, **not** the human test set, never written to `data/labels/` |
| FEMA NFHL flood hazard zones (MapServer 28, DFIRM 37119C) | https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28 | 2026-11-07 | Public domain (US Government work, FEMA) | Yes | No (courtesy credit "FEMA National Flood Hazard Layer") | Raw: `data/layers/fema_flood_zones.geojson`, 34,704 polygons, 267 MB, paged by DFIRM_ID 37119C (local, gitignored). Web: `data/layers/web/fema_flood_zones.geojson`, 8,753 regulatory polygons (1% SFHA + 0.2% band, minimal-hazard X dropped), simplified ~3 m, 8.4 MB, built by `scripts/build_web_layers.py`. Not for regulatory determinations. |
| Overture Maps places (release 2026-10-21.0) | https://docs.overturemaps.org/attribution/ | 2026-11-07 | Per-record check over the 79,385 Mecklenburg places: CDLA-Permissive-2.0 (Meta 35,418 · BrightQuery 23,214 · Microsoft 13,995 · others 768), **Apache-2.0 (Foursquare 4,864)**, CC0-1.0 (AllThePlaces 1,126) | Yes | Yes (Overture + listed sources) | Only id, name, category, confidence and per-record source licence kept. No phones, emails, websites or addresses. |
| Overture Maps buildings (release 2026-10-21.0) | https://docs.overturemaps.org/attribution/ | 2026-11-07 | ODbL 1.0 | Yes | **Yes** ("© OpenStreetMap contributors, Overture Maps Foundation"); share-alike on derived databases | `overture_buildings.parquet` |

## Models

| model | link | licence | commercial OK? | attribution? | use |
|---|---|---|---|---|---|
| DINOv2 ViT-B/14 `facebook/dinov2-base` @ `f9e44c81…` | https://huggingface.co/facebook/dinov2-base | Apache-2.0 | Yes | Licence notice | image↔image (`find_similar`, clustering). Weights are shared with ORYN's HF cache. |
| OpenCLIP ViT-B-16 `laion2b_s34b_b88k` | https://huggingface.co/laion/CLIP-ViT-B-16-laion2B-s34B-b88K | MIT (weights + open_clip code) | Yes | Licence notice | text↔image (`search_text`). Trained on LAION-2B (web-scraped image–text pairs); the weights themselves are MIT. |
| SAM 2.1 hiera-tiny `facebook/sam2.1-hiera-tiny` | https://huggingface.co/facebook/sam2.1-hiera-tiny | Apache-2.0 (weights + code) | Yes | Licence notice | `outline`: lazy, prompted by the hit's cell box + centre point |
| RemoteCLIP (checked, **not used**) | https://huggingface.co/chendelong/RemoteCLIP | code Apache-2.0; **weights carry no licence** on HF (no model card) | Unclear → No | – | Rejected: weights licence not stated |
| GeoRSCLIP (checked, **not used**) | https://huggingface.co/Zilun/GeoRSCLIP | tagged only "cc"; trained on RS5M (CC BY-NC 4.0) | **No** (non-commercial lineage) | – | Rejected |

| additional software | licence |
|---|---|
| PyTorch, torchvision | BSD-3 |
| open_clip_torch | MIT |
| transformers | Apache-2.0 |
| faiss-cpu | MIT |
| scikit-learn | BSD-3 |
| DuckDB (+ spatial, httpfs extensions) | MIT |
| pyarrow | Apache-2.0 |
| pyosmium (`osmium`) | BSD-2 |
| reportlab 4.4.4 (PDF one-pager export) | BSD-3 |
| torch 2.14.1+cu126 / torchvision (GPU env for the one-off precompute + approved query-time GPU) | BSD-3 |
| @fontsource/inter, @fontsource/ibm-plex-mono (UI fonts, bundled locally) | SIL OFL 1.1 (fonts) / MIT (packaging) |
| React 18.3.1, Vite 5.4.11, TypeScript 5.6.3, @vitejs/plugin-react | MIT / MIT / Apache-2.0 / MIT |
| Playwright 1.64.1 (Python; demo_check v2, compare screenshots: dev tool only) | Apache-2.0 |
| lucide-react 0.460.0 (UI icons in web/app-v2) | ISC |
| web/app-v2 (the conversational app): no new dependencies (same React, MapLibre GL 4.7.1, Vite, Inter as the classic UI) | — |

### Places config
`engine/groundtruth/conf/places.json`: neighbourhood bounding boxes resolved once via OSM Nominatim (ODbL, © OpenStreetMap
contributors), clipped to the county. UNC Charlotte comes from the OSM campus outline.

## Licence gate (2026-11-07)

Full table, reasons and open decisions: [`LICENCE_GATE.md`](LICENCE_GATE.md). Items marked PASS there are cleared for
use, but nothing below is in use yet unless it is listed above. Each item's attribution string:

| item | licence | attribution string |
|---|---|---|
| GeoAI (`opengeos/geoai`, base package only, no extras) | MIT | "GeoAI © Qiusheng Wu, MIT License" |
| GeoAI `BuildingFootprintExtractor` weights (`giswqs/geoai`, provisional) | MIT | "Building footprint model from GeoAI (giswqs/geoai), MIT License" |
| segment-geospatial (core only) | MIT | "segment-geospatial © Qiusheng Wu, MIT License" |
| SAM 1 / SAM 2 / SAM 2.1 weights | Apache-2.0 | "Segment Anything (SAM / SAM 2) © Meta Platforms, Inc., Apache License 2.0" |
| Grounding DINO (`IDEA-Research/grounding-dino-tiny` / `-base`): ACCEPTED for Groundtruth (2026-11-08): weights Apache/MIT; training data partly research-only; legal review required before ORYN commercial use | Apache-2.0 | "Grounding DINO © IDEA Research, Apache License 2.0" |
| Florence-2 (`microsoft/Florence-2-base` / `-large`), pending D1 | MIT | "Florence-2 © Microsoft Corporation, MIT License" |
| Clay v1.5 (`made-with-clay/Clay`) | Apache-2.0 | "Clay Foundation Model v1.5, Apache License 2.0" |
| TorchGeo (code) | MIT | "TorchGeo © Microsoft Corporation / TorchGeo contributors, MIT License" |
| TorchGeo Tile2Vec NAIP weights | MIT | "Tile2Vec (Jean et al., 2019), MIT License" |
| TiTiler / rio-tiler | MIT / BSD-3-Clause | "TiTiler © Development Seed (MIT); rio-tiler (BSD-3-Clause)" |
| SciPy (display-mosaic build only: `scripts/heavy/build_mosaic.py`, distance transform + bilinear sampling; already pinned in `scripts/heavy/requirements-heavy.txt`). No new dependency: the mosaic uses rasterio/GDAL (COG + JPEG), NumPy, Shapely, rio-tiler, morecantile (MIT) | BSD-3-Clause | "SciPy © SciPy developers, BSD 3-Clause" |
| PDAL | BSD | "PDAL © Hobu, Inc., BSD License" |
| USGS 3DEP lidar (`s3://usgs-lidar-public`, EPT) | US Government Public Domain | "Map services and data available from U.S. Geological Survey, National Geospatial Program." |
| Valhalla / OSRM | MIT / BSD-2-Clause | "Valhalla contributors (MIT)" / "Project OSRM (BSD-2-Clause)" + "© OpenStreetMap contributors" on routes |
| DuckDB + spatial (bundled GDAL, PROJ, GEOS (LGPL-2.1, D4)) | MIT | "DuckDB © DuckDB Foundation, MIT License" |
| deck.gl | MIT | "deck.gl © vis.gl / OpenJS Foundation, MIT License" |
| PMTiles / tippecanoe / Planetiler | BSD-3 / BSD-2-Clause / Apache-2.0 | "PMTiles © Protomaps (BSD-3)"; "tippecanoe (BSD-2-Clause)"; "Planetiler (Apache-2.0)" |
| Martin | MIT OR Apache-2.0 | "Martin © MapLibre contributors" |
| MCP Python SDK | MIT | "MCP Python SDK © Anthropic, PBC, MIT License" |
| llama.cpp | MIT | "llama.cpp © The ggml authors, MIT License" |
| Qwen3-4B / Qwen3-4B-Instruct-2507 (GGUF `Qwen/Qwen3-4B-GGUF` or `unsloth/Qwen3-4B-Instruct-2507-GGUF`) | Apache-2.0 | "Qwen3 © Alibaba Cloud (Qwen team), Apache License 2.0" |
| Phi-3.5-mini / Phi-4-mini-instruct (fallback) | MIT | "Phi © Microsoft Corporation, MIT License" |
| Mistral-7B-Instruct-v0.3 (alternative) | Apache-2.0 | "Mistral 7B © Mistral AI, Apache License 2.0" |
| Overture places | CDLA-Permissive-2.0 / Apache-2.0 / CC0-1.0 per record | "Overture Maps Foundation, overturemaps.org" + "Data from [provider]" per record; Foursquare: "Copyright 2024 Foursquare Labs, Inc." + "transformed to the Overture schema" |
| Overture buildings (ODbL; drop any Google Open Buildings records, D3) | ODbL 1.0 | "© OpenStreetMap contributors, Overture Maps Foundation" + listed building sources |
| Geofabrik NC OSM extract | ODbL 1.0 | "© OpenStreetMap contributors" |

**Not cleared:**
- OWLv2: Google ban.
- SAM 3 / 3.1: SAM License.
- DINOv3, including SAT-493M and the copy in `giswqs/geoai`: DINOv3 License.
- Qwen2.5-3B-Instruct: Qwen Research Licence.
- Llama 3.x and Gemma: custom licences.
- TorchGeo CC-BY-NC weights and OlmoEarth.
- Unclear, pending a decision or verification:
  - GeoAI solar, car and parking weights.
  - Satlas NAIP (ODC-BY), DOFA (CC-BY-4.0 weights), Panopticon and ChesapeakeRSC.

**Decision (Monish, 2026-11-08):** Grounding DINO and Florence-2 are accepted for Groundtruth: weights Apache/MIT;
training data partly research-only; legal review required before ORYN commercial use. PDAL is not used (laspy +
lazrs read the 3DEP EPT directly).
