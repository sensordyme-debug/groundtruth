"""Place provider adapters (engine/groundtruth/places): HTTP behaviour, normalization, caps, ledger, secrets.

Everything here uses httpx.MockTransport with bodies shaped like the documented schemas, EXCEPT the tests marked
``live`` at the bottom, which call the real APIs and are skipped unless the key is present. Mocked passes are not
evidence that a live integration works.
"""
from __future__ import annotations

import copy
import json

import httpx
import pytest

from groundtruth import config
from groundtruth.places import base as pbase
from groundtruth.places.base import ProviderError, SpendCapExceeded, UsageLedger, clear_caches, load_conf
from groundtruth.places.credentials import _parse, has_secret
from groundtruth.places.foursquare import API_VERSION, FoursquareProvider
from groundtruth.places.google import GoogleProvider
from groundtruth.places.testing import (FSQ_SEARCH, GOOGLE_GEOCODE_OK, GOOGLE_TEXT_SEARCH, NODA_BBOX, YELP_REVIEWS,
                                        YELP_SEARCH, Script, body)
from groundtruth.places.yelp import YelpProvider, bbox_center_radius

FAKE = {"FOURSQUARE_API_KEY": "FAKEfsqKEY-7d1c9e0a5b", "YELP_API_KEY": "FAKEyelpKEY-3f2e1d0c9b",
        "GOOGLE_MAPS_API_KEY": "FAKEgoogKEY-a1b2c3d4e5", "YELP_CLIENT_ID": "FAKEyelpCLIENT-0000"}
FSQ_HOST, YELP_HOST = "places-api.foursquare.com", "api.yelp.com"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setenv("GROUNDTRUTH_ENV_FILE", str(tmp_path / "absent.env"))
    for k in FAKE:
        monkeypatch.delenv(k, raising=False)
    clear_caches()
    yield tmp_path
    clear_caches()


@pytest.fixture
def keys(sandbox, monkeypatch):
    for k, v in FAKE.items():
        monkeypatch.setenv(k, v)
    return FAKE


def conf(**provider_over):
    c = copy.deepcopy(load_conf())
    for name, over in provider_over.items():
        c["providers"][name].update(over)
    return c


def ledger_rows(tmp_path):
    p = tmp_path / "app" / "v1" / "provider_usage.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def make(cls, script, c=None, sleeps=None):
    return cls(c or conf(), transport=script.transport, sleep=(sleeps.append if sleeps is not None else lambda s: None))


# ------------------------------------------------------------------------------------------------- credentials
def test_env_file_parser(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\nexport A=1\nB = 'two words'\nC=\"x\"\nD=val # trailing\nEMPTY=\nnot a line\n")
    assert _parse(f) == {"A": "1", "B": "two words", "C": "x", "D": "val", "EMPTY": ""}
    monkeypatch.setenv("GROUNDTRUTH_ENV_FILE", str(f))
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)
    assert has_secret("A") and not has_secret("EMPTY")


# ------------------------------------------------------------------------------------------------- foursquare
def test_foursquare_search_success_headers_and_normalization(keys, sandbox):
    s = Script({FSQ_HOST + "/places/search": [body(FSQ_SEARCH)]})
    recs = make(FoursquareProvider, s).search("coffee", NODA_BBOX, ["cafe"], limit=10, investigation_id="inv-1")
    req = s.requests[0]
    assert req.headers["Authorization"] == f"Bearer {keys['FOURSQUARE_API_KEY']}"
    assert req.headers["X-Places-Api-Version"] == API_VERSION == "2025-06-17"
    assert req.url.params["ne"] == f"{NODA_BBOX[3]},{NODA_BBOX[2]}" and req.url.params["sw"].startswith("35.22")
    assert req.url.params["query"].startswith("coffee") and "fields" not in req.url.params  # Pro default
    a = recs[0]
    assert a.id == "foursquare:fsq_amelies_0001" and a.provider_place_id == "fsq_amelies_0001"
    assert (a.lat, a.lon) == (35.2468, -80.8067)
    assert a.address == "2424 N Davidson St" and a.city == "Charlotte" and a.state == "NC" and a.postal_code == "28205"
    assert a.phone == "(704) 555-0101" and a.website == "https://example.com/amelies"
    assert a.categories == ["cafe"] and a.categories_raw == ["Bakery", "Café"]
    assert a.attribution == "Powered by Foursquare" and a.retrieved_at.endswith("Z")
    assert a.raw["fsq_category_ids"] == ["cat-bakery", "cat-cafe"]
    assert recs[2].lat is None and recs[2].lon is None  # missing coordinates stay missing (never invented)
    rows = ledger_rows(sandbox)
    assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["endpoint"] == "places/search"
    assert rows[0]["estimated_cost_usd"] == 0.015 and rows[0]["investigation_id"] == "inv-1"


