# Licence gate

Date: **2026-11-07**. These are research notes, not legal advice. They were checked against primary sources:
GitHub LICENSE files and repo metadata, Hugging Face model cards and API `cardData`, official dataset pages and papers.
Nothing was installed.

**Rule applied.** An item passes only if its code and its weights are MIT, BSD, Apache-2.0 or public domain (CC-BY is
allowed for data, with attribution). It also needs no non-commercial or research-only terms anywhere.

**When training data binds the weights.** Some weights are released under a permissive licence by the people who
trained them, while the training images carry a research-only dataset licence (SA-1B, Objects365, Flickr30k). In that
case the lineage is recorded below, but it does not fail the item. This is the same basis on which SAM 2.1 (SA-1B
lineage) and OpenCLIP (LAION lineage) were already accepted in `SOURCES.md`. Training-data terms do fail an item when
they are passed through to the weights: either the weights licence is itself restrictive, or the publisher gives no
clear licence (the GeoRSCLIP precedent). See Decisions needed, D1.

**Hard bans.**
- ultralytics (AGPL) and anything that depends on it.
- ArcGIS Living Atlas models.
- Weights trained on xView or DOTA.
- Anything trained on Google data or hosted only by Google.

## Gate table

| # | item | repo / hosting | code licence | weights licence (where hosted) | training data & terms | commercial OK | attribution | verdict | reason |
|---|---|---|---|---|---|---|---|---|---|
| 1 | GeoAI package (`geoai-py`) | https://github.com/opengeos/geoai | MIT License | n/a | n/a | Yes | Licence notice | **PASS** | No ultralytics in `requirements.txt` or any optional extra (source grep: no `ultralytics` import). Required deps are heavy (torchgeo, leafmap, localtileserver, jupyter-server-proxy, spacy, overturemaps, datasets…). Avoid all extras. `rfdetr` is the only detector extra and was not cleared |
| 1a | GeoAI `BuildingFootprintExtractor` | HF `giswqs/geoai` / `building_footprints_usa.pth` | MIT | `license: mit` (HF card: "trained from scratch using the GeoAI Python package") | torchvision `maskrcnn_resnet50_fpn`, 2 classes. Training notebook uses NAIP RGB (`naip_rgb_train.tif`, public domain) and `naip_train_buildings.geojson` from HF dataset `giswqs/geospatial` (`license: mit`); where the labels originally came from is not stated | Yes | Licence notice | **PASS (provisional)** | Weights MIT; imagery is NAIP. The original source of the labels is undocumented |
| 1b | GeoAI `SolarPanelDetector` | HF `giswqs/geoai` / `solar_panel_detection.pth` | MIT | `license: mit` | Mask R-CNN R50-FPN. Notebook trains on `solar_panels_davis_ca.tif` + `.geojson` (Davis, CA). Imagery provider and label source not stated | Yes (as tagged) | Licence notice | **UNCLEAR** | Weights tagged MIT, but the imagery and label provenance are undocumented. Trained on a single small scene |
| 1c | GeoAI `ParkingSpotDetector` (`ParkingSplotDetector` is a deprecated alias) | HF `giswqs/geoai` / `parking_spot_detection.safetensors` | MIT | `license: mit` | Mask R-CNN R50-FPN, 3 classes. **No training notebook or data source published**. The demo image is credited to OpenAerialMap | Yes (as tagged) | Licence notice | **UNCLEAR** | Training data unknown |
| 1d | GeoAI `CarDetector` | HF `giswqs/geoai` / `car_detection_usa.pth` | MIT | `license: mit` | Mask R-CNN R50-FPN. Trained on `cars_7cm.tif` (7 cm imagery, so not NAIP; the notebook points to OpenAerialMap). Provider and label licence not stated | Yes (as tagged) | Licence notice | **UNCLEAR** | Provenance undocumented. It also needs about 7 cm GSD, so it will not work on 60 cm NAIP |
| 1e | (note) `giswqs/geoai` also redistributes `dinov3_vitl16_sat493m.pth` plus `DINOv3_LICENSE.md` | same repo | – | DINOv3 License (despite the repo-level `mit` tag) | SAT-493M | see #16 | – | **FAIL** | Custom DINOv3 License; do not pull this file |
| 2 | Grounding DINO (original) | https://github.com/IDEA-Research/GroundingDINO; weights from GitHub releases / HF `ShilongLiu/GroundingDINO` | Apache License 2.0 | Repo licence Apache-2.0 (no separate weights licence) | Swin-T OGC: O365, GoldG, Cap4M. Objects365: "available for the academic purpose only"; annotations CC BY 4.0; images under Flickr ToU. GoldG includes Flickr30k ("non-commercial research and/or educational purposes"). Cap4M is unreleased web data | Yes (weights) | Licence notice | **PASS (lineage note)** | Weights Apache-2.0; the research-only datasets do not purport to bind the weights (D1). The custom CUDA op build is painful; prefer the HF port |
| 2b | Grounding DINO, HF transformers port | HF `IDEA-Research/grounding-dino-tiny`, `IDEA-Research/grounding-dino-base` | transformers Apache-2.0 | `license: apache-2.0`, not gated | Same lineage. Base adds COCO / OpenImages / RefCOCO-family data per the paper | Yes | Licence notice | **PASS (lineage note)** | Preferred route: pure transformers, no compiled ops |
| 3 | OWLv2 | HF `google/owlv2-base-patch16-ensemble`, `google/owlv2-large-patch14-ensemble`; code https://github.com/google-research/scenic | Apache License 2.0 | `license: apache-2.0` | OWL-ST self-training: "We use the WebLI dataset as the source of weak supervision" (arXiv 2306.09683). WebLI is Google-internal web data. The annotator was trained on Objects365 + Visual Genome; fine-tuned on LVIS_base | Licence: yes | Licence notice | **FAIL (Google ban)** | Licence-clean. We read the ban as covering Google data, and these weights come from Google's WebLI; hosting on HF does not change that. If the ban only covers Google tiles/imagery, it becomes PASS (D2) |
| 4 | segment-geospatial (`samgeo`) | https://github.com/opengeos/segment-geospatial | MIT license | – | – | Yes | Licence notice | **PASS** | Core deps (`segment_anything`, torch, rasterio…) have no ultralytics. Do **not** install `[samgeo3]` (pulls `sam3`, SAM License), `[text]` (pulls `groundingdino-py`, not reviewed) or `[all]` |
| 4a | SAM 1 weights | `dl.fbaipublicfiles.com/segment_anything/sam_vit_{b,l,h}_*.pth`; code https://github.com/facebookresearch/segment-anything | Apache License 2.0 | "The model is licensed under the Apache 2.0 license" | SA-1B (dataset under the "SA-1B Dataset Research License") | Yes | Licence notice | **PASS (lineage note)** | Same basis as the SAM 2.1 already accepted |
| 4b | SAM 2 / 2.1 weights | HF `facebook/sam2-hiera-{tiny,small,base-plus,large}`, `facebook/sam2.1-hiera-*`; https://github.com/facebookresearch/sam2 | Apache License 2.0 | Apache-2.0 ("SAM 2 model checkpoints … are licensed under Apache 2.0") | SA-1B + SA-V | Yes | Licence notice | **PASS** | Already in use |
| 4c | SAM 3 / 3.1 weights | HF `facebook/sam3`, `facebook/sam3.1` (gated: manual); https://github.com/facebookresearch/sam3 | "SAM License" (custom) | "SAM License" (`license: other`) | – | Licence permits it, but it is custom | Licence copy | **FAIL** | Custom Meta licence with a field-of-use clause (ITAR / military / weapons) and a California-law agreement; not MIT/BSD/Apache |
| 5 | TiTiler | https://github.com/developmentseed/titiler | MIT License | n/a | n/a | Yes | Licence notice | **PASS** | |
| 5b | rio-tiler | https://github.com/cogeotiff/rio-tiler | BSD-3-Clause | n/a | n/a | Yes | Licence notice | **PASS** | |
| 6 | PDAL | https://github.com/PDAL/PDAL | "released under the terms of the BSD open source license" (LICENSE.txt) | n/a | n/a | Yes | Licence notice | **PASS** | Individual plugins and linked libs (e.g. GEOS, LGPL) have their own terms; core is BSD |
| 7 | USGS 3DEP lidar | AWS `s3://usgs-lidar-public` (EPT, us-west-2, no account); `s3://usgs-lidar` (raw LAZ, requester pays); index http://usgs.entwine.io; managed by Hobu, Inc. | – | n/a | Registry licence: "US Government Public Domain". USGS FAQ: "free and in the public domain … There are no restrictions" | Yes | Courtesy: "Map services and data available from U.S. Geological Survey, National Geospatial Program." | **PASS** | The registry/README document EPT only; COPC is not documented there. Not verified elsewhere; do not assume a COPC endpoint |
| 8 | Valhalla | https://github.com/valhalla/valhalla | The MIT License (MIT) (`COPYING`) | n/a | Routing graph built from OSM (ODbL) | Yes | Licence notice + "© OpenStreetMap contributors" | **PASS** | Tiles built from OSM are a derived database (see #22) |
| 9 | OSRM | https://github.com/Project-OSRM/osrm-backend | BSD-2-Clause | n/a | OSM (ODbL) | Yes | Licence notice + OSM credit | **PASS** | Same ODbL note |
| 10 | DuckDB + spatial | https://github.com/duckdb/duckdb, https://github.com/duckdb/duckdb-spatial | MIT License (both) | n/a | n/a | Yes | Licence notices | **PASS (note)** | Spatial bundles (vcpkg.json) GDAL (MIT/X-style), PROJ (MIT-style), sqlite3 (public domain), **GEOS (LGPL-2.1)** (D4) |
| 11 | deck.gl | https://github.com/visgl/deck.gl | MIT License | n/a | n/a | Yes | Licence notice | **PASS** | |
| 12 | PMTiles | https://github.com/protomaps/PMTiles | BSD-3 (reference implementations); spec "public domain, or CC0" | n/a | n/a | Yes | Licence notice | **PASS** | Sample tilesets in the repo have their own terms; don't use them |
| 12b | tippecanoe (Felt fork) | https://github.com/felt/tippecanoe | BSD-2-Clause | n/a | n/a | Yes | Licence notice | **PASS** | |
| 12c | Planetiler | https://github.com/onthegomap/planetiler | Apache License 2.0 | n/a | Typically OSM input (ODbL) | Yes | Licence notice + OSM credit | **PASS** | Needs Java; only if building basemap tiles |
| 13 | Martin | https://github.com/maplibre/martin | "Licensed under either of" Apache-2.0 or MIT | n/a | n/a | Yes | Licence notice | **PASS** | |
| 14 | Florence-2 | HF `microsoft/Florence-2-base`, `microsoft/Florence-2-large` | MIT (code in HF repo, `trust_remote_code`) | `license: mit` (+ LICENSE file) | FLD-5B: 5.4B auto-generated annotations on 126M images from ImageNet-22k, Objects365, Open Images, Conceptual Captions, LAION (paper 2311.06242 §4.1). FLD-5B release/licence not stated | Yes | Licence notice | **PASS (lineage note)** | MIT weights; Objects365 / ImageNet research-only lineage (D1). Uses remote code, so pin a revision |
| 15 | Clay v1.5 | code https://github.com/Clay-foundation/model; weights HF `made-with-clay/Clay` (`v1.5/clay-v1.5.ckpt`) | Apache License 2.0 | `license: apache-2.0` ("Source code and Model weights: Apache-2.0") | 70M chips of Sentinel-2, Sentinel-1, Landsat 8/9, NAIP, LINZ, MODIS. All open: Copernicus free/open; USGS/NASA/USDA public domain; LINZ CC BY 4.0. DINOv2 teacher (Apache-2.0) | Yes | Licence notice (+ Copernicus/LINZ credit if redistributing data) | **PASS** | Includes 4-band NAIP in its spec, so it is a direct embedding candidate |
| 16 | DINOv3 (incl. SAT-493M variants) | https://github.com/facebookresearch/dinov3; HF `facebook/dinov3-vitl16-pretrain-sat493m`, `facebook/dinov3-vit7b16-pretrain-sat493m` (gated: manual) | "DINOv3 License" | "DINOv3 License" (`license: other`) | SAT-493M: 493M Maxar satellite images (Meta's description; not checked further). LVD-1689M for web variants | Licence does **not** forbid commercial use | Licence copy on redistribution; acknowledge in publications | **FAIL** | Custom licence, not MIT/BSD/Apache. Summary below |
| 17 | TorchGeo (code) | https://github.com/torchgeo/torchgeo | MIT | n/a | n/a | Yes | Licence notice | **PASS** | Weights are licensed per weight (next rows) |
| 17a | TorchGeo `TileNet_Weights.NAIP_ALL_TILE2VEC` (4-band) | via TorchGeo; source https://github.com/ermongroup/tile2vec | MIT | MIT (TorchGeo table) | NAIP (public domain) | Yes | Licence notice | **PASS** | Old and small (2018); weak baseline only |
| 17b | TorchGeo `Unet_Weights.NAIP_RGBN_RESNET18_CHESAPEAKERSC` | source https://github.com/isaaccorley/ChesapeakeRSC | MIT | MIT (TorchGeo table) | NAIP + Chesapeake land-cover labels (label licence not checked) | Yes (as tagged) | Licence notice | **UNCLEAR** | Segmentation model, not an embedder; label terms unverified |
| 17c | TorchGeo `Swin_V2_B_Weights.NAIP_RGB_{SI,MI}_SATLAS` | source https://github.com/allenai/satlas | – | **ODC-BY** (TorchGeo table) | SatlasPretrain (NAIP + labels incl. OSM-derived) | Yes | Attribution | **UNCLEAR** | ODC-BY is attribution-only, but it is not on the allowed list (D5). OSM-derived labels raise an ODbL question |
| 17d | TorchGeo `DOFA{Base,Large}16_Weights.DOFA_MAE` (any wavelengths, incl. RGB/NIR) | https://github.com/zhu-xlab/DOFA | – | **CC-BY-4.0** | Multi-sensor open EO data (not checked) | Yes | Attribution | **UNCLEAR** | CC-BY on weights; allowed for data only under the current rule (D5) |
| 17e | TorchGeo `Panopticon_Weights.VIT_BASE14` | https://github.com/Panopticon-FM/panopticon | – | Apache-2.0 (TorchGeo table) | Not verified | Yes (as tagged) | Licence notice | **UNCLEAR** | Training data not verified |
| 17f | TorchGeo NC/custom weights (`*_CITYSCAPES_SEMSEG`, `OAM_RGB_*_TCD`, `FMOW_RGB_SCALEMAE`: CC-BY-NC-4.0; `OlmoEarth*`: "OlmoEarth Artifact License") | – | – | as listed | – | **No** / custom | – | **FAIL** | Non-commercial or custom |
| 18 | MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk | MIT License | n/a | n/a | Yes | Licence notice | **PASS** | |
| 19 | llama.cpp | https://github.com/ggml-org/llama.cpp | MIT License | n/a | n/a | Yes | Licence notice | **PASS** | |
| 20a | Qwen3-4B / Qwen3-4B-Instruct-2507 | HF `Qwen/Qwen3-4B`, `Qwen/Qwen3-4B-Instruct-2507` | – | `license: apache-2.0` | Undisclosed | Yes | Licence notice | **PASS** | Q4_K_M GGUF is 2.50 GB, fits in the 4–6 GB budget |
| 20b | Qwen3-1.7B / Qwen3-8B | HF `Qwen/Qwen3-1.7B`, `Qwen/Qwen3-8B` | – | apache-2.0 | Undisclosed | Yes | Licence notice | **PASS** | 8B Q4 is about 5 GB, too tight alongside vision models |
| 20c | Qwen2.5-7B-Instruct | HF `Qwen/Qwen2.5-7B-Instruct` (+ `-GGUF`, apache-2.0) | – | apache-2.0 | Undisclosed | Yes | Licence notice | **PASS** | Superseded by Qwen3; heavier |
| 20d | Qwen2.5-3B-Instruct | HF `Qwen/Qwen2.5-3B-Instruct` | – | **"Qwen RESEARCH LICENSE AGREEMENT"** (`license_name: qwen-research`) | – | **No** ("FOR NON-COMMERCIAL PURPOSES ONLY") | Notice file | **FAIL** | Non-commercial |
| 20e | Phi-3.5-mini-instruct / Phi-4-mini-instruct | HF `microsoft/Phi-3.5-mini-instruct`, `microsoft/Phi-4-mini-instruct` | – | `license: mit` | Synthetic + filtered public web (per Microsoft) | Yes | Licence notice | **PASS** | No first-party Microsoft GGUF found for Phi-4-mini; third-party quants (e.g. `bartowski/microsoft_Phi-4-mini-instruct-GGUF`) set no licence field and inherit MIT |
| 20f | Mistral-7B-Instruct-v0.3 | HF `mistralai/Mistral-7B-Instruct-v0.3` | – | `license: apache-2.0` | Undisclosed | Yes | Licence notice | **PASS** | About 4.4 GB at Q4; weaker tool use than Qwen3 |
| 20g | Llama 3.x | Meta | – | Llama Community Licence (custom) | – | Restricted | Required ("Built with Llama") | **FAIL** | Custom licence + AUP (by rule; not re-read) |
| 20h | Gemma | Google | – | Gemma Terms of Use (custom) | Google | Restricted | – | **FAIL** | Custom licence + Google ban (by rule; not re-read) |
| 21 | Overture Maps, **places** | https://docs.overturemaps.org/attribution/ | – | n/a | Per provider: CDLA Permissive 2.0 (Meta, Microsoft, BrightQuery, …), Apache 2.0 (Foursquare, "Copyright 2024 Foursquare Labs, Inc."), CC0 1.0 (AllThePlaces) | Yes | "Data from [provider]" + Foursquare notice + "transformed to the Overture schema" | **PASS** | CDLA-Permissive-2.0 is permissive and attribution-only (D5 covers it too) |
| 21b | Overture Maps, **buildings** (also base, divisions, transportation) | same | – | n/a | "License for theme: ODbL". Building sources include OSM, Esri Community Maps, Microsoft Global ML Building Footprints, **Google Open Buildings**, USGS 3DEP | Yes | "© OpenStreetMap contributors, Overture Maps Foundation" + listed sources | **PASS (ODbL, note)** | ODbL is share-alike (see below). Check per-record `sources`: drop any `Google Open Buildings` records under the Google ban (likely none in NC; D3) |
| 22 | NC OSM extract (Geofabrik) | https://download.geofabrik.de/north-america/us/north-carolina.html | – | n/a | "License: ODbL 1.0"; "created by OpenStreetMap Contributors" | Yes | "© OpenStreetMap contributors" | **PASS (ODbL)** | Same share-alike note |

