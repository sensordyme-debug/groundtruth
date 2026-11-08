# Geospatial validation report: geography resolver

Date: 2026-11-08. Branch: `intelligence/evidence-grounded-investigation`.
Code: `engine/groundtruth/geography.py` (v0.1.0). Data build: `scripts/build_boundaries.py` → `data/boundaries/v1/`.
Tests: `tests/test_geography.py`.

## What it guarantees

A place mention resolves to one of two things:
- a real geometry with a stated `kind`, `source`, `source_date`, `licence` and an `approximate` flag; or
- an explicit "can't resolve" (`geometry=None` plus a `reason`) or an ambiguity (`ambiguous=True`, with `alternatives`).

It never guesses. In particular, "restaurants in Mecklenburg County" always resolves to the county polygon (kind
`county`, ~1 413 km²), and a neighbourhood shown on screen in `context.area` cannot override a place named in the text.

**Likely cause of the original bug (from reading the code; not changed here).** `planner.parse` only knows the
18 `places.json` names, so "Mecklenburg County" left `plan.place = None`. After that, the place shown on the map is
used instead: `planner.py` around line 325 (`place … taken from the map (context)`), and `context.area.place` in
`investigate.compile_plan` around lines 248–253. If the lead calls `geography.resolve(objective, context)` first and
uses its geometry as the scope, this path is closed.

## Boundary data (`data/boundaries/v1/`, EPSG:4326, built 2026-11-08T07:14:53-0500)

Every feature carries `name`, `kind`, `source`, `source_date`, `licence` and `source_id`. `meta.json` holds sources,
counts, sha256 checksums and the cross-check numbers.

| Layer (file) | Count | Source | Source date | Licence |
|---|---|---|---|---|
| county.geojson | 1 | US Census Bureau TIGERweb `State_County/MapServer/1` (GEOID 37119), copied from `data/boundary/mecklenburg.geojson` | 2026-11-07 (retrieved) | Public domain (US Census) |
| municipalities.geojson | 7 | 5 from OSM `boundary=administrative` + `admin_level=8` relations, from the local Geofabrik NC pbf. Cornelius and Davidson from US Census TIGERweb Incorporated Places `Places_CouSub_ConCity_SubMCD/MapServer/4` (current vintage) | OSM 2026-11-06; TIGERweb 2026-11-08 | ODbL 1.0 (OSM); public domain (Census) |
| neighbourhoods_bbox.geojson | 18 | `engine/groundtruth/conf/places.json` (Nominatim bboxes), kind `neighbourhood_bbox`, `approximate=true` | 2026-11-07 | ODbL 1.0 |
| neighbourhoods_osm.geojson | 114 | OSM `place=neighbourhood/suburb/quarter` areas whose representative point is in the county (NC pbf) | 2026-11-06 | ODbL 1.0 |
| landmarks.geojson | 1 | UNC Charlotte campus outline (`data/layers/osm_unc_charlotte_campus.geojson`, OSM) | 2026-11-06 | ODbL 1.0 |
| zcta.geojson | 36 | US Census 2020 ZCTAs, TIGERweb `PUMA_TAD_TAZ_UGA_ZCTA/MapServer/11`, queried by the county envelope, clipped to the county. Only `ZCTA5`/`GEOID` are kept (no population fields) | 2026-11-08 (retrieved; 2020 vintage) | Public domain (US Census) |
| water_bodies.geojson | 3 | Full OSM water polygons, not clipped: Lake Norman (relation/299512), Lake Wylie (299167, 302213), Mountain Island Lake (299166) | 2026-11-06 | ODbL 1.0 |
| outside_municipalities.json | 56 names | OSM admin_level=8 places near the county with less than 50 % of their area inside it (Gastonia, Concord, Mooresville, …) | 2026-11-06 | ODbL 1.0 |

URLs:
- Geofabrik NC extract: https://download.geofabrik.de/north-america/us/north-carolina.html. The local copy is
  `data/osm/north-carolina.osm.pbf`, tag-filtered to `data/boundaries/v1/work/boundaries_filtered.osm.pbf` (4.7 MB).
- TIGERweb: https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/ (the three layers named above).

The ZCTA download is a bounded REST query of about 60 features, not the national ZCTA file (more than 500 MB), so it
was well under the 50 MB limit. No TIGER/Line zip files were downloaded.