def test_foursquare_premium_fields_and_match_and_ask(keys, sandbox):
    s = Script({
        FSQ_HOST + "/places/match": [{"place": FSQ_SEARCH["results"][0], "match_score": 0.97,
                                      "context": {"latitude": 35.2, "longitude": -80.8}}],
        FSQ_HOST + "/places/ask": [{"results": [{"justifications": ["tip-derived reason"],
                                                 "place": {**FSQ_SEARCH["results"][1], "rating": 8.9, "price": 2}}]}],
        FSQ_HOST + "/places/": [{**FSQ_SEARCH["results"][0], "rating": 9.1, "price": 1}],
    })
    p = make(FoursquareProvider, s, conf(foursquare={"request_premium_fields": True}))
    m = p.match("Amelie's French Bakery", "2424 N Davidson St", "Charlotte", "NC", "28205")
    assert m.raw["match_score"] == 0.97 and s.requests[0].url.params["cc"] == "US"
    assert "rating" in s.requests[0].url.params["fields"]
    d = p.details("fsq_amelies_0001")
    assert d.rating == 9.1 and d.rating_scale_max == 10.0 and d.price == 1
    ask = p.ask("quiet coffee shop to work", ll=(35.247, -80.807))
    assert ask[0].raw["discovery_only"] is True and ask[0].raw["ask_justification_count"] == 1
    assert "tip-derived reason" not in json.dumps([r.model_dump() for r in ask])
    cats = [r["category"] for r in ledger_rows(sandbox)]
    assert cats == ["match_premium", "details_premium", "ask_premium"]


def test_missing_key_disables_provider_with_clear_reason(sandbox):
    s = Script({FSQ_HOST: [body(FSQ_SEARCH)]})
    p = make(FoursquareProvider, s)
    st = p.status()
    assert st["enabled"] is False and st["key_present"] is False
    assert "FOURSQUARE_API_KEY is not set" in st["reason"]
    with pytest.raises(ProviderError) as ei:
        p.search("coffee", NODA_BBOX)
    assert ei.value.code == "missing_key" and not s.requests  # no HTTP call was made


def test_429_retry_honours_retry_after(keys, sandbox):
    s = Script({FSQ_HOST: [(429, {"message": "slow down"}, {"Retry-After": "3"}), body(FSQ_SEARCH)]})
    sleeps: list[float] = []
    recs = make(FoursquareProvider, s, sleeps=sleeps).search("coffee", NODA_BBOX)
    assert len(recs) == 4 and len(s.requests) == 2
    assert 3.0 in sleeps
    row = ledger_rows(sandbox)[-1]
    assert row["status"] == "ok" and row["retry_count"] == 1 and row["rate_limited"] is True and row["attempts"] == 2


def test_429_retry_after_too_long_is_not_retried(keys, sandbox):
    s = Script({FSQ_HOST: [(429, {}, {"Retry-After": "3600"}), body(FSQ_SEARCH)]})
    with pytest.raises(ProviderError) as ei:
        make(FoursquareProvider, s).search("coffee", NODA_BBOX)
    assert ei.value.code == "rate_limited" and len(s.requests) == 1