### DINOv3 License, summary (read in full; "Last Updated: August 19, 2025")
- **Grants** a "non-exclusive, worldwide, non-transferable and royalty-free limited license" to use, reproduce,
  distribute, copy, create derivative works of, and modify the "DINO Materials". These cover code and weights.
  **Commercial use is not forbidden**, and there is no user-count threshold.
- **Redistribution**: allowed only "under the terms of this Agreement", and you must include a copy of the Agreement.
  Derivatives inherit the licence.
- **Attribution**: publications of research using it "must acknowledge the use of DINO Materials". There is no
  product-branding requirement.
- **Field-of-use**: no use subject to ITAR or "end uses prohibited by Trade Controls, including those related to
  military or warfare purposes, nuclear industries or applications, espionage, or the development or use of guns or
  illegal weapons". You must not be a sanctions target, and use must comply with privacy and data-protection law.
- **Other terms**:
  - No reverse engineering or decompiling of the underlying components.
  - Patent/IP-litigation termination clause, plus an indemnity to Meta.
  - Meta may terminate on breach (you must then delete the materials).
  - Meta may modify the terms unilaterally ("effective immediately").
  - California law and courts.
- No separate acceptable-use policy is referenced. HF access is gated (manual approval).
- The SAT-493M weights are under the same DINOv3 License.
- **Verdict FAIL**: it is a custom, revocable, field-restricted licence, not MIT/BSD/Apache. It is not non-commercial,
  so a human could choose to accept it (D6).

