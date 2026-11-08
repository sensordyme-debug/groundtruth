# Third-party place API terms review

Prepared **2026-11-08** by the engineering agent for human and legal review. **This is not legal advice and not an
approval.** Each verdict is an engineering classification of what can be built safely now. Quotes come from the pages
below as fetched on that date. Where a page could not be read, this review says so.

Context for every item:
- Groundtruth is a hackathon project whose commercial intent is unresolved. ORYN may reuse it later.
- The map is **MapLibre over USDA NAIP imagery**, with OSM-derived boundaries (`conf/places.json`, ODbL). It is not a
  Google map.
- The project forbids data about people.

Verdict scale:
- APPROVED
- APPROVED WITH RESTRICTIONS
- HUMAN/LEGAL REVIEW REQUIRED
- DO NOT USE UNTIL RESOLVED

## Overall

| Provider | Verdict | One-line reason |
|---|---|---|
| Foursquare Places API | **APPROVED WITH RESTRICTIONS** | Usable live with attribution and no server-side caching of attributes. The full EULA on redistribution and derived data has not been fully reviewed |
| Yelp Places API | **HUMAN/LEGAL REVIEW REQUIRED** | Commercial use needs Yelp's written consent. There are strict display rules, a §5(g) geocode/OSM clause that may touch our OSM-based map, and a rule against building a listings database |
| Google Maps Platform | **DO NOT USE UNTIL RESOLVED** | Conflicts with the owner's "no Google data" rule and the "no use with non-Google maps" restriction. Disabled in conf |

---

## Foursquare Places API

Sources:
- Usage guidelines: https://docs.foursquare.com/fsq-developers-places/reference/usage-guidelines
- Self-Service API License Agreement (dated February 29, 2024 per search snippet; not fully read):
  https://foursquare.com/legal/terms/apilicenseagreement
- Pricing: https://foursquare.com/pricing/

| Topic | Finding | Classification |
|---|---|---|
| Caching | `fsq_place_id`: "unlimited caching (solely to improve the performance of your application)". Photo IDs: unlimited. Other attributes: Enterprise customers get "24-hour local-device caching only (no server-based caching is permitted)"; "Pay as You Go & Sandbox Customers: no caching permitted." The code sets `cache_ttl_s: 0` and never persists Foursquare attributes. Investigations that save results should keep only `fsq_place_id` and re-fetch the rest | APPROVED WITH RESTRICTIONS |
| Retention | Same as caching. Saved sessions must not store Foursquare names, addresses or phones beyond the ID. **Engineering follow-up:** check what `api/sessions.py` and investigations persist when a places result is pinned | HUMAN/LEGAL REVIEW REQUIRED (the persistence path is not yet built) |
| Attribution | "credit Foursquare as the source of your data through either visual credit (i.e., buttons, our developer logo, etc.) or contextual credit". The EULA reportedly requires "Powered by Foursquare" wherever Places data appears. Records carry `attribution: "Powered by Foursquare"`; the UI must show it | APPROVED WITH RESTRICTIONS |
| Display (map) | No restriction found on showing Foursquare data on non-Foursquare maps | APPROVED |
| Redistribution | Not addressed in the usage guidelines, and the EULA was not fully read. Do not export Foursquare attributes (GeoJSON export, `export` tool) until reviewed | HUMAN/LEGAL REVIEW REQUIRED |
| Derived data | Not addressed in the guidelines. Resolution clusters are computed in memory per request and not stored | HUMAN/LEGAL REVIEW REQUIRED |
| Commercial use | Self-serve Pay-as-you-Go is a commercial product. The separate Foursquare **Open Source Places** data (Apache-2.0, via Overture, already in `SOURCES.md`) remains the clean option for stored or derived use | APPROVED (API use under paid terms) |
| Reviews / tips | Tips and photos are Premium fields, and both are user-generated. The adapter never requests tips or photos. The Ask endpoint's `justifications` are tip-derived, so they are dropped and only their count is kept | APPROVED WITH RESTRICTIONS |
| People data | No user data is requested | APPROVED |

## Yelp Places API

Sources:
- API Terms of Use (last updated September 22, 2026): https://terms.yelp.com/developers/api_terms/
- Display requirements: https://terms.yelp.com/developers/display_requirements/
- FAQ: https://docs.developer.yelp.com/docs/places-faq