Build cost:
- Time: 40 s wall clock, most of it the 35 s tag-filter pass over the 431 MB NC pbf.
- Peak memory: 2.1 GB RSS.
- Output: 4.0 MB of layers plus the 4.7 MB work pbf.
- Machine at build time: 61 GB free disk, 15 GB RAM available.

### Cross-checks (from meta.json)

**County.** IoU between the TIGERweb county and the OSM `admin_level=6` Mecklenburg relation: **0.9985**.

**Municipalities.** The rule: use OSM; switch to TIGER when OSM is missing or IoU(OSM, TIGER) < 0.9.

| Town | OSM relation | Census GEOID | IoU OSM vs TIGER | Used | Area km² | Fraction in county |
|---|---|---|---|---|---|---|
| Charlotte | 177415 | 3712000 | 0.9959 | OSM | 815.9 | 0.9999 |
| Cornelius | 179854 | 3714700 | **0.8300** | TIGER | 34.05 | 1.0 |
| Davidson | 179858 | 3716400 | **0.8885** | TIGER | 17.50 | 0.9466 |
| Huntersville | 179853 | 3733120 | 0.9901 | OSM | 109.30 | 1.0 |
| Matthews | 176954 | 3741960 | 0.9975 | OSM | 44.49 | 0.9999 |
| Mint Hill | 179857 | 3743480 | 0.9952 | OSM | 64.06 | 0.9955 |
| Pineville | 176951 | 3752220 | 0.9943 | OSM | 17.25 | 1.0 |

Notes on the two TIGER towns:
- Cornelius: OSM is 30.9 km² and TIGER is 34.1 km². 4.6 km² is in TIGER only, most of it on land, which suggests
  annexations newer than the OSM mapping.
- Davidson: OSM is 16.8 km² and TIGER is 17.5 km².
- Both towns keep their OSM id in `same_as`, so the resolver still treats the Nominatim bbox of the same relation as
  the same town.

**Water bodies.** Lake Norman is 127.65 km² in total, of which 32.30 km² is inside the county. The county-clipped
copy
in `data/risk/v1/shore` ends at the county line, which runs across the lake. That copy would measure "within
5 miles" from an artificial edge, so it is not used.

**ZCTAs.** 60 intersected the county envelope:
- 24 were dropped as having no real overlap. 23 had 0 km² in the county; the 24th was 28027 with 0.024 km².
- 36 were kept, 10 of them clipped at the county line.

## Benchmark results (actual run)

Command:

```
GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data bash scripts/gt.sh pytest -v tests/test_geography.py
```

Result: **74 passed in 2.98 s.** The full suite (`pytest -q`) gave 180 passed and 3 skipped in 8.0 s, with nothing
else broken.

### Containment: inclusion and exclusion with real coordinates

Every point below was checked against the layers in `data/boundaries/v1`.

| Case | Query | Point (lon, lat) | Expected | Result |
|---|---|---|---|---|
| county:uptown-in | Mecklenburg County | Trade & Tryon (-80.8431, 35.2271) | in | PASS |
| county:gastonia-out | Mecklenburg County | downtown Gastonia (-81.1873, 35.2621) | out | PASS |
| county:concord-out | Mecklenburg County | downtown Concord (-80.5795, 35.4088) | out | PASS |
| charlotte:uptown-in | Charlotte | Trade & Tryon | in | PASS |
| charlotte:huntersville-dt-out | Charlotte | downtown Huntersville (-80.8428, 35.4107) | out | PASS |
| charlotte:matthews-dt-out | Charlotte | downtown Matthews (-80.7237, 35.1168) | out | PASS |
| myers-park:queens-univ-in | Myers Park | Queens University (-80.8318, 35.1888) | in | PASS |
| myers-park:uptown-out | Myers Park | Trade & Tryon | out | PASS |
| myers-park:southpark-mall-out | Myers Park | SouthPark Mall (-80.8295, 35.1534) | out | PASS |
| huntersville:downtown-in | Huntersville | downtown Huntersville | in | PASS |
| huntersville:uptown-out | Huntersville | Trade & Tryon | out | PASS |
| huntersville:davidson-college-out | Huntersville | Davidson College (-80.8445, 35.5005) | out | PASS |
| matthews:downtown-in | Matthews | downtown Matthews | in | PASS |
| matthews:uptown-out | Matthews | Trade & Tryon | out | PASS |
| matthews:mint-hill-out | Matthews | Mint Hill centre (-80.6473, 35.1796) | out | PASS |
| lake-norman:water-in | Lake Norman | open water (-80.9100, 35.5050), 838 m from shore | in | PASS |
| lake-norman:20km-south-out | Lake Norman | the same point moved 20 km south (exact UTM offset) | out | PASS |
| 5mi-lake-norman:cornelius-3km-in | within 5 miles of Lake Norman | Cornelius (-80.8580, 35.4600), 2 984 m from shore | in | PASS |
| 5mi-lake-norman:huntersville-6km-in | within 5 miles of Lake Norman | downtown Huntersville, 6 225 m from shore | in | PASS |
| 5mi-lake-norman:uptown-out | within 5 miles of Lake Norman | Trade & Tryon, 23 258 m from shore | out | PASS |
| zip-28202:romare-bearden-park-in | 28202 | Romare Bearden Park (-80.8487, 35.2272) | in | PASS |
| zip-28202:trade-and-tryon-out(28280) | 28202 | Trade & Tryon is ZCTA 28280 (a unique ZIP) | out | PASS |
| zip-28202:matthews-out | 28202 | downtown Matthews | out | PASS |
| restaurants-county:matthews-in | restaurants in Mecklenburg County | downtown Matthews | in | PASS |
| restaurants-county:gastonia-out | restaurants in Mecklenburg County | downtown Gastonia | out | PASS |

