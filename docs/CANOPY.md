# Canopy and slope rasters (data/canopy/v1)

**This is a canopy cover estimate from lidar height. It does not detect individual trees.**

Built by `scripts/build_canopy.py`. `engine/groundtruth/risk.py` reads it through `canopy_facts` (`canopy_over_pct`) and
`slope_facts` (`slope_deg`). The version comes from `conf/data_versions.json` → `"canopy": "v1"`.

## Method

The rasters are written per lidar place on the 1 m lidar grid (EPSG:32617), one strip of 1024 rows at a time.

| Output | Values | Rule |
|---|---|---|
| `canopy.tif` (uint8) | 1 canopy · 0 not canopy · 255 no data | A cell is canopy when all three hold: (1) the lidar nDSM is ≥ `canopy.min_height_m` (3 m); (2) the cell is **not** under an Overture building footprint grown by `building_buffer_m` (1 m); (3) NAIP 2022 NDVI = (NIR − red)/(NIR + red), using band 4 (NIR) and band 1 (red), is ≥ `ndvi.min` (0.2) somewhere within ±`ndvi.tolerance_px` (1 px). The tolerance allows for lidar-vs-imagery misregistration and tree lean. |
| `slope.tif` (uint8) | 0–89 degrees · 255 no data | `np.gradient` of the bare-earth DTM at 1 m. Uses the same no-data mask as `canopy.tif`. |
| `overview.png` | RGBA, 4 m/px | Green alpha = share of canopy in each 4×4 m block. Grey = no data. Bounds are in `meta.json` → `overview`. It is meant as a static map overlay; the UI does not use it yet. |
| `meta.json` | | Inputs with dates, thresholds, resolution, CRS, coverage %, counts, NAIP scene ids, processing date |

All thresholds are in `conf/risk.json` → `canopy`.

**Coverage and no data.** `scripts/build_lidar.py` fills cells that have no lidar returns: the DSM is set to the DTM,
and the DTM is filled from the nearest ground point. A filled cell therefore looks like flat ground with nDSM = 0. To
avoid reading that as "no canopy", a cell counts as measured only when it lies within `coverage_radius_m` (5 m) of a
cell with DSM ≠ DTM, which means a real return. Every other cell is 255. Examples are open water with no returns and
the area outside the 3DEP project, which covers the NW part of the Lake Norman rectangle. Nothing is filled or
interpolated.

**Choosing the NDVI threshold.** I tested 1 km windows in Uptown, Myers Park, Lake Norman and CLT Airport. These are
the shares of tall, non-building cells that NDVI ≥ 0.2 within ±1 px keeps:

| Window | Kept |
|---|---|
| Myers Park | 90 % |
| Lake Norman | 88 % |
| Uptown | 62 % (tower lean, cranes, structures missing from Overture) |
| CLT Airport | ~1 % (hangars and structures missing from Overture) |

A zero shift registers within 1–3 percentage points of the best ±3 px shift.

## Inputs and dates

| Input | Date | Licence |
|---|---|---|
| USGS 3DEP lidar `USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019` → `data/lidar/v2/<Place>/{dsm,dtm,ndsm}.tif` | flown 2016, published 2019 | Public domain |
| USDA NAIP 2022, 4-band COGs (local cache), read through `PublicDataProvider.read_grid(bands=(1, 4))` with `GROUNDTRUTH_OFFLINE=1` | flights 2022-06-02 and 2022-09-27 (scene list per place in `meta.json`) | Public domain |
| Overture Maps buildings (`vector.store().buildings`), used only as an exclusion mask | release 2026-10-21.0 | ODbL 1.0: treat the rasters as a Produced Work and attribute "© OpenStreetMap contributors, Overture Maps Foundation" |
| Processing | 2026-11-08 (`meta.json` → `processing_date`) | |

## Coverage and results (build of 2026-11-08)

Only the 6 lidar places have rasters. Everything else returns `canopy_over_pct = null` with
`canopy_status = "unavailable"`, never 0.

| Place | Grid (1 m) | Measured | Canopy (% of measured) | Tall cells removed: building mask | Tall cells removed: NDVI |
|---|---|---|---|---|---|
| Ballantyne | 3655×4442 | 99.6 % | 36.2 % | 19.0 % | 9.1 % |
| CLT Airport | 8197×7769 | 99.8 % | 38.1 % | 10.7 % | 11.5 % |
| Lake Norman | 10380×9710 | 69.1 % (lake and outside the flight) | 30.3 % | 18.0 % | 6.9 % |
| Myers Park | 3650×4443 | 99.8 % | 43.4 % | 26.1 % | 7.4 % |
| University City | 7599×7202 | 99.5 % | 42.5 % | 15.2 % | 7.2 % |
| Uptown | 3098×2998 | 100 % | 16.7 % | 46.1 % | 14.9 % |

- **Build cost:** 7 min 39 s for all 6 places on the PC CPU env, with a peak RSS of 1.1 GB. The output is 112 MB.
- **NAIP:** no covered cell lacked a valid NAIP pixel, so the lidar-only fallback was never used.

**Small benchmark** (`data/out/canopy_sheet.png`). I ran all 1,011 pools from `data/derived/v1/Lake_Norman/houses.geojson`:

- **Coverage:** every pool was measured (none unavailable).
- **`canopy_over_pct`:** median 1.1 %, 55 % of pools > 0, 11 % ≥ 20 %.

The sheet shows 10 pools drawn at random (seed 7), stratified as 4 with ≥ 30 %, 3 with 5–30 % and 3 below 5 %. I
compared each overlay with the NAIP crop by eye:

- **8/10 look right:** the canopy sits on visible tree crowns and open pools read 0.
- **#4 (40.1 %) is an overestimate:** canopy covers the south half of a pool whose water is visible in 2022.
- **#3 (42.2 %) is unclear:** I can't tell the crowns from the roof there.

This was one person looking at 10 crops. It is not a labelled accuracy figure.

## Limits

- **Lidar 2016 vs imagery 2022.** Heights are about 6 years older than the imagery. The NDVI test drops most trees
  removed since 2016. Trees that grew taller than 3 m after 2016 are missed.
- **Canopy estimate, not tree detection.** Values are per 1 m cell. There are no tree counts, species or crowns.
- **Canopy over roofs is not counted.** Cells inside a building footprint (+1 m) are never canopy, even when branches
  overhang the roof. Buildings missing from Overture are caught only by the NDVI test.
- **Edges.** Lidar and NAIP can be misregistered by about 1–3 m, and crowns lean in the imagery. With the ±1 px NDVI
  tolerance, crown edges can spill about 1 m onto pools and lawns (benchmark #4).
- **Shadows.** Shadowed vegetation can have low NDVI, so tall trees in deep building shadow (Uptown) may be dropped.
- **Water.** Over water, returns are patchy, so lakes are a mix of 0 and 255. Water is never canopy.
- **Slope.** Under buildings the slope is computed on the interpolated DTM (nearest ground plus a 5 m median).
- **Coverage.** Only these 6 places have rasters. NoDa, South End and Matthews have no lidar build.

## Rebuild

```
bash scripts/gt.sh python scripts/build_canopy.py --out data/canopy/v2 [--lidar-dir data/lidar/v2] [--places "Myers Park" ...]
```

Always write a new version folder, and switch `data_versions.json` only after an A/B comparison. `--window col row w h`
builds a sub-window for probing.
