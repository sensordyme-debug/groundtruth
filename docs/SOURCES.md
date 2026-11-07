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
| OpenStreetMap: roads (I-485/I-77/I-85 flagged), LYNX light-rail stations, water, UNC Charlotte campus | https://download.geofabrik.de/north-america/us/north-carolina.html | extract 2026-11-06, **copied from ORYN cache** | ODbL 1.0 | Yes | **Yes** ("© OpenStreetMap contributors"); share-alike applies to derived databases | Clipped to the county with pyosmium |
| FEMA NFHL flood hazard zones (MapServer 28, DFIRM 37119C) | https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28 | 2026-11-07 | Public domain (US Government work) | Yes | No (courtesy credit "FEMA NFHL") | `fema_flood_zones.geojson` |
| Overture Maps places (release 2026-10-21.0) | https://docs.overturemaps.org/attribution/ | 2026-11-07 | Per-record check over the 79,385 Mecklenburg places: CDLA-Permissive-2.0 (Meta 35,418 · BrightQuery 23,214 · Microsoft 13,995 · others 768), **Apache-2.0 (Foursquare 4,864)**, CC0-1.0 (AllThePlaces 1,126) | Yes | Yes (Overture + listed sources) | Only id, name, category, confidence and per-record source licence kept. No phones, emails, websites or addresses. |
| Overture Maps buildings (release 2026-10-21.0) | https://docs.overturemaps.org/attribution/ | 2026-11-07 | ODbL 1.0 | Yes | **Yes** ("© OpenStreetMap contributors, Overture Maps Foundation"); share-alike on derived databases | `overture_buildings.parquet` |

## Models

| model | link | licence | commercial OK? | attribution? | use |
|---|---|---|---|---|---|
| DINOv2 ViT-B/14 `facebook/dinov2-base` @ `f9e44c81…` | https://huggingface.co/facebook/dinov2-base | Apache-2.0 | Yes | Licence notice | image↔image (`find_similar`, clustering). Weights are shared with ORYN's HF cache. |
| OpenCLIP ViT-B-16 `laion2b_s34b_b88k` | https://huggingface.co/laion/CLIP-ViT-B-16-laion2B-s34B-b88K | MIT (weights + open_clip code) | Yes | Licence notice | text↔image (`search_text`). Trained on LAION-2B (web-scraped image–text pairs); the weights themselves are MIT. |
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
| MCP Python SDK (`mcp`), Hatchling | MIT / MIT |
| pytest-asyncio, respx, jsonschema (Donovan test tooling) | Apache-2.0 / MIT / MIT |