### Behaviour tests

| Test | What it checks | Result |
|---|---|---|
| restaurants_in_county_is_the_county_not_myers_park | 4 phrasings give kind `county`, not approximate or ambiguous, source TIGERweb. With `context.area.place="Myers Park"` it is still the county (1 413 km² ± 2). | PASS |
| charlotte_is_the_city_not_the_county | "Charlotte" gives kind `city`, smaller than 0.9 × the county. "the Charlotte area" gives the city, `ambiguous`, with the county as an alternative. | PASS |
| every_municipality_resolves_to_its_boundary (×7) | Each of the 7 towns has the right kind, `approximate=false`, source, date and licence, and more than 50 % of its area in the county. | 7/7 PASS |
| matthews_bbox_is_superseded_not_ambiguous | The Nominatim bbox (same OSM relation) is listed as `superseded`, and the result is not ambiguous. | PASS |
| lake_norman_is_ambiguous_water_vs_area | Gives `water_body` (the full reservoir, more than 100 km²), `ambiguous` against the lakeside-area bbox. "the Lake Norman area" gives the bbox reading, not ambiguous. | PASS |
| myers_park_is_an_approximate_neighbourhood | kind `neighbourhood`, `approximate=true`, and the reason says APPROXIMATE. | PASS |
| within_5_miles_of_lake_norman_measures_from_the_water_edge | radius = 8 046.72 m, measured from the edge. Walking east in 50 m steps: every point below 0.99 r is inside (in the display polygon too), and the first point above 1.01 r is outside. | PASS |
| radius_2km_point_exact_within_1_percent | On 12 bearings, 1 980 m is in and 2 020 m is out. Display-polygon area = π r² ± 1 %. Same result for the text form "within 2 km of 35.2271, -80.8431". | PASS |
| radius_units (×5) | m, km, mile, ft, "half a mile" | 5/5 PASS |
| address_with_stub_geocoder | Gives a Point with the stub's exact coordinate and source. "within 1 km of <address>" includes 900 m and excludes 1 100 m. | PASS |
| address_without_geocoder_is_ambiguous_with_no_coordinate | kind `address`, `ambiguous`, geometry `None`, reason "address geocoding unavailable". `contains` raises. | PASS |
| geocoder_bad_or_conflicting_answers_are_not_guessed | NaN, San Francisco or `None` from the geocoder: no geometry. Two candidates far apart: ambiguous, highest score used. | PASS |
| counted_nouns_are_not_addresses | "3 restaurants on South Blvd in Matthews" gives Matthews. | PASS |
| custom_polygon_inclusion_exclusion_and_boundary | bbox in and out; a point exactly on an edge or vertex is IN (closed set); 1e-7° outside is OUT; a context GeoJSON triangle. | PASS |
| invalid_polygons_are_rejected_or_repaired_with_a_note | Rejected: west > east, lat 95, a Point. A bow-tie is repaired, with a note. | PASS |
| crs_roundtrip_error_below_1cm (×6) | 4326 → 32617 → 4326 error < 0.01 m, including (-84, 30), which is outside zone 17's design area | 6/6 PASS |
| invalid_coordinates_rejected (×7) | NaN, inf, lat 95, lon 200, "abc", `None` all raise `InvalidCoordinate`, including inside `contains` | 7/7 PASS |
| coordinates_outside_coverage_and_swapped | (0, 0) is outside coverage; a swapped lat/lon is detected; Gastonia is valid (near the county). | PASS |
| distance_m | A 3-4-5 offset gives 5 000 m (rel 1e-6); a point inside Charlotte is 0; uptown to Gastonia is ≈ 31.5 km ± 1 %. | PASS |
| zip | 28202 gives a ZCTA (approximate). 28280 resolves. 90210 and the PO-box ZIP 28201 do not resolve. | PASS |
| places_outside_the_county_are_not_guessed | Gastonia, Gaston County and Davidson County give no geometry, reason "outside Mecklenburg County". | PASS |
| nested_places_pick_the_most_specific_… | "Myers Park, Charlotte" gives Myers Park. "Huntersville, Mecklenburg County" gives Huntersville. "Matthews and Mint Hill" is ambiguous. | PASS |
| common_word_neighbourhood_names_need_a_locative | "cherry trees" does not resolve; "pools in Cherry" gives Cherry. Ravenwood (2 polygons) is ambiguous. | PASS |
| no_place_is_unresolved_not_a_default | With no place there is no silent default. A context bbox is used when the text names no place. | PASS |
| neighbourhood_upgrades_and_same_name_conflicts | Uptown and University City use exact OSM polygons, not ambiguous. South End is ambiguous (see below). | PASS |
| filter_features_points_and_polygons | Points and a polygon kept in input order; NaN and null geometries dropped; radius with `intersects`; `points_mask`. | PASS |
| deterministic | `to_dict()` is identical across calls (also proves it is JSON-serialisable). | PASS |
| every_layer_feature_has_provenance | Every feature has name, kind, source, source_date and licence, a valid geometry and 4326 bounds; every meta source has a licence. | PASS |

