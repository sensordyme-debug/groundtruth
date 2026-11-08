"""Builds docs/RED_GREEN_MASTER_AUDIT.md from the ledger below. Each item is (area, requirement, status, evidence).
Status: GREEN (implemented + verified, evidence cited) · YELLOW (implemented, verification incomplete) · RED (not done)
· BLOCKED (needs something only the owner can provide: keys, legal sign-off, console access) · DEFERRED (deliberately
postponed, reason given). Counts are computed from the rows; nothing is hand-totalled."""
from collections import Counter
from pathlib import Path

L = [
    # ---- API integrations (Part 3, Part 20)
    ("API", "Foursquare client (Search, Details, Match, Ask; auth + version header)", "GREEN", "engine/groundtruth/places/foursquare.py; mocked tests in tests/test_places_providers.py (50 pass)"),
    ("API", "Foursquare real authentication tested", "GREEN", "live test passed 2026-11-08 with the owner's key; live search returned listings (keys never printed)"),
    ("API", "Yelp client (Search, Match, Details, Reviews count/rating, Autocomplete; Bearer key, Client ID never sent)", "GREEN", "places/yelp.py; mocked tests"),
    ("API", "Yelp real authentication tested", "GREEN", "live test passed 2026-11-08; live search returned listings"),
    ("API", "Google client (Places New Text Search/Details, Geocoding, Address Validation flag, probe)", "GREEN", "places/google.py; mocked tests; disabled by default (owner rule + non-Google-map terms)"),
    ("API", "Google real authentication + enabled-API inspection", "GREEN", "probe 2026-11-08: Places (New) ok; Geocoding + Address Validation return auth (not enabled on the key); provider stays disabled by policy"),
    ("API", "Provider errors handled (401/403 no retry, 5xx, timeouts)", "GREEN", "tests/test_places_providers.py"),
    ("API", "Rate limits handled (429 + Retry-After, per-provider limiter)", "GREEN", "places/base.py; mocked 429-with-retry tests"),
    ("API", "Usage ledger (provider, endpoint, status, latency, retries, est. cost, no secrets)", "GREEN", "data/app/v1/provider_usage.jsonl; test asserts the key never appears in the ledger"),
    ("API", "Spend safeguards (daily request + USD caps)", "GREEN", "conf/providers.json; spend-cap refusal test"),
    ("API", "Attribution + terms documented", "GREEN", "docs/compliance/API_TERMS_REVIEW.md, docs/API_PROVIDER_MATRIX.md"),
    ("API", ".env.example placeholders; .env git-ignored; no secrets committed", "GREEN", ".env.example; .gitignore; history scan of 80 commits: 0 hits (docs/SECURITY_AUDIT.md)"),
    ("Places", "Unified PlaceRecord with nullable fields + provider extras kept separately", "GREEN", "places/base.py PlaceRecord"),
    ("Places", "Cross-provider deduplication (distance + name/address; similar names apart never merged)", "GREEN", "places/resolve.py; tests; live run found a bar/restaurant category mismatch blocking a true match: fixed with category families + regression test"),
    ("Places", "Provider selection + per-provider failure isolation (one fails, others return; partial)", "GREEN", "places/orchestrate.py; tests/test_places_orchestrate.py"),
    ("Places", "Provider results validated against the requested geography; invalid coordinates dropped, never invented", "GREEN", "orchestrate.py; orchestrate tests"),
    ("Places", "Corroboration ≠ verification stated; provider data labelled provider-reported", "GREEN", "resolve.py docs; feature provenance 'provider_reported'; UI badge"),
    ("Places", "People-data policy before any provider call", "GREEN", "api/places_api.py; tests/test_places_policy.py"),
    ("Places", "Provider data inside investigations (optional step, skipped when no key)", "GREEN", "live investigation 'restaurants in Myers Park': 30 Foursquare/Yelp listings as provider-reported findings"),
    ("Places", "Yelp display requirements (logo, stars, unmodified links) in the UI", "BLOCKED", "owner/legal: official Yelp logo + star sprite assets are not installed, and §5(g) (Yelp geocodes on an open-source map) needs a legal reading. Meanwhile Yelp display is gated OFF (conf/providers.json display_enabled=false): investigations never show Yelp content and state why (test_yelp_never_shown_while_display_gated)"),
    # ---- Geography (Part 5)
    ("Geography", "County searches use the county boundary (never a neighbourhood)", "GREEN", "geography.py; tests/test_geography.py; tests/test_investigate.py::test_county_never_falls_back_to_the_map_area"),
    ("Geography", "City searches (Charlotte = city limits, not county)", "GREEN", "test_geography; test_city_zip_radius_and_outside"),
    ("Geography", "Municipalities (Huntersville, Matthews, Cornelius, Davidson, Mint Hill, Pineville)", "GREEN", "data/boundaries/v1 (OSM / Census, IoU-checked); tests"),
    ("Geography", "Neighbourhood searches (exact where mapped; approximate boxes disclosed)", "GREEN", "114 OSM neighbourhoods + 18 boxes flagged approximate; plan limitation text"),
    ("Geography", "ZIP searches", "GREEN", "36 Census ZCTAs (approximate vs USPS, disclosed); test"),
    ("Geography", "Radius searches with real metric distance (incl. from a lake's edge)", "GREEN", "within N miles/km/m; tests ±1 %"),
    ("Geography", "Custom polygon searches (inclusion, exclusion, boundary point)", "GREEN", "test_geography; area chip draw → geojson scope"),
    ("Geography", "Coordinate validation (NaN, out of range, far from county)", "GREEN", "validate_coordinate tests"),
    ("Geography", "CRS transformations tested (round trip < 1 cm)", "GREEN", "test_geography"),
    ("Geography", "Spatial intersections / containment tested", "GREEN", "test_geography; exact-boundary clipping evidence in investigations"),
    ("Geography", "Ambiguity disclosed (Lake Norman water vs area box, South End, …)", "GREEN", "Resolution.ambiguous + plan limitations"),
    ("Geography", "Outside-county places answered, never guessed (e.g. Gastonia)", "GREEN", "test_city_zip_radius_and_outside"),
    ("Geography", "Street addresses resolved", "BLOCKED", "needs a geocoder: Google Geocoding is not enabled on the key and Google is disabled by policy; without one the resolver returns 'address geocoding unavailable', never a coordinate"),
    ("Geography", "Shore-distance phrases stay conditions, not radius scopes", "GREEN", "test_shore_distance_is_a_condition_not_an_area"),
    # ---- Investigation engine (Part 6–7)
    ("Investigation", "Objectives become validated typed plans", "GREEN", "investigate.py InvestigationPlan; 4 rejection tests"),
    ("Investigation", "Dependency-aware execution", "GREEN", "test_failed_step_blocks_dependents"),
    ("Investigation", "Partial failure handled", "GREEN", "bench: Ballantyne derived missing → partial"),
    ("Investigation", "Timeouts enforced (dependents blocked)", "GREEN", "test_step_timeout_blocks_dependents"),
    ("Investigation", "Cancellation", "GREEN", "cooperative: the running step finishes, later steps are skipped, status 'cancelled' (test_cooperative_cancellation_stops_between_steps); cancel on a finished investigation is a no-op; a tool already running can't be killed mid-call (Python threads), stated in the API reply"),
    ("Investigation", "Findings link to evidence (every number traceable)", "GREEN", "bench_investigations: evidence 12/12"),
    ("Investigation", "Reports reproducible", "GREEN", "bench determinism 12/12; report.md; demo_check v3 report check"),
    ("Investigation", "Denominator integrity (passing / failing / unknown; unknown never counted)", "GREEN", "test_denominator_integrity_unknown_stays_unknown"),
    ("Investigation", "Flagship evidence: building geometry, pool detection, imagery source + flight dates", "GREEN", "evidence e3/e4 (NAIP 2022 flights 06-02/06-22/09-27); demo_check v3"),
    ("Investigation", "Flagship evidence: FEMA zone geometry, source and EFFECTIVE date", "GREEN", "every result carries firm_panel + flood_map_effective from FEMA NFHL layer 3 (192 panels, effective 2009-03-02…2018-11-16, data/firm_panels/v1, scripts/fetch_firm_panels.py); the flood evidence lists the effective dates of the kept results; registry effective_date ≠ fetch date (tests/test_provenance_dates.py)"),
    ("Investigation", "Flagship evidence: elevation source + date; missing data identified", "GREEN", "3DEP 2016 (published 2019); 7 of 20 BFE unavailable reported as a gap"),
    ("Investigation", "Ranking explained (documented ordering, not a score)", "GREEN", "rank step evidence; conf rank_rule"),
    ("Investigation", "Predicted vs verified distinguished (pool prediction ≠ confirmed pool)", "GREEN", "finding nature 'AI prediction' chips; sheet badge 'AI prediction · not human-verified'"),
    ("Investigation", "Flagship headline strength above 'weak' (derived v1 precision re-measured)", "BLOCKED", "needs human reviews in the Validation Studio (0 of 498). Headline quality itself was rebuilt from evidence: scope, 'AI-detected candidates (none human-verified)', count with denominator, unknowns kept apart, ranking rule, limits; every number traced to evidence (test_headline_quality_and_consistency, test_headline_never_repeats_the_unknowns); follow-up set naming fixed after a live probe found '12 of 12' next to '12 of the 13'"),
    # ---- Validation Studio (Part 8)
    ("Validation", "Image viewer with zoom/pan + AI overlays", "GREEN", "Studio on the live map; scripts/studio_check.py PASS; screenshots 09/10"),
    ("Validation", "Review queue with class + region filters and progress", "GREEN", "studio_check: filter changes queue; progress '0 of 498 reviewed'"),
    ("Validation", "Accept / reject / uncertain / needs review recorded with history", "GREEN", "api/validation_api.py; tests/test_validation.py (journal, latest-wins, history)"),
    ("Validation", "Same decisions submitted from the UI", "GREEN", "scripts/studio_check.py clicks Accept in a real browser and checks the POST body {sample_id, decision} and the advance to the next sample; the POST is intercepted in the browser so no review is stored (reviewed count 0 → 0)"),
    ("Validation", "Draw / correct polygon", "GREEN", "scripts/studio_check.py draws a 4-vertex polygon on the map, saves it, and checks a closed 5-point ring posted as 'corrected' (intercepted, never stored); screenshot docs/media/v3/ui/11_validation_polygon_1440x900.png"),
    ("Validation", "Labels persist separately; training labels untouched", "GREEN", "data/validation/v1 journal + snapshot + backups; test hashes data/labels unchanged"),
    ("Validation", "Geographically separated samples (north / south / central holdouts)", "GREEN", "498 samples; script asserts no leakage; label-cluster flag"),
    ("Validation", "Pools + buildings, then parking / docks / solar", "GREEN", "190 pool, 176 building, 60 parking, 60 solar, 12 dock samples"),
    ("Validation", "Metrics: precision + Wilson CI, FDR, IoU, calibration, per region/class; recall only with a census", "GREEN", "validation.metrics; tests on synthetic sets; census tiles"),
    ("Validation", "Human-reviewed precision / recall numbers", "BLOCKED", "0 reviews: needs a human reviewer (the Studio is ready)"),
    ("Validation", "Export evaluation labels (GeoJSON / CSV)", "GREEN", "GET /validation/export; tests"),
    # ---- Data + imagery (Part 9)
    ("Data", "Source registry with acquisition / publication / ingestion dates, licence, freshness", "GREEN", "conf/sources.json (22 sources); docs/DATA_SOURCE_REGISTRY.md"),
    ("Data", "Freshness shown per layer and per source in the UI", "GREEN", "layers popover source lines; 'i' panel; /api/v1/sources"),
    ("Data", "Data coverage visible + missing data disclosed", "GREEN", "check_coverage step; gap findings"),
    ("Data", "Newer imagery investigated (county 2025, city 2024, NC OneMap 2023, NAIP 2024)", "GREEN", "DATA_SOURCE_REGISTRY: endpoints, dates, licences"),
    ("Data", "Newer imagery ingested", "BLOCKED", "county/city: no licence (DO NOT USE); NC OneMap: terms review A3; NAIP 2024 for NC unverified"),
    ("Data", "Provenance bugs (Overture date empty; OSM dated by file mtime)", "GREEN", "providers/public.py registry_date(): Overture = release 2026-10-21.0, OSM = data timestamp 2026-11-06, 'unavailable' when the registry has none, never a file mtime (tests/test_provenance_dates.py, 7 pass)"),
    ("Data", "NAIP scene seams", "RED", "mosaic not built (docs/MOSAIC.md: dodging step unfinished)"),
    ("Data", "New data versions registered (boundaries, validation) + manifest", "YELLOW", "data_versions.json updated; DEMO_MANIFEST not extended (derived, risk, lidar v2, boundaries)"),
    # ---- CV + refinement (Part 10–11)
    ("CV", "Find like this: rejecting a result refines without collapsing", "GREEN", "eval_point_find: parking 31→24→17 (OSM P 0.38→0.42→0.47); before: 70→3"),
    ("CV", "Find like this: unrelated objects controlled (per-click calibration)", "GREEN", "Lake Norman 7,137→1,160; pool eye 23/24"),
    ("CV", "Refinement improvement verified by humans", "BLOCKED", "needs human reviews in the Validation Studio (0 reviews); no automated or invented reviews"),
    ("CV", "Similarity explainable in the UI", "GREEN", "result sheet 'Why it matched': similarity vs the area threshold (+ its background quantile from the API), margin, 'not this' distance, and 'a resemblance, not proof'; browser check scripts/similarity_check.py 6/6, screenshot docs/media/v3/ui/similarity_why.png"),
    ("CV", "Vision metrics measured with geographic holdouts", "BLOCKED", "reporting done: metrics by_holdout_group (tests) and a holdout table in the Studio (studio_check: listed, 'not measured' until reviewed); the numbers need human reviews (0 of 498)"),
    ("CV", "Unknown results stay unknown", "GREEN", "denominator test; 'uncertain' never positive in metrics"),
    ("CV", "Solar over-detection (34,179 objects in Huntersville)", "GREEN", "fixed at the root cause: the old panel colour matched shadows (0.0% of known array pixels, 7.0% of Huntersville pixels) and 99.4% of objects were never checked. New panel colour + checks on every un-outlined box + outline rules (rectangularity, host cover, roof contrast): Huntersville 34,178 -> 0 (res) / 5,948 -> 1 (comm), Matthews holdout 22,745 -> 1, Uptown 1,193 -> 0; the 7 detections left in 7 areas all look like arrays by an agent eye-check (not a human review); pools unchanged (Ballantyne 364 -> 364); tests/test_solar.py (24). Recall is now very low (bright grey panels missed; OSM reference 8/12 South End, 0/8 elsewhere), so solar stays EXPERIMENTAL (docs/COMPUTER_VISION_EVALUATION.md section 4)"),
    ("CV", "Canopy / tree class", "YELLOW", "built: data/canopy/v1 for the 6 lidar places (nDSM ≥ 3 m, NAIP NDVI ≥ 0.2, building-masked, nodata never 0), wired into risk.canopy_facts, tests/test_canopy.py; accuracy only eye-checked on 10 Lake Norman pools (8 right, 1 over, 1 unclear) — no labelled measurement; other places 'unavailable' (docs/CANOPY.md)"),
    # ---- Security + licensing (Part 12–13)
    ("Security", "CORS restricted (was *)", "GREEN", "api/app.py allow-list + tailnet regex"),
    ("Security", "People check on client-supplied last_text before the LLM", "GREEN", "api/planner/brain.py"),
    ("Security", "Asset path checks (is_relative_to); unique export names + body cap", "GREEN", "api/app.py; tools.export"),
    ("Security", "Authentication for the API", "GREEN", "api/auth.py: Bearer token or HttpOnly session cookie on every /api route except /health and /auth/*; 401 on missing/malformed/unknown; hashed tokens; login rate limit; sign-in screen; tests/test_auth.py (8). Live API still runs GROUNDTRUTH_AUTH=off (dev mode, scripts/api_branch.sh) until the owner turns it on (docs/AUTH.md)"),
    ("Security", "Investigations owner check", "GREEN", "owner derived on the server from the principal (forged X-GT-Owner overwritten); read/cancel/evidence/feedback/report → 404 for another user's; legacy unowned hidden; pins only into own sessions (test_investigation_ownership_no_idor, test_sessions_are_per_user_even_with_forged_header, test_pin_only_into_own_session)"),
    ("Security", "API key restrictions in provider consoles", "BLOCKED", "owner's Google/Foursquare/Yelp consoles"),
    ("Licensing", "Compliance package (inventory, models, data, API terms, commercial matrix, approvals)", "GREEN", "docs/compliance/*.md (6 files)"),
    ("Licensing", "Legal approvals (Grounding DINO, OpenCLIP deployed-use clause, ODbL share-alike, Yelp commercial, Google)", "BLOCKED", "APPROVAL_REQUIRED.md A1–A8: owner / counsel"),
    # ---- Frontend (Part 14)
    ("Frontend", "Objective → plan → live steps → map → evidence → report", "GREEN", "demo_check v3 17/17 at 3 sizes"),
    ("Frontend", "Distinct labels: computed / AI prediction / provider data / human-verified / gap / assumption", "GREEN", "NatureChip + provenance badge (intel.css)"),
    ("Frontend", "Tablet + phone usable (no horizontal overflow)", "GREEN", "responsive_shots.py"),
    ("Frontend", "Validation Studio in the app", "GREEN", "rail 'Validate'; studio_check PASS"),
    ("Frontend", "Phone rail ergonomics", "GREEN", "≤ 600 px: the rail is off-canvas and slides in with the ☰ drawer, so map + conversation get the full width; scripts/responsive_shots.py checks hidden by default, shown with the drawer, Validate reachable, no horizontal overflow (docs/media/v3/ui/r_phone_*.png)"),
    # ---- Platform (Part 15–16)
    ("Platform", "Memory monitoring endpoint", "GREEN", "GET /api/v1/runtime (RSS, MemAvailable, GPU)"),
    ("Platform", "Health checks + warm-up", "GREEN", "/api/v1/health"),
    ("Platform", "Structured logs", "DEFERRED", "plain logs; not needed for the demo"),
    ("Platform", "Dockerised services", "DEFERRED", "Docker is off-limits on this PC (project rule); untested Dockerfiles would be misleading"),
    ("Platform", "Cold start reduced", "GREEN", "restart → healthy 274.8 s → 51.8 s (in-process warm-up 248.9 s → 46.9 s), same machine, scripts/cold_start_profile.sh [blocking]: healthy now after models + index + the flagship investigation; the chat chain (206.6 s) warms in the background, yielding to requests. First flagship after healthy: 2301 → 1103 ms. First plain chat search after healthy: 57 → 1294 ms (now warmed in the background)"),
    ("Platform", "Unit + integration + browser + regression tests run", "GREEN", "183 pytest + 3 live skipped; demo_check v3; studio_check; bench; eval_point_find"),
    ("Platform", "No destructive regression; demo-final + labels preserved", "GREEN", "demo-final untouched; labels hash test; classic UI at /classic"),
]


