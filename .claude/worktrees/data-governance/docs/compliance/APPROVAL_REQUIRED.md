# Decisions required from the owner or counsel

Date: 2026-11-08. These questions need a human decision. Nothing here has been approved. The recommendations are the
reviewer's, offered as input only. Background: `COMMERCIAL_USE_APPROVAL.md`. The place-API terms questions live in
`API_TERMS_REVIEW.md` and are not duplicated here.

### A1. Grounding DINO for commercial (ORYN or product) use
- **Who decides:** counsel. This is the owner's own D1 condition.
- **Question.** The weights are Apache-2.0, but they were trained partly on Objects365 (academic-only) and Flickr30k
  (non-commercial). Does that lineage create risk for commercial deployment?
- **Options.**
  - (a) Accept, on the reading that dataset terms do not bind released weights.
  - (b) Use it only for internal candidate generation and never ship it as a product feature.
  - (c) Replace it with a detector trained on commercially licensed data.
- **Consequence.**
  - (a) Residual contract/copyright risk.
  - (b) and (c) `find_anything` loses open-vocabulary quality or needs a new model.
- **Reviewer recommendation:** (b) until counsel rules.

### A2. ODbL share-alike on building-joined exports
- **Who decides:** owner, with counsel to confirm.
- **Question.** CSV/GeoJSON exports and the Lake Norman derived set combine Overture/OSM buildings (ODbL) with our
  detections. They are Derived Databases. When offered to customers or the public, they must be ODbL or come with the
  method. Which policy applies?
- **Options.**
  - (a) Accept ODbL for those exports, labelled as such.
  - (b) Commercially export only Produced Works: PDF reports, maps, counts.
  - (c) Export only our own NAIP-derived geometry, without OSM or Overture attributes.
- **Consequence.**
  - (a) Customers may redistribute that data freely.
  - (b) and (c) limit what the export button offers.
- **Reviewer recommendation:** (b) or (c) by default, with (a) on request.

### A3. NC OneMap orthoimagery (6-inch, 2023)
- **Who decides:** owner, then counsel if the terms are unclear.
- **Question.** May we download the official county mosaic, clip it to the 8 demo AOIs (about 5–7 GB as COGs) and
  use it commercially? The terms page (https://www.nconemap.gov/pages/terms) could not be read automatically.
- **Options.**
  - (a) The owner reads the terms or emails CGIA, then decides.
  - (b) Wait for NAIP 2024 (public domain) instead.
- **Consequence.** Without this, Groundtruth stays on 2022 imagery, which is about four years old. Also confirm that
  Mecklenburg is in the 2023 block; the service warns against raster analysis on its altered pixels.

### A4. Mecklenburg County aerial2025
- **Who decides:** owner contacts the county; counsel reviews any agreement.
- **Question.** Ask Mecklenburg County GIS (Data Center contact on https://gis.mecknc.gov/data-center) for written
  terms covering AOI use of the 2025 imagery. They currently state "does not support secondary distribution", offer
  no licence, and disable tile export.
- **Options.**
  - (a) Request a data-sharing agreement.
  - (b) Do not use it.
- **Consequence.**
  - (a) Potentially the freshest and sharpest imagery (cache to about 8 cm), but probably non-redistributable.
  - (b) No change.
- **Reviewer recommendation:** (b) unless the county grants written terms.

### A5. OpenCLIP LAION-2B weights in a deployed product
- **Who decides:** owner and counsel.
- **Question.** The licence is MIT. The model card, however, states that "any deployed use case … commercial or not
  … is currently out of scope", and the LAION-2B training set was withdrawn in 2023. Is that acceptable for
  commercial deployment?
- **Options.**
  - (a) Accept, because the licence governs.
  - (b) Evaluate a replacement CLIP trained on cleaner data, such as DataComp; this needs its own licence check.
  - (c) Keep it for the demo only.
- **Consequence.** (b) requires re-embedding the index, about 107k chips × 2 years.
- **Reviewer recommendation:** (c) now, and plan (b).

### A6. pyvalhalla bundled GPL libraries
- **Who decides:** owner, with counsel if distribution is planned.
- **Question.** Will Groundtruth or ORYN ever **distribute** a build, such as a Docker image, installer or appliance?
  The pyvalhalla wheel bundles GPL-2.0+ librttopo and jbigkit.
- **Options.**
  - (a) Hosted-only, with no action.
  - (b) Build Valhalla without spatialite, or with a libtiff that has no JBIG.
  - (c) Switch to OSRM (BSD-2).
- **Consequence.** (a) is fine until a build ships. Then GPL source-offer obligations apply to those libraries.

### A7. Close the open LICENCE_GATE decisions
- **Who decides:** owner.
- **D4.** LGPL-2.1 GEOS. Accept it unmodified and dynamically linked? It is already present through shapely and
  rasterio.
- **D5.** Extend "similarly permissive" to CDLA-Permissive-2.0 (Overture places, already in use) and CC BY 4.0 data
  (7 Esri building records)?
- **Consequence.** If either is not accepted, Overture places or the GEOS-based stack must be replaced.
- **Reviewer recommendation:** accept both, with the conditions in COMMERCIAL_USE_APPROVAL.

### A8. Exposure and authentication posture
- **Who decides:** owner.
- **Question.** Is tailnet-only (`tailscale serve`, no Funnel) with **no authentication** and **CORS `*`** acceptable
  for the demo and any pilot?
- **Options.**
  - (a) Keep it for the demo, and apply security fixes 1–3 from `docs/SECURITY_AUDIT.md`: the `last_text` people
    check, the CORS allowlist, and the places people check.
  - (b) Before any pilot, also add tailnet identity or a token, rate limits and body caps.
- **Consequence.** Without these fixes, any web page that a tailnet user opens can drive the API and use up the
  Places quota.
