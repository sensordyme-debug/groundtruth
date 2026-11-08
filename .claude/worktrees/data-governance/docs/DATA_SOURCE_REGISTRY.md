# Data source registry

Last verified: **2026-11-08**. Machine-readable twin: `engine/groundtruth/conf/sources.json`; the two files hold the
same content. Licence classifications are recommendations pending human sign-off
(`docs/compliance/COMMERCIAL_USE_APPROVAL.md`). This is not legal advice.

**Three dates per source.** These dates are never interchanged.
- **Acquisition:** when the ground was observed (the flight, or the map snapshot).
- **Publication:** when the provider released the data.
- **Ingestion:** when the data entered this project, taken from `meta.json` `built` fields or file mtimes.

## Freshness rules

`freshness_status` is measured from the newest acquisition date to 2026-11-08.

| kind | current | aging | stale |
|---|---|---|---|
| Imagery and elevation | < 2 years | 2–5 years | > 5 years |
| Crowd or vendor vector (OSM, Overture, routing graph) | < 90 days | 90–365 days | > 365 days |
| Regulatory (FEMA NFHL, live effective database) | fetched < 180 days ago | 180–365 days | > 365 days |
| Derived products | the worst status among their inputs | | |
| Live APIs | always "current": queried at request time, not cached | | |

## Ingested sources

| id | dataset | acquisition | publication | ingested | res. | coverage | CRS | bands | licence | fresh |
|---|---|---|---|---|---|---|---|---|---|---|
| `naip_2022` | USDA NAIP 2022 NC, via Planetary Computer | **2022-06-02** (11 scenes), **2022-06-22** (8), **2022-09-27** (37); dates come from the scene-id suffixes | 2023-07-20 (NOAA InPort record 70313, from a search summary; not re-checked) | 2026-11-07 | 0.6 m | whole county, 56 scenes, all cached locally | EPSG:26917 | R,G,B,NIR 8-bit | public domain | aging |
| `naip_2020` | USDA NAIP 2020 NC | **2020-07-11** (39), **2020-07-12** (18) | unverified | 2026-11-07 | 0.6 m | county in the catalogue; **43 of 57** COGs local | EPSG:26917 | R,G,B,NIR | public domain | aging |
| `naip_older` | NAIP 2018 / 2016 / 2014 / 2012 (catalogue only) | 2018-11-03/07; 2016-05-24/27; 2014-05-17/06-17; 2012-06-02/08-02 | unverified | 2026-11-07 (item lists only) | 0.6 m (2018), 1 m (earlier) | county | EPSG:26917 | 4 | public domain | stale |
| `usgs_3dep_lidar_meck_2016` | 3DEP `USGS_LPC_NC_Phase4_Mecklenburg_2016_LAS_2019` | **2016** (from the project name; exact dates unverified) | **2019** | 2026-11-07 21:57 → 2026-11-08 02:51 (lidar v2) | 1 m DSM/DTM/nDSM | 6 places: Ballantyne, CLT Airport, Lake Norman, Myers Park, University City, Uptown | EPT EPSG:3857 (USGS convention, unverified); rasters EPSG:32617 (verified) | dsm, dtm, ndsm | US Gov public domain | **stale** |
| `fema_nfhl` | FEMA NFHL layer 28, DFIRM 37119C | not recorded (live effective database; panel effective dates were not captured) | live | 2026-11-07 (layers); **2026-11-08** (risk v1) | vector | county | EPSG:4326 | – | public domain | current |
| `osm_geofabrik_nc` | Geofabrik NC extract + clipped layers | **2026-11-06T21:21:21Z** (PBF header) | 2026-11-06 | 2026-11-07 (copied from the ORYN cache); major roads 2026-11-08 | vector | NC; layers clipped to the county | EPSG:4326 | – | ODbL 1.0 | current |
| `osm_nominatim_places` | Neighbourhood bboxes (`conf/places.json`) | 2026-11-07 | live | 2026-11-07 | bbox | named places (Lake Norman extends into Iredell) | EPSG:4326 | – | ODbL + Nominatim policy | current |
| `overture_places` | Overture places 2026-10-21.0 | release 2026-10-21.0 | 2026-10-21 | 2026-11-07 | points | 79,385: CDLA-P-2.0 73,395 · Apache-2.0 (Foursquare) 4,864 · CC0 1,126 | EPSG:4326 | – | per record | current |
| `overture_buildings` | Overture buildings 2026-10-21.0 | release 2026-10-21.0 | 2026-10-21 | 2026-11-07 | footprints | 393,968: OSM 386,529 · Microsoft 7,432 · Esri 7 · **Google 0** | EPSG:4326 | – | ODbL (Esri records CC BY 4.0) | current |
| `census_tiger_boundary` | TIGERweb county 37119 | vintage not recorded | unverified | 2026-11-07 | vector | county, 1,413.18 km² | EPSG:4326 | – | public domain | current |
| `valhalla_graph_v1` | Valhalla graph built from OSM roads | 2026-11-06 (input OSM) | – | 2026-11-07 21:05 | graph | county + 5 km | EPSG:4326 | – | ODbL derived DB; Valhalla MIT | current |
| `chip_index_v3c8p192` | DINOv2 + OpenCLIP chip index | inherits NAIP 2022 / 2020 flights | – | 2026-11-07 (2022 at 18:14, 2020 at 20:13) | 224 px chips at 0.6 m | 107,770 chips per year | EPSG:4326 bboxes | RGB | derived (models: see the review) | aging |
| `dense_v1_pools` | Dense pool proposals, 2022 | inherits NAIP 2022 | – | 2026-11-07 22:11–23:39 | objects | 8 demo places | EPSG:4326 | – | derived | aging |
| `derived_lake_norman_v1` | Houses with pools, lakefront, docks | inherits NAIP 2022/2020, Overture 2026-10-21, OSM 2026-11-06 | – | 2026-11-08 04:03 | buildings | Lake Norman bbox only | EPSG:4326 | – | mixed (contains ODbL inputs) | aging |
| `risk_v1` | Flood (NFHL) + shore (OSM water) | NFHL fetched 2026-11-08; OSM 2026-11-06 | – | 2026-11-08 (shore 04:57, flood 05:08) | vector | county; 4 water bodies | EPSG:4326 (measured in 32617) | – | public domain + ODbL | current |