def test_5xx_retries_bounded_then_fails(keys, sandbox):
    s = Script({FSQ_HOST: [(503, {"message": "down"})]})
    sleeps: list[float] = []
    with pytest.raises(ProviderError) as ei:
        make(FoursquareProvider, s, sleeps=sleeps).search("coffee", NODA_BBOX)
    assert ei.value.code == "upstream_error"
    assert len(s.requests) == 3  # 1 + max_retries(2)
    assert [x for x in sleeps if x >= 0.3] == [0.5, 1.0]  # exponential backoff (rate-limiter waits are < 0.3 s)
    row = ledger_rows(sandbox)[-1]
    assert row["status"] == "error" and row["retry_count"] == 2 and row["http_status"] == 503
    assert row["estimated_cost_usd"] is None


def test_5xx_then_success(keys, sandbox):
    s = Script({YELP_HOST: [(502, {}), body(YELP_SEARCH)]})
    recs = make(YelpProvider, s).search("coffee", NODA_BBOX)
    assert len(recs) == 3 and ledger_rows(sandbox)[-1]["retry_count"] == 1


def test_timeout_retried_then_reported(keys, sandbox):
    s = Script({FSQ_HOST: [httpx.ReadTimeout("timed out")]})
    with pytest.raises(ProviderError) as ei:
        make(FoursquareProvider, s).search("coffee", NODA_BBOX)
    assert ei.value.code == "timeout" and len(s.requests) == 3


def test_auth_failure_not_retried(keys, sandbox):
    s = Script({YELP_HOST: [(401, {"error": {"code": "TOKEN_INVALID", "description": "Invalid access token"}})]})
    with pytest.raises(ProviderError) as ei:
        make(YelpProvider, s).search("coffee", NODA_BBOX)
    assert ei.value.code == "auth" and len(s.requests) == 1
    assert keys["YELP_API_KEY"] not in str(ei.value)


def test_yelp_daily_quota_429_not_retried(keys, sandbox):
    s = Script({YELP_HOST: [(429, {"error": {"code": "ACCESS_LIMIT_REACHED"}}), body(YELP_SEARCH)]})
    with pytest.raises(ProviderError) as ei:
        make(YelpProvider, s).search("coffee", NODA_BBOX)
    assert ei.value.code == "rate_limited" and len(s.requests) == 1


def test_spend_cap_request_count_refuses(keys, sandbox):
    s = Script({FSQ_HOST: [body(FSQ_SEARCH)]})
    p = make(FoursquareProvider, s, conf(foursquare={"daily_request_cap": 1}))
    p.search("coffee", NODA_BBOX)
    with pytest.raises(SpendCapExceeded) as ei:
        p.search("coffee", NODA_BBOX)
    assert ei.value.code == "spend_cap" and "daily request cap" in ei.value.message
    assert len(s.requests) == 1
    rows = ledger_rows(sandbox)
    assert rows[-1]["status"] == "refused" and rows[-1]["sent"] is False


def test_spend_cap_usd_refuses_before_any_call(keys, sandbox):
    s = Script({FSQ_HOST: [body(FSQ_SEARCH)]})
    p = make(FoursquareProvider, s, conf(foursquare={"daily_usd_cap": 0.01}))  # one search is $0.015
    with pytest.raises(SpendCapExceeded) as ei:
        p.search("coffee", NODA_BBOX)
    assert "spend cap" in ei.value.message and not s.requests


def test_rate_limiter_spacing():
    t = [0.0]
    slept: list[float] = []
    rl = pbase.RateLimiter(2.0, clock=lambda: t[0], sleep=slept.append)
    rl.acquire(), rl.acquire(), rl.acquire()
    assert slept == [0.5, 1.0]