### ODbL (OSM, Overture buildings) — what it implies here
- **Local use is acceptable.** Querying and displaying OSM/Overture data in the demo, and using it as weak reference
  sets, is fine with the attribution "© OpenStreetMap contributors" (plus Overture and its sources).
- **Share-alike applies to derived databases that are publicly used.** Clipped layers and `data/reference/osm_*.geojson`
  are derived databases. If we ever distribute them or serve them publicly as a database, they must be offered under
  ODbL, or as the diffs/method.
- **Produced Works do not have to be ODbL.** Rendered maps, tiles, images and statistics only need attribution.
- **Keep ODbL data separate.** Do not merge it into a proprietary or non-ODbL dataset that is shipped. Keep model
  detections in their own layer, separate from the ODbL layers (a "collective database").
- **Routing graphs** built by Valhalla or OSRM from OSM are derived databases; the routes they return are produced
  works.

## Decisions needed
- **D1 (lineage rule).** Confirm that research-only training sets (SA-1B, Objects365, Flickr30k, ImageNet) do not
  disqualify weights that their publisher releases as Apache/MIT. This matches the SAM 2.1 and OpenCLIP precedent.
  Grounding DINO, Florence-2 and SAM 1 depend on this.
- **D2 (Google ban scope).** Does it cover Google-trained weights (OWLv2: WebLI) or only Google data/tiles? Current
  reading: it covers them, so OWLv2 FAILs.