### Live place APIs (not cached datasets)

Terms are reviewed separately in `docs/compliance/API_TERMS_REVIEW.md`, written by another engineer. The caching rules
below are the configured ones from `engine/groundtruth/conf/providers.json`.

| id | status | caching |
|---|---|---|
| `places_foursquare` | enabled; daily caps of 300 requests and $5 | `cache_ttl_s 0`. Only `fsq_place_id` may be stored indefinitely; no other attributes are cached on Pay-as-you-Go/Sandbox. |
| `places_yelp` | enabled; daily caps of 200 requests and $3 | In-memory for 1 h (the terms allow ≤ 24 h). Business ids may be stored for matching. No review excerpts. |
| `places_google` | **disabled by policy** (no-Google rule; terms restrict use alongside a non-Google map) | Not applicable. If enabled: place ids may be stored indefinitely, lat/lng for ≤ 30 days. |

## Provenance defects found (code not edited)

1. `providers/public.py:394` `overture_source()` returns `date=""`. It should return the release, `2026-10-21.0`.
2. `providers/public.py:389` `routing_source()` dates OSM by the PBF **file mtime**, which is the copy date
   (2026-11-07). It should use the data timestamp in the PBF header, 2026-11-06T21:21:21Z. This is processing date
   used as data date.
3. The FEMA FIRM panel effective dates (NFHL layer 3 `EFF_DATE`) are not captured, so the UI cannot show "effective as
   of".
4. `lidar/v2`, `derived/v1` and `risk/v1` are not in `data/DEMO_MANIFEST.json`. `docs/INTELLIGENCE_AUDIT.md` reports
   the same gap.
5. The 3DEP flight dates are taken only from the project name. To confirm them, read the EPT `ept-sources` metadata or
   the USGS project report.

## Newer imagery investigation (research only; no imagery downloaded)