# ------------------------------------------------------------------------------------------------------- yelp
def test_yelp_search_normalization_and_no_client_id_sent(keys, sandbox):
    s = Script({YELP_HOST + "/v3/businesses/search": [body(YELP_SEARCH)]})
    recs = make(YelpProvider, s).search("bakery", NODA_BBOX, ["cafe", "restaurant"], limit=99)
    req = s.requests[0]
    assert req.headers["Authorization"] == f"Bearer {keys['YELP_API_KEY']}"
    assert keys["YELP_CLIENT_ID"] not in str(req.url) and keys["YELP_CLIENT_ID"] not in str(dict(req.headers))
    assert req.url.params["limit"] == "50" and req.url.params["categories"] == "coffee,cafes,restaurants"
    lat, lon, radius = bbox_center_radius(NODA_BBOX)
    assert req.url.params["radius"] == str(radius) and 2500 < radius < 3200
    a = recs[0]
    assert a.id == "yelp:yelpAmelies22charsXXXXX" and a.name == "Amelie's French Bakery"
    assert (a.lat, a.lon) == (35.24685, -80.80665) and a.address == "2424 N Davidson St"
    assert a.postal_code == "28205" and a.phone == "+17045550101" and a.price == 1 and a.review_count == 3000
    assert a.rating == 4.5 and a.rating_scale_max == 5.0 and a.categories == ["cafe"]
    assert a.source_url.startswith("https://www.yelp.com/biz/") and a.attribution == "Yelp"
    assert "image_url" not in json.dumps(a.model_dump())  # business photos are not kept (may show people)
    assert recs[2].lat is None


def test_yelp_radius_capped_at_40km():
    assert bbox_center_radius((-81.2, 34.9, -80.5, 35.6))[2] == 40000


def test_yelp_reviews_drop_people_data(keys, sandbox):
    s = Script({YELP_HOST + "/v3/businesses/": [body(YELP_REVIEWS)]})
    out = make(YelpProvider, s).reviews_summary("yelpAmelies22charsXXXXX")
    assert out["total"] == 3000 and out["excerpts_returned"] == 1 and out["excerpts"] == []
    s2 = Script({YELP_HOST + "/v3/businesses/": [body(YELP_REVIEWS)]})
    clear_caches()
    out2 = make(YelpProvider, s2, conf(yelp={"store_review_excerpts": True})).reviews_summary("x")
    dumped = json.dumps(out2)
    assert out2["excerpts"][0]["text"].startswith("Great pastries")
    for banned in ("Pat Q.", "user123", "profile_url", "user_details", "u.jpg", '"user"'):
        assert banned not in dumped


def test_yelp_match_and_autocomplete(keys, sandbox):
    s = Script({
        YELP_HOST + "/v3/businesses/matches": [{"businesses": [YELP_SEARCH["businesses"][0]]}],
        YELP_HOST + "/v3/autocomplete": [{"terms": [{"text": "Bakeries"}], "businesses": [{"id": "b1", "name": "B"}],
                                          "categories": [{"alias": "bakeries", "title": "Bakeries"}]}],
    })
    p = make(YelpProvider, s)
    m = p.match("Amelie's French Bakery", "2424 N Davidson St", "Charlotte", "NC", "28205")
    assert m[0].provider_place_id == "yelpAmelies22charsXXXXX"
    assert s.requests[0].url.params["country"] == "US"
    ac = p.autocomplete("bake", ll=(35.24, -80.80))
    assert ac["terms"] == ["Bakeries"] and ac["categories"][0]["alias"] == "bakeries"


def test_yelp_in_memory_cache_marks_ledger(keys, sandbox):
    s = Script({YELP_HOST: [body(YELP_SEARCH)]})
    p = make(YelpProvider, s)
    p.search("coffee", NODA_BBOX)
    p.search("coffee", NODA_BBOX)
    assert len(s.requests) == 1
    rows = ledger_rows(sandbox)
    assert rows[-1]["cached"] is True and rows[-1]["sent"] is False and rows[-1]["estimated_cost_usd"] == 0.0