def main():
    c = Counter(s for _, _, s, _ in L)
    n = len(L)
    out = ["# RED / GREEN master audit (2026-11-08)", "",
           "**Baseline.** The brief cites 102 RED items. No such checklist exists in the repository (searched docs/ and the tree for "
           "RED / 🔴 / checklists: none). The ledger below is built from this brief's own acceptance criteria (Part 20) plus its "
           "explicit requirements (Parts 3–16), one row per verifiable requirement. It is regenerated by `scripts/red_green_ledger.py`; "
           "counts come from the rows.", "",
           f"**Totals ({n} items):** " + " · ".join(f"{k} {c.get(k, 0)}" for k in ("GREEN", "YELLOW", "RED", "BLOCKED", "DEFERRED")),
           f"  ", f"Not GREEN: {n - c.get('GREEN', 0)} (RED {c.get('RED', 0)} + BLOCKED {c.get('BLOCKED', 0)} + YELLOW {c.get('YELLOW', 0)} + DEFERRED {c.get('DEFERRED', 0)}).",
           "", "GREEN = implemented and verified with the evidence cited. YELLOW = implemented, verification incomplete. BLOCKED = needs "
           "something only the owner can provide (keys, legal sign-off, a human reviewer, console access).", ""]
    area = None
    for a, req, st, ev in L:
        if a != area:
            out += ["", f"## {a}", "", "| Requirement | Status | Evidence |", "|---|---|---|"]
            area = a
        out.append(f"| {req} | **{st}** | {ev} |")
    Path(__file__).resolve().parents[1].joinpath("docs", "RED_GREEN_MASTER_AUDIT.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(n, dict(c))


if __name__ == "__main__":
    main()
