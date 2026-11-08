# Commercial-use approval matrix

> **Status: RECOMMENDATIONS ONLY, pending human sign-off.**
> I prepared this classification as a review aid. It is **not** legal approval, and none of it has been approved by
> the project owner (Monish) or by counsel. An item counts as cleared only when the owner signs off below, and for
> items marked HUMAN/LEGAL REVIEW, only when counsel does too.

Date: 2026-11-08. Detail and sources:
- `LICENSE_INVENTORY.md` (software);
- `MODEL_LICENSE_REVIEW.md` (models);
- `DATA_SOURCE_LICENSE_REVIEW.md` (data);
- `API_TERMS_REVIEW.md` (place APIs, written by another engineer and only referenced here);
- `docs/LICENCE_GATE.md` (earlier gate and decisions D1–D7).

**Classes.**
- **APPROVED.** A permissive licence or public domain, with no conditions beyond keeping notices.
- **APPROVED WITH RESTRICTIONS.** Usable commercially if the listed conditions are met.
- **HUMAN/LEGAL REVIEW REQUIRED.** There is a legal question that engineering cannot settle.
- **DO NOT USE UNTIL RESOLVED.** No grant of rights exists, or the terms conflict with project rules.

"Commercial" means use in a paid product or service, including ORYN. "Groundtruth" means the current hackathon or demo
use.

## Data

| item | Groundtruth | commercial | conditions / reason |
|---|---|---|---|
| USDA NAIP (2012–2022; 2024 if found) | APPROVED | **APPROVED** | Public domain. Courtesy credit. |
| Planetary Computer hosting (STAC, SAS, data-API tiles) | APPROVED | **APPROVED WITH RESTRICTIONS** | Serve from local COGs. Do not use the PC tile API as a production CDN. PC terms page not re-read (unverified). |
| USGS 3DEP lidar | APPROVED | **APPROVED** | Public domain. Courtesy credit. Data is stale (2016). |
| FEMA NFHL | APPROVED | **APPROVED WITH RESTRICTIONS** | Public domain. Must carry the "not a regulatory determination" disclaimer. |
| Census TIGER | APPROVED | **APPROVED** | Public domain |
| OSM (Geofabrik extract, clipped layers, shoreline, reference sets) | APPROVED WITH RESTRICTIONS | **APPROVED WITH RESTRICTIONS** | "© OpenStreetMap contributors" everywhere. ODbL share-alike on any publicly used **Derived Database** (A2). |
| OSM Nominatim (public instance) | APPROVED WITH RESTRICTIONS | **DO NOT USE UNTIL RESOLVED** for product traffic | The usage policy forbids heavy or commercial use of the public instance. Self-host or license a geocoder. |
| Valhalla graph (from OSM) | APPROVED WITH RESTRICTIONS | **APPROVED WITH RESTRICTIONS** | The graph is an ODbL derived database; isochrones are Produced Works and need attribution. Software issue: F1 below. |
| Overture places (CDLA-P-2.0 / Apache-2.0 / CC0 per record) | APPROVED WITH RESTRICTIONS | **APPROVED WITH RESTRICTIONS** | Per-record attribution, plus the Foursquare NOTICE. Needs D5 sign-off, because CDLA-P-2.0 is not literally on the MIT/BSD/Apache list. |
| Overture buildings (ODbL) | APPROVED WITH RESTRICTIONS | **APPROVED WITH RESTRICTIONS** | ODbL as for OSM. Re-check for Google Open Buildings records on each refresh (0 today). Credit Microsoft and Esri. |
| Derived Lake Norman v1 (buildings × detections) | APPROVED WITH RESTRICTIONS | **HUMAN/LEGAL REVIEW REQUIRED** | This is an ODbL Derived Database. Exporting it commercially triggers share-alike (A2). |
| CSV/GeoJSON exports containing building or OSM attributes | APPROVED WITH RESTRICTIONS | **HUMAN/LEGAL REVIEW REQUIRED** | Same as above (A2). PDF, map and count exports are Produced Works and only need attribution. |
| Chip index, dense pools, inventory (NAIP + models) | APPROVED | **APPROVED WITH RESTRICTIONS** | No data licence issue. Inherits the OpenCLIP and Grounding DINO model status below. |
| Mecklenburg County aerial2025 | **DO NOT USE UNTIL RESOLVED** | **DO NOT USE UNTIL RESOLVED** | No licence. The county "does not support secondary distribution". Imagery is by request only (A4). |
| City of Charlotte Aerial24 | **DO NOT USE UNTIL RESOLVED** | **DO NOT USE UNTIL RESOLVED** | No item licence. City open data is CC BY-NC-SA (non-commercial). |
| NC OneMap orthoimagery (2023 block; 2024–2027 service) | HUMAN/LEGAL REVIEW REQUIRED | **HUMAN/LEGAL REVIEW REQUIRED** | The terms page was unread (JS-rendered). Mecklenburg's inclusion is unverified. Some services need a token (A3). |
| Foursquare / Yelp Places APIs | see API_TERMS_REVIEW.md | see API_TERMS_REVIEW.md | Paid commercial terms, with display and caching limits. |
| Google Places / Geocoding | **DO NOT USE UNTIL RESOLVED** | **DO NOT USE UNTIL RESOLVED** | Project "no Google data" rule. The terms restrict use with a non-Google map. Disabled in `providers.json`. |