# ----------------------------------------------------------------------------------------------------- google
def test_google_disabled_by_default_even_with_key(keys, sandbox):
    s = Script({"places.googleapis.com": [body(GOOGLE_TEXT_SEARCH)]})
    p = make(GoogleProvider, s)
    st = p.status()
    assert st["enabled"] is False and st["key_present"] is True and "no Google data" in st["reason"]
    with pytest.raises(ProviderError) as ei:
        p.search("bakery", NODA_BBOX)
    assert ei.value.code == "disabled" and not s.requests


def test_google_text_search_when_enabled(keys, sandbox):
    s = Script({"places.googleapis.com/v1/places:searchText": [body(GOOGLE_TEXT_SEARCH)]})
    p = make(GoogleProvider, s, conf(google={"enabled": True}))
    recs = p.search("bakery", NODA_BBOX, ["cafe"], limit=50)
    req = s.requests[0]
    sent = json.loads(req.content)
    assert req.headers["X-Goog-Api-Key"] == keys["GOOGLE_MAPS_API_KEY"]
    assert req.headers["X-Goog-FieldMask"].startswith("places.id,places.displayName")
    assert "places.rating" not in req.headers["X-Goog-FieldMask"]  # Pro SKU by default
    assert sent["pageSize"] == 20 and sent["includedType"] == "cafe"
    assert sent["locationRestriction"]["rectangle"]["low"] == {"latitude": NODA_BBOX[1], "longitude": NODA_BBOX[0]}
    r = recs[0]
    assert r.name == "Amélie's French Bakery & Café" and r.address == "2424 N Davidson St"
    assert r.city == "Charlotte" and r.county == "Mecklenburg County" and r.state == "NC" and r.postal_code == "28205"
    assert r.categories == ["cafe"] and r.source_url == "https://maps.google.com/?cid=1" and r.attribution == "Google Maps"
    assert ledger_rows(sandbox)[-1]["category"] == "text_search_pro"
    assert ledger_rows(sandbox)[-1]["estimated_cost_usd"] == 0.032


def test_google_empty_text_search_body(keys, sandbox):
    s = Script({"places.googleapis.com": [{}]})
    assert make(GoogleProvider, s, conf(google={"enabled": True})).search("x", NODA_BBOX) == []


def test_google_geocode_status_handling_and_key_never_logged(keys, sandbox):
    s = Script({"maps.googleapis.com": [body(GOOGLE_GEOCODE_OK)]})
    p = make(GoogleProvider, s, conf(google={"enabled": True}))
    g = p.geocode("600 E 4th St, Charlotte, NC")
    assert g[0]["lat"] == 35.2207 and g[0]["place_id"] == "ChIJ_geo"
    s2 = Script({"maps.googleapis.com": [{"status": "REQUEST_DENIED", "error_message": "API key invalid"}]})
    with pytest.raises(ProviderError) as ei:
        make(GoogleProvider, s2, conf(google={"enabled": True})).geocode("x")
    assert ei.value.code == "auth" and len(s2.requests) == 1
    s3 = Script({"maps.googleapis.com": [httpx.ConnectError("boom")]})
    with pytest.raises(ProviderError) as ei3:
        make(GoogleProvider, s3, conf(google={"enabled": True})).geocode("x")
    assert keys["GOOGLE_MAPS_API_KEY"] not in str(ei3.value)
    text = (sandbox / "app" / "v1" / "provider_usage.jsonl").read_text()
    assert keys["GOOGLE_MAPS_API_KEY"] not in text and "key=" not in text