| Topic | Finding | Classification |
|---|---|---|
| Caching | Content may not be stored "for a period longer than twenty-four (24) hours from receipt" (§5(a)), with an exception for "storing Yelp business IDs which you may use solely for back-end matching purposes". The FAQ says "You may cache Yelp Places API content for a maximum of 24 hours." The code keeps an in-memory cache of at most 1 h (`cache_ttl_s: 3600`) and nothing on disk | APPROVED WITH RESTRICTIONS |
| Retention | Beyond 24 h, only business IDs may be kept, and only for matching. Under §4, content must be removed within 24 h on Yelp's request. Saved investigations must not persist Yelp content | APPROVED WITH RESTRICTIONS |
| Attribution and display | Display requirements: <ul><li>"Make Yelp's logo prominently visible on all content that originated from Yelp"</li><li>The logo must link to yelp.com or the business page</li><li>Yelp's own star sprite must be used ("Generic or unbranded stars are not permitted")</li><li>Show ratings "side-by-side with the number of reviews"</li><li>Do not blend Yelp ratings with other sources</li><li>Link directly to Yelp pages, without modifying links (§5(e), §5(q))</li></ul>Records carry `source_url` (the Yelp business URL, unmodified), `rating` and `review_count`. **UI work is required before any Yelp data is shown** | HUMAN/LEGAL REVIEW REQUIRED (UI is not compliant yet) |
| Display (map) | §5(g) forbids using "any geocodes provided in the Yelp Content for use or display in Open Street Maps". It also reportedly covers other open-source map implementations where geocodes would be contributed or licensed under open terms. Our map is MapLibre (open-source software) over NAIP, with OSM-derived boundaries. We do not contribute data to OSM, but whether "display in" an OSM-based or open-source map covers us needs a legal reading | HUMAN/LEGAL REVIEW REQUIRED |
| Combining | §5(c) forbids showing Yelp review ratings "alongside or in conjunction with other user-generated content". The display requirements also forbid blending star ratings. The code never blends ratings: each member keeps its own `rating` and `rating_scale_max`. The UI must not show Yelp ratings next to Foursquare or Google ratings | APPROVED WITH RESTRICTIONS |
| Redistribution | §4: "non-sublicensable, non-transferable". §5(i) forbids copying, renting, selling or sublicensing. §9.2 forbids sharing with third parties, including for AI training. No export of Yelp content. Do not send Yelp content to third-party LLM APIs without review | HUMAN/LEGAL REVIEW REQUIRED |
| Derived data | §5(b) forbids using the content "to update or create your own database of business listing information". §5(d) forbids creating or disclosing "metrics about, or perform any analysis of the API" without consent. Cross-provider clusters are per-request and not stored. Aggregate analysis may be allowed for non-commercial use "only displayed in the aggregate as an analytical output, and not individually" | HUMAN/LEGAL REVIEW REQUIRED |
| Commercial use | §5(d): no use "for any commercial purpose without the express written consent of Yelp". The FAQ says trial calls are "STRICTLY FOR EVALUATION AND NOT FOR COMMERCIAL DEPLOYMENT". If ORYN or any commercial use follows, Yelp's written consent or a commercial agreement is needed | DO NOT USE UNTIL RESOLVED (for commercial use) |
| Reviews | The endpoint returns up to 3 excerpts of about 160 characters and requires the Enhanced or Premium plan. Excerpts need "read more" links (display requirements). The project bans people data, so `user` (name, id, profile, photo) is dropped on receipt. Excerpts are off by default (`store_review_excerpts: false`), leaving only the count and aggregate rating | APPROVED WITH RESTRICTIONS (aggregate only) |
| People data | Reviewer identities are never kept. Business `image_url` is dropped because photos can show people | APPROVED |

## Google Maps Platform (Geocoding, Places API New, Address Validation)

Sources:
- Places policies: https://developers.google.com/maps/documentation/places/web-service/policies
- Geocoding policies: https://developers.google.com/maps/documentation/geocoding/policies
- Terms: https://cloud.google.com/maps-platform/terms and
  https://cloud.google.com/maps-platform/terms/maps-service-terms. **Both pages were truncated by the fetch tool and
  could not be read.** The clause below is quoted from secondary copies (archived versions and third-party appendices)
  and must be checked against the live terms.

