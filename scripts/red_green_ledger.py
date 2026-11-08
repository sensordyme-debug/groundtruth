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
    ("Places", "Yelp display requirements (logo, stars, unmodified links) in the UI", "RED", "no provider-listing UI yet; required before Yelp content is shown (API_TERMS_REVIEW)"),
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
    ("Investigation", "Cancellation", "YELLOW", "cooperative cancel endpoint + UI stop; no automated test; a running tool can't be killed (Python threads)"),
    ("Investigation", "Findings link to evidence (every number traceable)", "GREEN", "bench_investigations: evidence 12/12"),
    ("Investigation", "Reports reproducible", "GREEN", "bench determinism 12/12; report.md; demo_check v3 report check"),
    ("Investigation", "Denominator integrity (passing / failing / unknown; unknown never counted)", "GREEN", "test_denominator_integrity_unknown_stays_unknown"),
    ("Investigation", "Flagship evidence: building geometry, pool detection, imagery source + flight dates", "GREEN", "evidence e3/e4 (NAIP 2022 flights 06-02/06-22/09-27); demo_check v3"),
    ("Investigation", "Flagship evidence: FEMA zone geometry, source and EFFECTIVE date", "YELLOW", "zone + source + fetch date recorded; FIRM effective dates never captured (DATA_SOURCE_REGISTRY)"),
    ("Investigation", "Flagship evidence: elevation source + date; missing data identified", "GREEN", "3DEP 2016 (published 2019); 7 of 20 BFE unavailable reported as a gap"),
    ("Investigation", "Ranking explained (documented ordering, not a score)", "GREEN", "rank step evidence; conf rank_rule"),
    ("Investigation", "Predicted vs verified distinguished (pool prediction ≠ confirmed pool)", "GREEN", "finding nature 'AI prediction' chips; sheet badge 'AI prediction · not human-verified'"),
    ("Investigation", "Flagship headline strength above 'weak' (derived v1 precision re-measured)", "RED", "derived v1 not re-measured; needs human reviews (Validation Studio)"),
    # ---- Validation Studio (Part 8)
    ("Validation", "Image viewer with zoom/pan + AI overlays", "GREEN", "Studio on the live map; scripts/studio_check.py PASS; screenshots 09/10"),
    ("Validation", "Review queue with class + region filters and progress", "GREEN", "studio_check: filter changes queue; progress '0 of 498 reviewed'"),
    ("Validation", "Accept / reject / uncertain / needs review recorded with history", "GREEN", "api/validation_api.py; tests/test_validation.py (journal, latest-wins, history)"),
    ("Validation", "Same decisions submitted from the UI", "YELLOW", "buttons wired to the API; deliberately not exercised in automation (no fake human reviews)"),
    ("Validation", "Draw / correct polygon", "YELLOW", "API validates corrected polygons (tests); UI draw uses the map tool; not browser-tested end to end"),
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
    ("Data", "Provenance bugs (Overture date empty; OSM dated by file mtime)", "RED", "providers/public.py:389/394 (registry has the right dates; the envelope source still wrong)"),
    ("Data", "NAIP scene seams", "RED", "mosaic not built (docs/MOSAIC.md: dodging step unfinished)"),
    ("Data", "New data versions registered (boundaries, validation) + manifest", "YELLOW", "data_versions.json updated; DEMO_MANIFEST not extended (derived, risk, lidar v2, boundaries)"),
    # ---- CV + refinement (Part 10–11)
    ("CV", "Find like this: rejecting a result refines without collapsing", "GREEN", "eval_point_find: parking 31→24→17 (OSM P 0.38→0.42→0.47); before: 70→3"),
    ("CV", "Find like this: unrelated objects controlled (per-click calibration)", "GREEN", "Lake Norman 7,137→1,160; pool eye 23/24"),
    ("CV", "Refinement improvement verified by humans", "RED", "OSM oracle flawed for pools; needs Studio reviews"),
    ("CV", "Similarity explainable in the UI", "YELLOW", "sim_example / sim_threshold on every result; not yet shown in the sheet"),
    ("CV", "Vision metrics measured with geographic holdouts", "YELLOW", "holdout samples built; human metrics pending reviews; existing metrics Lake-Norman-biased (disclosed)"),
    ("CV", "Unknown results stay unknown", "GREEN", "denominator test; 'uncertain' never positive in metrics"),
    ("CV", "Solar over-detection (34,179 objects in Huntersville)", "RED", "found by the sampler; solar marked experimental; needs a verifier"),
    ("CV", "Canopy / tree class", "RED", "canopy + slope rasters not built"),
    # ---- Security + licensing (Part 12–13)
    ("Security", "CORS restricted (was *)", "GREEN", "api/app.py allow-list + tailnet regex"),
    ("Security", "People check on client-supplied last_text before the LLM", "GREEN", "api/planner/brain.py"),
    ("Security", "Asset path checks (is_relative_to); unique export names + body cap", "GREEN", "api/app.py; tools.export"),
    ("Security", "Authentication for the API", "RED", "none; tailnet-only exposure assumed (SECURITY_AUDIT patch suggestions)"),
    ("Security", "Investigations owner check", "RED", "anyone with an id can read an investigation"),
    ("Security", "API key restrictions in provider consoles", "BLOCKED", "owner's Google/Foursquare/Yelp consoles"),
    ("Licensing", "Compliance package (inventory, models, data, API terms, commercial matrix, approvals)", "GREEN", "docs/compliance/*.md (6 files)"),
    ("Licensing", "Legal approvals (Grounding DINO, OpenCLIP deployed-use clause, ODbL share-alike, Yelp commercial, Google)", "BLOCKED", "APPROVAL_REQUIRED.md A1–A8: owner / counsel"),
    # ---- Frontend (Part 14)
    ("Frontend", "Objective → plan → live steps → map → evidence → report", "GREEN", "demo_check v3 17/17 at 3 sizes"),
    ("Frontend", "Distinct labels: computed / AI prediction / provider data / human-verified / gap / assumption", "GREEN", "NatureChip + provenance badge (intel.css)"),
    ("Frontend", "Tablet + phone usable (no horizontal overflow)", "GREEN", "responsive_shots.py"),
    ("Frontend", "Validation Studio in the app", "GREEN", "rail 'Validate'; studio_check PASS"),
    ("Frontend", "Phone rail ergonomics", "YELLOW", "works; rail takes 48 px of 390"),
    # ---- Platform (Part 15–16)
    ("Platform", "Memory monitoring endpoint", "GREEN", "GET /api/v1/runtime (RSS, MemAvailable, GPU)"),
    ("Platform", "Health checks + warm-up", "GREEN", "/api/v1/health"),
    ("Platform", "Structured logs", "DEFERRED", "plain logs; not needed for the demo"),
    ("Platform", "Dockerised services", "DEFERRED", "Docker is off-limits on this PC (project rule); untested Dockerfiles would be misleading"),
    ("Platform", "Cold start reduced", "RED", "warm-up still ~3–4 min (index paging + chain warm)"),
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