### Metadata requests made (2026-11-08)

The brief allowed one tiny service-metadata request. The recorded one is:

`GET https://meckaerial.mecklenburgcountync.gov/server/rest/services/aerial2025/MapServer?f=pjson`

It returned:
- `currentVersion` 11.5
- `serviceDescription` "aerial2025 mecklenburg county"
- `copyrightText` "MeckCoGIS"
- `spatialReference` wkid 102719 / latestWkid 2264
- `fullExtent` x 1268976.65–1651919.35, y 447176.92–663735.08 (ftUS)
- `singleFusedMapCache` true
- tileInfo: 512×512 JPEG tiles, 12 LODs, scale 1:425,000 → 1:300, finest resolution 0.2604 ft/px
- `capabilities` "Map"
- `exportTilesAllowed` false
- `maxImageWidth`/`Height` 4096
- `units` esriFeet
- `layers` []
- `documentInfo` Subject/Comments "aerial2020 mecklenburg county" (a leftover from the 2020 service)

Disclosure: I also read five `iteminfo` metadata pages, which are item descriptions, not imagery. That is more than
the single request the brief allowed.
- Mecklenburg aerial2025 `iteminfo`. accessInformation "MeckCoGIS"; licenseInfo empty; extent −81.0831, 34.9973 →
  −80.5409, 35.5274.
- Charlotte Aerial24 `iteminfo`. "Aerial Imagery for Spring 2024"; licenseInfo empty; extent −81.0696, 34.9987 →
  −80.5462, 35.5198.
- NC OneMap `Orthoimagery_20242027` `iteminfo`. "NC Orthoimagery 2024-2027"; 6-inch true colour; licenseInfo "Please
  refer to https://www.nconemap.gov/pages/terms".
- NC OneMap `Orthoimagery_2023` and `Orthoimagery_2025` `iteminfo`. Both returned **499 Token Required**.

No tiles, exports or imagery bytes were requested.

### Findings

| candidate | acquisition | resolution | bands | CRS | coverage | licence / terms | download | access |
|---|---|---|---|---|---|---|---|---|
| **Mecklenburg County aerial2025** (`meck_aerial2025`) | 2025, from the service name (flight dates unverified) | native GSD unverified; the cache goes to ~7.9 cm/px. Earlier county orthos were 4-inch Pictometry. | RGB JPEG cache (no NIR) | EPSG:2264 | whole county | **No licence.** The county Data Center says "Mecklenburg County does not support secondary distribution of this data"; imagery and LiDAR are not offered for direct download (data request form). Data is provided "as is". | Tile export disabled (`exportTilesAllowed false`). `export` of map images up to 4096 px is technically possible, but no terms grant reuse. | ArcGIS MapServer, cached |
| **City of Charlotte Aerial24** (`charlotte_aerial24`) | spring 2024 | unverified | RGB (assumed) | unverified | whole county | No licence on the item. City open data is CC BY-NC-SA (`docs/SOURCES.md`), which is **non-commercial** | not tested | MapServer |
| **NC OneMap orthoimagery** (`nc_onemap_ortho_2023`) | Feb–Apr 2023. Mecklenburg's membership of the 21-county SPM23 block is **inferred** from "True Orthoimagery for Asheville & Charlotte" in CGIA slides; **unverified** | 6 in (0.1524 m) | 4-band RGBN product; web services as true colour, CIR and NDVI | unverified | 21 counties (2023 block). The 2024–2027 service holds the latest flight per area | "refer to nconemap.gov/pages/terms" (the page is JS-rendered and I could not read it: **unverified**) | county downloads (4-band mosaics, MrSID) | ImageServer; some `/secure/` items need a token |
| NC OneMap 2025 (Eastern Piedmont) | early 2025 | 6 in | – | – | 25 Eastern Piedmont counties. Mecklenburg is **probably not** included (it is Southern Piedmont); unverified | as above. The metadata warns that pixel values were altered and the imagery is "best suited for general viewing and should not be used for raster analysis" | | |
| NC OneMap 2026 | Feb–Dec 2026 programme (12,349 mi², Greensboro / Winston-Salem area) | 6 in | | | Mecklenburg not indicated | | | |
| **NAIP 2024 NC** (`naip_2024_nc`) | unverified. NC was flown in every even year from 2012 to 2022, so 2024 is expected | 0.6 m expected | RGBN | EPSG:26917 | **Not on Planetary Computer**: the STAC search of 2026-11-07 returned no 2024 items over the county | public domain | EarthExplorer (NAIP collection) or the USDA NAIP hub | to check |