### Example resolutions and latency (warm, single call, CPU)

| Text | kind | name | status | approximate | ms |
|---|---|---|---|---|---|
| `restaurants in Mecklenburg County` | county | Mecklenburg County | resolved | False | 7 |
| `parks in Charlotte` | city | Charlotte | resolved | False | 9 |
| `the Charlotte area` | city | Charlotte | ambiguous | False | 8 |
| `Myers Park` | neighbourhood | Myers Park | resolved | True | 3 |
| `Matthews` | municipality | Matthews | resolved | False | 3 |
| `Lake Norman` | water_body | Lake Norman | ambiguous | False | 67 |
| `homes in the Lake Norman area` | neighbourhood | Lake Norman | resolved | True | 27 |
| `pools within 5 miles of Lake Norman` | radius | within 5 miles of Lake Norman | ambiguous | False | 213 |
| `South End` | neighbourhood | South End | ambiguous | False | 3 |
| `28201` | zip | 28201 | unresolved | False | 2 |
| `restaurants in Gastonia` | – | Gastonia | unresolved | False | 2 |
| `123 Main St, Huntersville` (no geocoder) | address | 123 main st, huntersville | ambiguous | False | 2 |
| `find swimming pools` | – | – | unresolved | False | 2 |

## How ambiguity is handled (explicit rules)

1. **Text beats context.** A place named in the text beats `context.area` (place, bbox or GeoJSON). The context is
   used only when the text names no place. With neither, the result is unresolved; there is no silent county default.
2. **Several places in the text.** If they nest (each one is at least 90 % inside the next), the most specific is used
   and the others are recorded in `detail.within`. If they do not nest, the first is used, `ambiguous=True`, and the
   others are listed. "within N of X … in Y" applies the radius to X only, lists Y as "NOT applied", and is marked
   ambiguous.
3. **Several readings of one name.** Layer priority decides: county > city/municipality > water body > landmark >
   OSM neighbourhood polygon > Nominatim bbox. Wording can select a reading ("Lake Norman area" means the bbox;
   "town of Matthews" means the municipality).