- **D3 (Overture buildings).** Filter out `Google Open Buildings` records by per-record source (none expected in NC).
- **D4 (LGPL).** Accept unmodified, dynamically linked LGPL-2.1 libraries (GEOS in DuckDB spatial; it is already
  present via Shapely and rasterio wheels)?
- **D5 (attribution-only licences on weights/data).** Extend "similarly permissive" to ODC-BY (Satlas NAIP), CC-BY-4.0
  weights (DOFA) and CDLA-Permissive-2.0 (Overture places, already in use)?
- **D6 (DINOv3).** Accept a custom commercial-OK but field-restricted Meta licence? Recommended: no. Keep DINOv2
  (Apache-2.0).
- **D7 (GeoAI detectors).** Solar, car and parking weights have undocumented training data. Use them only as
  unpublished internal baselines, or skip them. The building extractor is provisionally OK. The car model needs
  about 7 cm imagery and will not work on NAIP.

## Recommended stack
- **Open-vocabulary detector:** Grounding DINO via HF transformers, `IDEA-Research/grounding-dino-tiny` (Apache-2.0),
  with `-base` as the larger option. Second candidate: `microsoft/Florence-2-base` (MIT, pinned revision). Both
  depend on D1. OWLv2 is excluded under the Google ban. Masks: SAM 2.1 (already in use). No SAM 3.