def test_google_address_validation_gated(keys, sandbox):
    s = Script({"addressvalidation.googleapis.com": [{"result": {"verdict": {"addressComplete": True},
                                                                 "address": {"formattedAddress": "600 E 4th St"}}}]})
    p = make(GoogleProvider, s, conf(google={"enabled": True}))
    with pytest.raises(ProviderError) as ei:
        p.validate_address(["600 E 4th St"])
    assert ei.value.code == "disabled" and not s.requests
    p2 = make(GoogleProvider, s, conf(google={"enabled": True, "address_validation_enabled": True}))
    assert p2.validate_address(["600 E 4th St"])["verdict"]["addressComplete"] is True


def test_google_probe_reports_per_api(keys, sandbox):
    s = Script({"maps.googleapis.com": [body(GOOGLE_GEOCODE_OK)],
                "places.googleapis.com": [(403, {"error": {"status": "PERMISSION_DENIED"}})],
                "addressvalidation.googleapis.com": [(403, {})]})
    out = make(GoogleProvider, s).probe_apis()  # works while disabled: probe only
    assert out == {"geocoding": "ok", "places_new": "auth", "address_validation": "auth"}


# ------------------------------------------------------------------------------------------------------ ledger
def test_ledger_never_contains_secrets(keys, sandbox):
    routes = {FSQ_HOST: [(429, {}, {"Retry-After": "0"}), body(FSQ_SEARCH)],
              YELP_HOST: [(401, {"error": {"code": "TOKEN_INVALID"}})],
              "places.googleapis.com": [(500, {})], "maps.googleapis.com": [body(GOOGLE_GEOCODE_OK)]}
    s = Script(routes)
    c = conf(google={"enabled": True})
    make(FoursquareProvider, s, c).search("coffee", NODA_BBOX, investigation_id="inv-x")
    for fn in (lambda: make(YelpProvider, s, c).search("coffee", NODA_BBOX),
               lambda: make(GoogleProvider, s, c).search("coffee", NODA_BBOX)):
        with pytest.raises(ProviderError):
            fn()
    make(GoogleProvider, s, c).geocode("600 E 4th St")
    text = (sandbox / "app" / "v1" / "provider_usage.jsonl").read_text()
    for v in FAKE.values():
        assert v not in text
    for banned in ("Authorization", "Bearer", "X-Goog-Api-Key", "http://", "https://", "Amélie", "results"):
        assert banned not in text
    rows = [json.loads(x) for x in text.splitlines()]
    assert set(rows[0]) == set(pbase.LEDGER_FIELDS)
    assert {r["provider"] for r in rows} == {"foursquare", "yelp", "google"}
    usage = UsageLedger(sandbox / "app" / "v1" / "provider_usage.jsonl").usage_today()
    assert usage["foursquare"]["requests"] == 2 and usage["foursquare"]["rate_limited"] == 1
    assert usage["yelp"]["errors"] == 1


# -------------------------------------------------------------------------------------------------------- live
# Real API calls. Skipped while the key is absent (no .env exists yet). They spend a few calls within the caps.
@pytest.mark.skipif(not has_secret("FOURSQUARE_API_KEY"), reason="live: FOURSQUARE_API_KEY not set")
def test_live_foursquare_search():
    clear_caches()
    recs = FoursquareProvider().search("coffee", NODA_BBOX, limit=5, investigation_id="live-test")
    assert recs and all(r.provider == "foursquare" for r in recs)


@pytest.mark.skipif(not has_secret("YELP_API_KEY"), reason="live: YELP_API_KEY not set")
def test_live_yelp_search():
    clear_caches()
    recs = YelpProvider().search("coffee", NODA_BBOX, limit=5, investigation_id="live-test")
    assert recs and all(r.provider == "yelp" for r in recs)


@pytest.mark.skipif(not has_secret("GOOGLE_MAPS_API_KEY"), reason="live: GOOGLE_MAPS_API_KEY not set")
def test_live_google_probe():
    clear_caches()
    out = GoogleProvider().probe_apis(investigation_id="live-probe")
    assert set(out) == {"geocoding", "places_new", "address_validation"}
    print({k: v for k, v in out.items()})  # api name -> ok/auth/...; no key material