4. **When the result is ambiguous.**
   - It is ambiguous only when another reading is a *different* entity whose IoU with the chosen geometry is below
     0.5. Examples:
     - South End: the OSM polygon relation/13268943 covers 0.34 km²; Nominatim's South End is node/4780150588.
     - Ravenwood: two OSM polygons share the name.
     - Mineral Springs: an OSM suburb inside the county, and a town of that name in Union County.
   - It is also ambiguous when the alternative is a different *kind of thing*: Lake Norman's water polygon against
     the lakeside-area bbox.
   - An approximate bbox of the **same** OSM object as the chosen exact polygon is listed with `superseded=true` and
     does not make the result ambiguous (Matthews and the other towns, Uptown, University City, UNC Charlotte).
   - **Matthews is a deliberate choice, not an oversight.** The brief gave "Matthews" as an ambiguity example.
     Here the municipality and the places.json bbox are the same OSM relation (176954), so the bbox is reported as
     a superseded approximation. Flagging that as ambiguous would ask "which do you mean?" about two
     approximations of one town.
5. **Common words as names.** OSM-only neighbourhood names ("Cherry", "Gateway", "Midtown", …) and towns outside the
   county ("Faith", "Locust", …) count only after a locative word (in, near, around, of, at, …) or when they are the
   whole request.
6. **Alternatives.** Each alternative carries a `ref` ({layer, name, source_id}). Passing it back as
   `resolve({"ref": ...})` selects that reading exactly, which a "which did you mean?" UI can use.

## Known limits

- **Neighbourhoods are mostly bounding boxes.**
  - Of the 18 `places.json` names, 7 are still approximate bboxes (`approximate=true`): Ballantyne, NoDa,
    Myers Park, Plaza Midwood, Dilworth, SouthPark and Steele Creek.
  - These were upgraded to exact polygons: Uptown and University City (OSM, same object), UNC Charlotte (campus
    outline), the 6 towns (municipal boundaries) and Lake Norman (water).
  - Myers Park's bbox is about 3.6 × 4.4 km, so it over-includes neighbouring areas.
  - OSM's "Dilworth Historic District" polygon exists but is not used for "Dilworth" (different name).
  - The Charlotte Neighborhood Profile Areas were not used: they are not available locally, and their licence was
    not checked.
- **ZIP is ZCTA.**
  - 2020 Census ZCTAs approximate USPS ZIP delivery areas, so every ZIP result is `approximate=true`.
  - PO-box-only ZIPs (for example 28201) have no ZCTA and stay unresolved.
  - Some unique ZIPs do have ZCTAs: 28280 covers the Trade & Tryon block.
  - Only ZIPs in the NC range 27006–28909, or numbers written as "zip NNNNN", are treated as ZIPs.
- **Radius.**
  - Containment uses the exact EPSG:32617 distance to the anchor (`shapely.dwithin`).
  - The returned `geometry` is a display/prefilter polygon. For anchors with more than 2 000 vertices (the lakes) it
    is built by stepwise buffering with 5 m simplification, so it is a superset of the exact set by at most
    `detail.display_polygon_margin_m` (20 m for 5 miles of Lake Norman). This brought the build from 14.6 s to
    about 0.2 s.
  - The buffer is not clipped to the county.
  - "near X" is not turned into a radius here. Travel time ("within 10 minutes of") is not a radius and is left to
    the planner and routing.
- **Lake Norman** is mostly outside the county (32 of 128 km² inside). Measuring from its full shoreline is the
  intent; `detail.area_in_county_km2` records the split.
- **Addresses.**
  - Detection is a deterministic regex: a house number, up to 5 words, then a street suffix. Words such as
    "on", "in" or "of" between the number and the suffix reject a match ("3 restaurants on South Blvd"), so a
    name like "1 Bank of America Plaza" is not detected.
  - Geocoding is entirely the injected geocoder's job. Its points must be finite and within 50 km of the county
    envelope, and candidates more than 50 m apart make the result ambiguous.
- **Coordinates in text** are read as "lat, lon" first; "lon, lat" is accepted only if that is the only valid order.
- **Municipal boundaries change.** Cornelius and Davidson come from the current TIGERweb vintage and the others from
  OSM 2026-11-06. The IoU agreement between the two sources is in meta.json.
- **"and" / "or" between places** is reported as ambiguous; no union is built.
- **Data version.** The resolver reads `config.data_dir("boundaries")`, which defaults to `v1`. There is no
  `boundaries` key in `conf/data_versions.json` yet; the lead can add one.

## Reproduce

```
# build (refuses to overwrite an existing v1 unless --force; the NC pbf filter result is cached in v1/work/)
GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data bash scripts/gt.sh python scripts/build_boundaries.py
# test
GROUNDTRUTH_DATA=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data bash scripts/gt.sh pytest -v tests/test_geography.py
```
