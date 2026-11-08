# Place API provider matrix

Date: **2026-11-08**. Code: `engine/groundtruth/places/`. Config: `engine/groundtruth/conf/providers.json`.
Routes: `api/places_api.py` (`POST /api/v1/places/search`, `GET /api/v1/places/providers`, `GET /api/v1/places/usage`).
Terms analysis: `docs/compliance/API_TERMS_REVIEW.md`.

**Test status, honestly:** every adapter is tested only against mocked HTTP (`httpx.MockTransport`). The mock bodies
are shaped from the published schemas. **No live call has ever been made**, because no keys exist yet. The live tests
(`test_live_*` in `tests/test_places_providers.py`) skip until a key is present. A mocked pass does not show that the
live integration works.

## Summary

| | Foursquare | Yelp | Google Maps Platform |
|---|---|---|---|
| Env var(s) | `FOURSQUARE_API_KEY` | `YELP_API_KEY` (`YELP_CLIENT_ID` optional, reported only, never sent) | `GOOGLE_MAPS_API_KEY` |
| Auth | `Authorization: Bearer <service key>` + `X-Places-Api-Version: 2025-06-17` | `Authorization: Bearer <API key>` | `X-Goog-Api-Key` header (Places New, Address Validation). `key=` query param (Geocoding) |
| Default state | enabled, once the key is set | enabled, once the key is set | **disabled** in conf, even with a key |
| Implemented | Place Search, Place Details, Place Match, Ask (discovery only) | Business Search, Business Match, Business Details, Reviews (aggregate only by default), Autocomplete | Places (New) Text Search, Place Details, Geocoding, Address Validation (behind a second flag), `probe_apis()` |
| Used by `search_places` | Place Search | Business Search | Text Search (only if enabled) |
| Mocked tests | pass | pass | pass |
| Live tests | blocked (no key) | blocked (no key) | blocked (no key); the probe also needs a human decision |
| Daily caps (conf) | 300 requests / $5.00 | 200 requests / $3.00 | 100 requests / $2.00 |
| Rate limit (client side) | 10 QPS; Ask 1 QPS | 5 QPS | 5 QPS |
| Response cache | none (`cache_ttl_s` 0) | in memory, 1 h (terms allow at most 24 h) | none |
| Terms verdict | APPROVED WITH RESTRICTIONS | HUMAN/LEGAL REVIEW REQUIRED | DO NOT USE UNTIL RESOLVED |

## Foursquare Places API
- **Base:** `https://places-api.foursquare.com`. This is the new Places API: `fsq_place_id`, plus top-level `latitude` and
  `longitude`.
  Migration guide: https://docs.foursquare.com/fsq-developers-places/reference/migration-guide
- **Endpoints:**
  - `GET /places/search` (`query`, `ne`/`sw` box, `limit` ≤ 50).
    https://docs.foursquare.com/fsq-developers-places/reference/place-search
  - `GET /places/{fsq_place_id}`.
    https://docs.foursquare.com/fsq-developers-places/reference/place-details
  - `GET /places/match` (`name`, `address`, `city`, `cc`, plus optional `state`, `postal_code`, `ll`) returns
    `{place, match_score, context}`.
    https://docs.foursquare.com/fsq-developers-places/reference/place-match
  - `GET /places/ask` (`query`, `ll`, `context`, `location`) returns `{results: [{justifications, place}]}`.
    https://docs.foursquare.com/fsq-developers-places/reference/ask
    The adapter treats Ask as **discovery only**: its records carry `raw.discovery_only = true`. It does not keep the
    justifications, which are derived from user tips, only their count. Ask is never used as a spatial filter.
