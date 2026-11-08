# Data source licence review

Date: 2026-11-08. These are research notes, not legal advice. The classifications are recommendations pending
owner/counsel sign-off. Dates and coverage for every source are in `docs/DATA_SOURCE_REGISTRY.md` and
`engine/groundtruth/conf/sources.json`. Third-party place APIs (Foursquare, Yelp, Google) are reviewed in
`docs/compliance/API_TERMS_REVIEW.md`, which another engineer is writing, and are not repeated here.

## Public-domain US Government data

| source | licence basis | obligations | recommendation |
|---|---|---|---|
| **USDA NAIP** (2012–2022) | US Government work, public domain. See the USGS EROS NAIP page (https://www.usgs.gov/centers/eros/science/usgs-eros-archive-aerial-photography-national-agriculture-imagery-program-naip) and the PC dataset page, which says "public domain" (https://planetarycomputer.microsoft.com/dataset/naip). | none; courtesy credit "USDA NAIP" | **APPROVED** |
| **USGS 3DEP lidar** | AWS Registry of Open Data, "US Government Public Domain" (https://registry.opendata.aws/usgs-lidar/). USGS: "no restrictions". | courtesy credit (string in SOURCES.md) | **APPROVED** |
| **FEMA NFHL** | US Government work (https://www.fema.gov/flood-maps/national-flood-hazard-layer) | none. Do not present results as a regulatory flood determination; the UI already says this. | **APPROVED** (with the disclaimer) |
| **Census TIGERweb** | US Government work (https://www.census.gov/data/developers/about/terms-of-service.html covers the API; the data is public domain) | none | **APPROVED** |

## Hosting terms: Microsoft Planetary Computer

- **What the terms cover.** The imagery licence (public domain) is separate from the **service** terms
  (https://planetarycomputer.microsoft.com/terms). The page is JS-rendered and could not be read on 2026-11-08, so its
  exact text is **unverified**. From prior reading (unverified now), the terms apply Microsoft's terms of use to the
  APIs and SAS-token blob access and defer to each dataset's own licence for the data.
- **How Groundtruth uses PC.**
  - It reads COGs with short-lived SAS tokens (`planetary-computer` package) and caches them locally.
  - It proxies data-API tiles **only as a fallback** (`api/app.py:509-528`).
  - Tiles are cached to disk (`data/cache/tiles/naip`).
- **Assessment.** Caching public-domain pixels is unproblematic for the data itself. Heavy commercial re-serving of
  the PC **tile API** could exceed fair use. **APPROVED WITH RESTRICTIONS**: serve from local COGs (the default path)
  and do not use the PC tile API as a production CDN.
- **Platform risk** (unverified, check before relying on it). Microsoft has retired parts of the Planetary Computer,
  including the Hub, and the data API's long-term availability is not guaranteed. Local copies avoid this risk.

## OpenStreetMap (ODbL 1.0), the most important obligation

- **Licence.** ODbL 1.0 (https://opendatacommons.org/licenses/odbl/1-0/), with the OSMF attribution guidelines
  (https://osmfoundation.org/wiki/Licence/Attribution_Guidelines). Attribution: "© OpenStreetMap contributors".
- **Used in Groundtruth for:**
  - clipped layers (`data/layers/osm_*.geojson`);
  - reference sets (`data/reference/osm_*.geojson`);
  - shoreline (`data/risk/v1/shore`);
  - the Valhalla graph;
  - neighbourhood bboxes (Nominatim);
  - Overture buildings and transportation (see below).
- **Share-alike: what triggers it.**
  - A **Derived Database** that is **Publicly Used** must be offered under ODbL, or the derived database and the
    method made available (§4.4, §4.6).
  - The clipped layers, reference sets, shoreline, the routing graph, and any table that **joins** OSM or Overture
    buildings to Groundtruth detections are Derived Databases. One example is the Lake Norman houses-with-pools set,
    which is a list of Overture building footprints flagged with pools and docks.
  - **CSV and GeoJSON exports** (`/api/v1/tools/export`) of features that carry OSM/Overture building geometry or
    attributes are extracts of a Derived Database. If such exports are offered to the public or customers, the
    **share-alike obligation applies to that extracted database**.
- **What does not trigger it.**
  - **Produced Works** only need attribution (§4.3): rendered maps, PNG/vector tiles shown in the UI, PDFs that show
    maps, counts and statistics.
  - Detections derived purely from NAIP pixels (pool outlines from SAM) are **not** ODbL, as long as they are kept in
    a separate layer. That keeps them a "Collective Database" (§1, §4.5), not a Derived Database.
- **Recommendation: APPROVED WITH RESTRICTIONS.**
  1. Show attribution everywhere OSM-derived content appears (the map, exports, reports).
  2. Keep NAIP-only detections in their own layer.
  3. Before any **commercial export** of building-joined results, the owner decides one of:
     - (a) accept ODbL share-alike for those exports;
     - (b) export only Produced Works (maps, counts, PDFs);
     - (c) replace the building geometry with Groundtruth-only geometry.
     This is APPROVAL_REQUIRED A2.
- **Nominatim.** The usage policy (https://operations.osmfoundation.org/policies/nominatim/) allows at most 1 req/s,
  needs an identifying User-Agent, requires caching, and **forbids heavy or bulk commercial use** of the public
  instance. Today's one-off bbox resolution plus cached lookups are fine. A product needs its own geocoder or a
  commercial provider.

## Overture Maps (release 2026-10-21.0)

Per-theme licences are from https://docs.overturemaps.org/attribution/, read 2026-11-08.

| theme | licence | local clip check (2026-11-08) | obligations | recommendation |
|---|---|---|---|---|
| **places** | No theme licence; licensed **per source**. CDLA-Permissive-2.0 for Meta, Microsoft, BrightQuery, Uberall, DAC, PinMeTo and others; **Apache-2.0** for Foursquare (NOTICE.txt, "Copyright 2024 Foursquare Labs, Inc.", "transformed to the Overture schema"); **CC0-1.0** for AllThePlaces | 79,385 records: CDLA-P-2.0 73,395 · Apache-2.0 4,864 · CC0 1,126. The `source_licence` field is kept per record. | Attribution: "Overture Maps Foundation" plus "Data from [provider]". Include Foursquare's NOTICE when redistributing those records. CDLA-P-2.0 (https://cdla.dev/permissive-2-0/) puts no conditions on **Results** (analysis outputs). | **APPROVED WITH RESTRICTIONS** (attribution + NOTICE). Needs D5 sign-off: CDLA-P-2.0 is not on the literal MIT/BSD/Apache list. |
| **buildings** | **ODbL**. Sources: OSM (ODbL), Microsoft ML Building Footprints (ODbL), Esri Community Maps (CC BY 4.0), Google Open Buildings (CC BY 4.0) | 393,968 records: OSM 386,529 · Microsoft 7,432 · **Esri 7** · **Google 0** | Same as OSM: share-alike on derived databases, plus credit to Microsoft and Esri. No Google records are present (D3 satisfied); re-check on each refresh. | **APPROVED WITH RESTRICTIONS** (ODbL, see the OSM section) |
| base / transportation / divisions | ODbL (plus CC BY 4.0 sources in divisions) | not ingested | – | n/a |

## Mecklenburg County and NC imagery (found, not ingested)

| source | terms found | recommendation |
|---|---|---|
| **Mecklenburg County aerial2025** (`meckaerial.mecklenburgcountync.gov/.../aerial2025/MapServer`) | Item `licenseInfo` is **empty**; access info is "MeckCoGIS". The county GIS Data Center (https://gis.mecknc.gov/data-center) says: "**Mecklenburg County does not support secondary distribution of this data**". Data is provided "as is" with no warranty. Imagery and LiDAR are not downloadable; they come via the data request form. The service disables tile export (`exportTilesAllowed: false`). | **DO NOT USE UNTIL RESOLVED.** Written permission from the county is needed for any use beyond viewing. NC public-records law (G.S. 132) may make the data obtainable, but G.S. 132-10 / 153A-463-style provisions can still restrict commercial resale of GIS data. Counsel must advise. |
| **City of Charlotte Aerial24** | `licenseInfo` empty. City of Charlotte open data is **CC BY-NC-SA** (`docs/SOURCES.md`): non-commercial and share-alike. | **DO NOT USE UNTIL RESOLVED** (non-commercial licence fails the project rule) |
| **NC OneMap orthoimagery** (NC Orthoimagery Program; `Orthoimagery_20242027` and the 2023 block) | `licenseInfo` says "Please refer to https://www.nconemap.gov/pages/terms". The page is JS-rendered and **unread (unverified)**. Some `/secure/` services return "Token Required". The 2025 service metadata says the pixel values were altered and the imagery is unsuitable for raster analysis. | **HUMAN/LEGAL REVIEW REQUIRED.** Read the NC OneMap terms, and confirm that Mecklenburg is in the 2023 block, before any download. State-produced data is likely a public record, but this is unverified. |
| **NAIP 2024 NC** | public domain, if it exists | **APPROVED** (availability unverified) |

## Derived products in this repo

| product | inputs | licence consequence |
|---|---|---|
| chip index, dense pools, inventory | NAIP + Apache/MIT models | No data-licence obligations. The OpenCLIP caveat is in MODEL_LICENSE_REVIEW. |
| Lake Norman v1 (houses-with-pools, lakefront, docks) | NAIP detections + **Overture buildings (ODbL)** + **OSM water/roads (ODbL)** | **ODbL Derived Database.** Public or commercial distribution of the table triggers share-alike (A2). |
| risk v1 flood | FEMA | public domain |
| risk v1 shore | OSM | ODbL derived |
| Valhalla graph | OSM | ODbL derived. Isochrones are Produced Works and need only attribution. |
| `conf/places.json` bboxes | Nominatim/OSM | ODbL. Insubstantial extract (14 bboxes); attribution kept in `_source`. |

## Attribution block (recommended for UI footer and exports)

> Imagery: USDA NAIP (public domain) via Microsoft Planetary Computer · Elevation: U.S. Geological Survey 3DEP ·
> Flood zones: FEMA NFHL · © OpenStreetMap contributors (ODbL) · Overture Maps Foundation (buildings ODbL; places
> CDLA-Permissive-2.0 / Apache-2.0 [Foursquare] / CC0, data from listed providers) · Microsoft Global ML Building
> Footprints · Esri Community Maps contributors · US Census Bureau