| Topic | Finding | Classification |
|---|---|---|
| Owner rule | The project rule is "no Google data" (`docs/HANDOFF.md`, `docs/LICENCE_GATE.md` hard bans). This alone blocks use | DO NOT USE UNTIL RESOLVED |
| Display with a non-Google map | Maps terms §3.2.3, "No Use With Non-Google Maps", in quoted versions: "Customer will not use the Google Maps Core Services with or near a non-Google Map in a Customer Application". One version gives the examples "(i) display Places listings on a non-Google map". The lettering varies between versions ((d) or (e)). Google's EEA page says that, for EEA customers since 8 July 2025, Places content other than lat/lng/place_id "cannot be used With any Map" (the Places UI Kit is exempt). This app's map is MapLibre/NAIP, so showing Google Places or Geocoding results on it appears prohibited. Even returning Google results to a page that contains this map ("with or near") is at risk | DO NOT USE UNTIL RESOLVED |
| Caching | Place IDs: "You can therefore store place ID values indefinitely." Lat/lng: "for up to 30 consecutive calendar days, after which you must delete the cached coordinates." Geocoding policy: "Don't export, extract, or scrape Google Maps Content for use outside the Services." The code sets `cache_ttl_s: 0` | APPROVED WITH RESTRICTIONS (if ever enabled) |
| Retention | Only `place_id` may be kept indefinitely. Coordinates and other content must not be persisted beyond 30 days, and saved investigations must not keep them | APPROVED WITH RESTRICTIONS (if ever enabled) |
| Attribution | "Attribution should take the form of the Google Maps logo whenever possible"; the text "Google Maps" is allowed where space is limited. Third-party attributions in responses must also be shown. Records carry `attribution: "Google Maps"`. The adapter does not yet request `places.attributions`, so add it before enabling | HUMAN/LEGAL REVIEW REQUIRED |
| Redistribution | The terms bar exporting or scraping content outside the Services. No export | DO NOT USE UNTIL RESOLVED |
| Derived data | The terms (from the quoted versions) restrict creating content from Google Maps Content. Cross-provider resolution that merges Google records with others' is derived data | DO NOT USE UNTIL RESOLVED |
| Commercial use | Allowed under paid Maps Platform terms, subject to the restrictions above | n/a while blocked |
| Reviews | Not requested (the `reviews` field is not in the mask) | n/a |
| People data | None requested | APPROVED |

**What would change the Google verdict:**
1. The owner lifts the "no Google data" rule for this specific use.
2. Legal confirms a compliant pattern, for example geocoding-only without display on the map, the Places UI Kit, or
   showing Google results on a Google map.
3. `places.attributions` is added to the mask and rendered.

Until then, `providers.json` keeps `google.enabled = false`. A key alone does not enable it.

## Engineering controls already in place
- **Keys:**
  - Keys come only from the environment or the git-ignored `.env`.
  - They are never logged. The ledger writes whitelisted fields only, and the tests assert that fake keys never appear
    in the ledger, errors or API responses.
  - `/places/providers` exposes presence only.
- **No people data:** no reviewer names, ids or photos, and no business photos.
- **No persistent caching of provider content.** The only cache is Yelp's in-memory cache, kept within its 24 h limit.
- **Attribution travels with every record** (`attribution`, `source_url`). Envelope `limits` restate the display rules.
- **Ratings are never blended.** Each record keeps its provider's scale.
- **Spend caps and a usage ledger limit runaway cost.**

## Open items for the human
1. Decide whether Groundtruth or ORYN use is commercial. If so, contact Yelp for written consent, or drop Yelp.
2. Get a legal reading of Yelp §5(g) (geocodes and OSM or open-source maps) against our MapLibre/OSM-boundary map.
3. Read the Foursquare Self-Service EULA in full for redistribution, derived data and the "Powered by Foursquare"
   placement.
4. Check, in the persistence path being built, that saved investigations and pins keep only provider IDs
   (`fsq_place_id`, Yelp business id, Google place_id) and re-fetch details.
5. Google: keep it disabled unless the owner and legal both clear it.