- **Cost:** https://foursquare.com/pricing/
  - Pro endpoints: $15.00 per 1k calls from call 501 to 100k per month. The first 500 calls are $0; the sandbox page
    also mentions "up to 10,000 free calls on Pro endpoints".
  - Premium: $18.75 per 1k.
  - Ask: $30 per 1k (Pro fields) or $36 per 1k (Premium fields).
  - Field tiers: Pro is `tel`, `website`, `email`, `social_media` plus core fields; Premium is `rating`, `price`,
    `hours`, `tips`, `photos`.
    https://docs.foursquare.com/fsq-developers-places/reference/response-fields
  - By default the adapter omits `fields`, which returns all Pro fields at the Pro price. Setting
    `request_premium_fields` adds `rating` and `price` at the Premium price.
- **Rate limits:** 50 QPS on Pay-as-you-Go. Ask is limited to 1 QPS.
  https://docs.foursquare.com/fsq-developers-places/reference/rate-limits
- **Limitations:**
  - Category filtering is text-only. Foursquare category ids are not mapped, so requested categories are appended to
    the query, and results are mapped back to category keys by whole-word matching on category names.
  - The docs don't describe 429 headers. Retry-After is honoured if the API sends it; otherwise the client uses
    exponential backoff.
  - `source_url` is left null because no public place-page URL is documented in the response.

## Yelp Places API (formerly Fusion)
- **Base:** `https://api.yelp.com/v3`.
  https://docs.developer.yelp.com/docs/fusion-intro
- **Endpoints:**
  - `GET /businesses/search` (`term`, `latitude`/`longitude`, `radius` ≤ 40,000 m, `categories`, `limit` ≤ 50).
    https://docs.developer.yelp.com/reference/v3_business_search
  - `GET /businesses/matches` (`name`, `address1`, `city`, `state`, `country`, plus optional fields).
    https://docs.developer.yelp.com/reference/v3_business_match
  - `GET /businesses/{id}`.
  - `GET /businesses/{id}/reviews`. This needs the Enhanced or Premium plan.
    https://docs.developer.yelp.com/reference/v3_business_reviews
  - `GET /autocomplete`.
    https://docs.developer.yelp.com/reference/v3_autocomplete
- **Search area:** search uses a circle that covers the requested area (half the box diagonal, capped at 40 km). The
  orchestrator then drops any points outside the area.
- **Reviews:**
  - The adapter returns only `total`, `excerpts_returned` and `possible_languages`.
  - Setting `store_review_excerpts: true` (default false) also keeps excerpt `text`, `rating`, `time_created` and `url`.
  - It never keeps `user` (name, id, profile URL, photo). It never keeps business `image_url`, because photos can
    show people.
- **Errors:**
  - A 429 with `ACCESS_LIMIT_REACHED` (daily quota gone) is not retried.
  - Other 429s and 5xx responses are retried, up to 2 retries.
- **Cost and limits:**
  - Yelp sells monthly plans. Yelp's product page lists Base at $229/month, Enhanced at $299 and Premium at $643; I
    found these via search, as Yelp's figures, and did not verify them on the official page.
  - Paid plans include 30,000 calls/month with a daily limit of up to 5,000. The trial gives 5,000 calls over 30 days,
    "strictly for evaluation and not for commercial deployment".
    https://docs.developer.yelp.com/docs/places-faq
  - The conf price of $7.63 per 1k is an **estimate** (Base plan ÷ 30k calls), not a documented per-call price.
  - Overage is billed per 1,000 calls.
- **Limitations:**
  - Yelp returns no `website` field, so it is left null.
  - Business Match "does not return businesses without any reviews". The docs show the match response example as a
    bare array while the schema shows `{businesses: []}`; the adapter accepts both.

## Google Maps Platform
- **Disabled by default.** `providers.json` has `"google": {"enabled": false, "reason": ...}`. There are two reasons:
  - The project owner's rule is "no Google data".
  - The Maps terms forbid using Google Maps content with or near a non-Google map. This app's map is MapLibre over NAIP.
  - See the terms review. The code works when enabled, and that path is tested with mocks.