## Models

| item | Groundtruth | commercial | conditions / reason |
|---|---|---|---|
| DINOv2 base (Apache-2.0) | APPROVED | **APPROVED** | Keep the NOTICE |
| SAM 2.1 hiera-tiny (Apache-2.0; SA-1B lineage) | APPROVED | **APPROVED** | D1 lineage rule (owner-accepted for Groundtruth). Counsel should confirm D1 for commercial use. |
| Qwen3-4B GGUF (Apache-2.0) | APPROVED | **APPROVED** | Keep the NOTICE |
| OpenCLIP ViT-B-16 LAION-2B (MIT) | APPROVED WITH RESTRICTIONS | **HUMAN/LEGAL REVIEW REQUIRED** | The model card says "any deployed use case … commercial or not … out of scope". The LAION-2B training set was withdrawn (A5). |
| Grounding DINO tiny (Apache-2.0; Objects365/Flickr30k lineage) | APPROVED WITH RESTRICTIONS (owner D1) | **HUMAN/LEGAL REVIEW REQUIRED** | The owner's D1 decision says "legal review required before ORYN commercial use" (A1). |

## Software

| item | Groundtruth | commercial | conditions / reason |
|---|---|---|---|
| Permissive Python and npm deps (MIT, BSD, Apache, ISC, PSF, BlueOak, OFL fonts) | APPROVED | **APPROVED** | Keep notices. Fonts must not be sold on their own. |
| MPL-2.0 (certifi, tqdm) | APPROVED | **APPROVED** | Unmodified |
| GCC runtime in numpy, scipy, faiss and sklearn wheels | APPROVED | **APPROVED** | GCC Runtime Library Exception |
| GEOS LGPL-2.1 (shapely, rasterio, DuckDB spatial, pyvalhalla) | APPROVED WITH RESTRICTIONS | **APPROVED WITH RESTRICTIONS** | Dynamic and unmodified. Ship the LGPL text and allow relinking if distributed. D4 sign-off needed. |
| NVIDIA CUDA wheels (GPU env) | APPROVED WITH RESTRICTIONS | **APPROVED WITH RESTRICTIONS** | Redistribution only per the NVIDIA EULA (matters for container images) |
| **pyvalhalla wheel**: bundled librttopo and jbigkit (GPL-2.0+), spatialite and freexl (tri-licence), LGPL-3 libs | APPROVED WITH RESTRICTIONS | **HUMAN/LEGAL REVIEW REQUIRED** | Hosted use only: no distribution, so GPL-2 obligations are not triggered. Any shipped build needs review, a rebuild without spatialite, or OSRM (A6). |
| Ultralytics / any AGPL | forbidden | forbidden | Not installed (verified) |

## Sign-off

| role | name | date | scope approved | signature |
|---|---|---|---|---|
| Project owner | | | | |
| Counsel | | | | |
