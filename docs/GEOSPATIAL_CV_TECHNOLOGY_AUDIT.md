# Geospatial / computer-vision technology audit: GeoPandas, GDAL, YOLO, OpenCV (2026-11-08)

Question: would adopting these four tools materially improve Groundtruth?

How I answered it:
- Usage was checked by grep over `engine/`, `api/`, `scripts/` and `tests/`, and by `importlib.util.find_spec` in both Python environments (`~/.venvs/groundtruth`, `~/.venvs/groundtruth-gpu`).
- Nothing was installed for this audit.

## Usage today

| Library | Code | Installed | Notes |
|---|---|---|---|
| GeoPandas | absent | no | Vector work is done by DuckDB Spatial (`engine/groundtruth/vector.py`, `risk.py:69`) and Shapely (`derived.py`, `point.py`, `risk.py`). |
| GDAL (`osgeo`) | absent (transitive only) | no `osgeo` | We reach GDAL through rasterio (WarpedVRT, windowed reads, COG writing) and through DuckDB Spatial's bundled GDAL. |
| YOLO (Ultralytics) | absent | no | AGPL, so forbidden (LICENCE_GATE.md:18). No permissive YOLO-family detector is present. |
| OpenCV (`cv2`) | absent | no | Image work uses PIL, numpy, `scipy.ndimage` (dense blobs, mosaic) and `rasterio.features` (masks to polygons). |
| rasterio, rio-tiler, DuckDB, Shapely, pyproj, PIL, torch, scipy, faiss, laspy+lazrs | direct | yes | laspy/lazrs are in the CPU env only. |

## Decisions

### GeoPandas: REJECT for the core stack (re-evaluate only for report or GIS interchange)
- **What it would replace:**
  - spatial joins and overlays: DuckDB Spatial already does indexed range queries and `ST_*` functions;
  - per-feature tests: Shapely 2 with STRtree;
  - CRS handling: pyproj via `geo.to_measure`.
- **Measured on our own data, through the live API:**

  | operation | time |
  |---|---|
  | FEMA 100-year intersection for 946 Lake Norman homes (DuckDB bbox query + Shapely intersects over 8,761 zones) | 414 ms, cold within the investigation |
  | elevation, shore and WHY facts for 20 homes | 356 ms |
  | follow-up filter + explain | 37 ms |

  None of these is a bottleneck. The slow parts are the cold imagery scans (3–21 s), which GeoPandas doesn't touch.
- **No GeoPandas benchmark was run.** It isn't installed, and an apples-to-apples run would need a new environment. Because no vector step in the investigation path costs more than 0.5 s, the most it could save is a fraction of 0.5 s.
- **Cost:** GeoPandas is a large dependency. It pulls in pandas plus pyogrio or Fiona, which bundle another GDAL. It would duplicate DuckDB, and DuckDB is already the faster columnar path for Overture.
- **Licence:** BSD-3 (fine).
- **When to revisit:**
  - if we add shapefile / GeoPackage import or export for analysts (pyogrio alone may be enough);
  - if report generation needs dissolve/overlay chains that are clumsy in SQL.

### GDAL (direct `osgeo` bindings): REJECT
- rasterio already gives us every GDAL capability we use: windowed reads, WarpedVRT reprojection, resampling, nodata, overviews, COG writing (`providers/public.py`, `scripts/heavy/build_mosaic.py`), and rasterize/shapes (`rasterio.features`).
- The real raster problem, the scene seams, is a radiometric problem, not missing tooling. Overlap mismatch is a median 0.54 grey levels; the visible band is a low-frequency brightness gradient within each scene (docs/MOSAIC.md). Neither `gdalwarp` nor `gdalbuildvrt` corrects that.
- The fix is a dodging/feathering step, which numpy and scipy already provide.
- **Cost of adopting:** a second GDAL build next to rasterio's wheel, with version clashes in the same process.
- **Licence:** MIT/X (fine).

### YOLO: REJECT Ultralytics; DEFER any permissive YOLO-family detector
- **Ultralytics is AGPL-3.0**, which the project forbids.
- **Permissive alternatives**, such as RT-DETR or YOLOX with Apache weights:
  - None is installed or cleared (LICENCE_GATE lists `rfdetr` as "not cleared").
  - A closed-set detector needs **training labels**. We have 66 human labels: 52 pools, 7 look-alikes and 7 parking lots, and 51 of the pool labels sit in one 1.7 km stretch of Lake Norman.
  - Training on these and evaluating with a geographic holdout isn't possible without new labelling: the holdout would be a handful of objects.
- **What it would compete with** (different tasks):
  - DINOv2 patch retrieval: open-set and example-driven, no training;
  - Grounding DINO: open-vocabulary detection by text;
  - SAM 2.1: segmentation given a prompt;
  - YOLO would be a fast closed-set detector for fixed classes.
- Our strongest current results come from **example-driven retrieval plus verifiers**, which is the product's differentiator:
  - "Find like this", calibrated: 23/24 pools by eye from one click;
  - Lake Norman dense pools: 46/51 labels.
- **Revisit when:**
  - there are at least ~300 geographically spread labels per class, and
  - a permissive detector passes the licence gate.
- Then the measure is per-class precision/recall with a geographic holdout (train on the north of the county, test on the south) against the retrieval baseline on the same holdout.

### OpenCV: DEFER (adopt selectively only for image registration in change detection, once there are change labels)
- **Covered today:**
  - morphology and connected components: `scipy.ndimage`;
  - contours to polygons: `rasterio.features.shapes`;
  - colour spaces: our own HSV in `rerank.py`;
  - resizing: PIL.
- **The one place OpenCV would add something:** sub-pixel alignment of 2020 vs 2022 crops (ECC / phase correlation) before comparing them.
  - The audit names misalignment as a false-change source.
  - The dock check absorbs it today with a 1.2 m buffer.
- We have no human-reviewed change labels to measure an improvement against, apart from "~9 of 17 clearly new" in Ballantyne and 0 of 69 docks new.
- **Licence:** Apache-2.0 (fine). A headless wheel is about 40 MB.
- **Adopt when** a change-label set exists, measured as change precision with vs without registration.

## Summary

| Technology | Decision | Reason in one line |
|---|---|---|
| GeoPandas | REJECT (core) | DuckDB Spatial + Shapely already do it; vector steps are not the bottleneck (< 0.5 s). |
| GDAL (direct) | REJECT | rasterio exposes what we use; the seam is radiometric, not a tooling gap. |
| YOLO | REJECT Ultralytics (AGPL) · DEFER permissive | No label base for a geographically honest closed-set model; example-driven retrieval is the differentiator. |
| OpenCV | DEFER | Only image registration would help, and it can't be measured until change labels exist. |