- **Endpoints:**
  - `POST https://places.googleapis.com/v1/places:searchText`. Takes `X-Goog-FieldMask`, `locationRestriction.rectangle`,
    a single `includedType`, and `pageSize` ≤ 20 (at most 60 results across pages).
    https://developers.google.com/maps/documentation/places/web-service/text-search
  - `GET https://places.googleapis.com/v1/places/{id}`. The field mask has no `places.` prefix here.
    https://developers.google.com/maps/documentation/places/web-service/place-details
  - `GET https://maps.googleapis.com/maps/api/geocode/json`. It returns HTTP 200 with a body `status`;
    `REQUEST_DENIED` maps to auth, and `OVER_QUERY_LIMIT` maps to rate_limited.
    https://developers.google.com/maps/documentation/geocoding/requests-geocoding
  - `POST https://addressvalidation.googleapis.com/v1:validateAddress`. This also needs `address_validation_enabled`.
- **Field masks:** Pro SKU by default (`id`, `displayName`, `formattedAddress`, `location`, `types`, `primaryType`,
  `googleMapsUri`, `addressComponents`). `request_enterprise_fields` adds `rating`, `userRatingCount`, `priceLevel`,
  `nationalPhoneNumber` and `websiteUri`, and the whole request is then billed as Enterprise.
- **Cost:** lowest-tier list prices per 1k (monthly free cap in brackets). The ledger estimates ignore the free caps,
  so they are conservative.
  - Geocoding: $5.00 (10k free)
  - Text Search Pro: $32 (5k free)
  - Text Search Enterprise: $35 (1k free)
  - Place Details Pro: $17 (5k free)
  - Place Details Enterprise: $20 (1k free)
  - Address Validation Pro: $17 (5k free)

  https://developers.google.com/maps/billing-and-pricing/pricing
- **Probe:** `GoogleProvider.probe_apis()` makes one minimal call each to Geocoding, Places New and Address Validation.
  It reports `ok`, `auth`, `rate_limited` and so on per API. Only `test_live_google_probe` uses it, and that test is
  skipped without a key.
  - The probe runs while the provider is disabled, because it only checks which APIs the key can reach.
  - It still spends about 3 calls within the caps.
  - If the owner does not want any Google call at all, do not set `GOOGLE_MAPS_API_KEY` on machines that run pytest.
- **Limitations:** the Geocoding key travels in the query string, so the HTTP helper never logs URLs or exception text.

## Shared behaviour (all providers)
- **Keys:**
  - Keys are read from the process environment, then from the repo-root `.env`, which is git-ignored (see
    `.env.example`).
  - `GET /places/providers` reports only `key_present: true/false`.
  - Error messages are built from status codes and exception class names, never from the URL or the response body.
- **Retries:**
  - Timeout is 10 s. Up to 2 retries with exponential backoff (0.5 s, 1 s, … capped at 8 s).
  - 429 and 5xx honour `Retry-After` up to 20 s. A longer wait is not retried.
  - 401/403 are not retried and report `auth`.
- **Usage ledger:** one row per logical call in `data/app/v1/provider_usage.jsonl`.
  - Fields: `ts`, `provider`, `endpoint` (a logical name, not a URL), `category`, `status`, `http_status`,
    `latency_ms`, `retry_count`, `attempts`, `sent`, `estimated_cost_usd`, `rate_limited`, `cached`,
    `investigation_id`, `error_code`.
  - Only these fields are written: no keys, headers, URLs or bodies. The tests assert this with fake keys.
- **Spend caps:**
  - Before each request the client checks today's (UTC) ledger. If the daily request cap or the estimated USD cap
    would be exceeded, it refuses with `SpendCapExceeded` (code `spend_cap`) and writes a `refused` row.
  - `search_places` skips a capped provider and reports why.
- **Resolution:** described in `engine/groundtruth/places/resolve.py`. Providers may share upstream data, so
  `corroboration` counts providers and does not verify anything.