- **Local LLM planner:** **Qwen3-4B-Instruct-2507** (Apache-2.0), run in llama.cpp (MIT).
  - First-party GGUF: `Qwen/Qwen3-4B-GGUF` (Apache-2.0, `Qwen3-4B-Q4_K_M.gguf`, 2.50 GB; hybrid thinking model).
  - For the non-thinking 2507 instruct build: `unsloth/Qwen3-4B-Instruct-2507-GGUF` (tagged apache-2.0, Q4_K_M
    2.50 GB) or `ggml-org/Qwen3-4B-Instruct-2507-Q8_0-GGUF` (apache-2.0).
  - Fallback: Phi-4-mini-instruct (MIT).
  - Not Qwen2.5-3B (research licence), not Llama or Gemma.
- **Embedding bake-off candidates (PASS):**
  - DINOv2 ViT-B/14 (Apache-2.0, current).
  - OpenCLIP ViT-B-16 LAION-2B (MIT, current).
  - **Clay v1.5** (Apache-2.0, NAIP-aware).
  - TorchGeo Tile2Vec NAIP (MIT, weak baseline).
- **Embedding candidates pending D5 or verification:** Satlas NAIP Swin-V2-B (ODC-BY), DOFA (CC-BY-4.0), Panopticon
  (Apache-2.0, data unverified).
- **Excluded:** DINOv3 and DINOv3-SAT (custom licence), OlmoEarth (custom), ScaleMAE (CC-BY-NC).
- **Serving and geo tooling (all PASS):**
  - TiTiler / rio-tiler, PDAL + USGS 3DEP EPT (public domain).
  - DuckDB spatial (D4), deck.gl, PMTiles + tippecanoe, Martin.
  - Valhalla or OSRM (ODbL attribution on outputs).
  - MCP Python SDK.
- **GeoAI:** optional. If used, install the base package only (no extras). Vendoring the ~30-line torchvision Mask
  R-CNN loader with `hf_hub_download` gives the same result without the heavy dependency tree.

## Decisions closed (Monish, 2026-11-08)
- **D1 Grounding DINO + Florence-2:** accepted for Groundtruth: "weights Apache/MIT; training data partly
  research-only; legal review required before ORYN commercial use".
- **PDAL:** not used; laspy (BSD-2) + lazrs (MIT) read the 3DEP EPT. Closed.