### Recommendation (targeted, AOI-clipped)

1. **Preferred, licence-clean: NAIP 2024 for NC, if USGS EarthExplorer or the USDA NAIP hub has it.**
   - It is public domain and uses the same pipeline and CRS as 2022.
   - Fetch only the quarter-quads that intersect the 8 demo AOIs, then clip to the AOIs.
   - Size: the 8 demo bboxes sum to ≈ 330 km². This is an upper bound, because South End, Myers Park, Uptown and NoDa
     overlap. At 0.6 m that is ≈ 0.9 Gpx: ≈ 3.7 GB raw RGBN, or ≈ 0.4 GB as JPEG/deflate COGs.
   - Action: a human checks EarthExplorer. Nothing is downloaded until that is done.
2. **Higher resolution: NC OneMap 2023 6-inch (RGBN).**
   - Use the official county download, not the ImageServer, because the service metadata warns that pixel values were
     altered. Clip it to the AOIs.
   - Size for the 8 AOIs at 0.1524 m: ≈ 14.2 Gpx. That is ≈ 57 GB raw RGBN, or ≈ 5–7 GB as JPEG-YCbCr COGs.
   - Blocked on two checks: that Mecklenburg is in SPM23, and the NC OneMap terms (APPROVAL_REQUIRED A3).
3. **Do not use Mecklenburg aerial2025 or Charlotte Aerial24** until the county or city grants terms in writing.
   - Mecklenburg states no secondary distribution and offers no licence.
   - Charlotte's open data is non-commercial.
   - If permission is granted, the 8 AOIs at ~3 in come to ≈ 57 Gpx, about 21 GB as JPEG COGs.

Each new year goes to a new `data/naip/vN/`-style folder on a branch, following the frozen-demo rule. It is never
written into the demo-final data.

## Sources consulted
- [Mecklenburg aerial2025 MapServer](https://meckaerial.mecklenburgcountync.gov/server/rest/services/aerial2025/MapServer)
- [Mecklenburg aerial2025 iteminfo](https://meckaerial.mecklenburgcountync.gov/server/rest/services/aerial2025/MapServer/info/iteminfo)
- [Mecklenburg GIS Data Center](https://gis.mecknc.gov/data-center)
- [Mecklenburg Open Mapping](https://maps.mecklenburgcountync.gov/openmapping/)
- [Mecklenburg 2018 4-inch ortho metadata](https://maps.mecklenburgcountync.gov/metadata/item/2018-sid-4inch-fgdc)
- [Charlotte Aerial24 MapServer](https://gis.charlottenc.gov/arcgis/rest/services/WEB/Aerial24/MapServer)
- [NC Orthoimagery 2024-2027 iteminfo](https://services.gis.nc.gov/secure/rest/services/Imagery/Orthoimagery_20242027/ImageServer/info/iteminfo)
- [NC OneMap terms](https://www.nconemap.gov/pages/terms) (not readable)
- [NC OneMap imagery](https://www.nconemap.gov/pages/imagery) (not readable)
- [NC ortho programme updates (it.nc.gov)](https://it.nc.gov/documents/gicc/ortho-program-update-jan-10-2024/open)
- [USDA NAIP hub](https://naip-usdaonline.hub.arcgis.com)
- [USGS EROS NAIP](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-aerial-photography-national-agriculture-imagery-program-naip)
- [NOAA InPort 2022 NC NAIP](https://www.fisheries.noaa.gov/inport/item/70313)
- [Planetary Computer NAIP](https://planetarycomputer.microsoft.com/dataset/naip)
- [Overture attribution](https://docs.overturemaps.org/attribution/)
