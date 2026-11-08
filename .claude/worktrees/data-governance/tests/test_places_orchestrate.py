"""search_places orchestration + the /api/v1/places router (mocked HTTP only)."""
from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from groundtruth import config
from groundtruth.envelope import Envelope
from groundtruth.places import orchestrate
from groundtruth.places.base import clear_caches, load_conf
from groundtruth.places.orchestrate import build_providers, parse_area, search_places
from groundtruth.places.testing import FSQ_SEARCH, GOOGLE_TEXT_SEARCH, NODA_BBOX, YELP_SEARCH, Script, body

FAKE = {"FOURSQUARE_API_KEY": "FAKEfsqKEY-orch-11111", "YELP_API_KEY": "FAKEyelpKEY-orch-22222",
        "GOOGLE_MAPS_API_KEY": "FAKEgoogKEY-orch-33333", "YELP_CLIENT_ID": "FAKEclient-orch-44444"}
NODA = {"bbox": list(NODA_BBOX)}


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


def providers(routes, c=None):
    s = Script(routes)
    c = c or conf()
    return build_providers(c, transports={n: s.transport for n in ("foursquare", "yelp", "google")},
                           sleep=lambda x: None), s, c


OK_ROUTES = {"places-api.foursquare.com": [body(FSQ_SEARCH)], "api.yelp.com": [body(YELP_SEARCH)],
             "places.googleapis.com": [body(GOOGLE_TEXT_SEARCH)]}


def test_two_providers_merge_filter_and_envelope(keys, sandbox):
    objs, s, c = providers(OK_ROUTES)
    env = search_places("coffee", NODA, ["cafe"], investigation_id="inv-7", conf=c, provider_objs=objs)
    Envelope.model_validate(env.model_dump())
    m = env.result.metrics
    assert env.status == "ok" and env.tool == "search_places"
    assert m["providers"]["foursquare"]["status"] == "ok" and m["providers"]["yelp"]["status"] == "ok"
    assert m["providers"]["google"]["status"] == "disabled"  # default-off, not requested => not partial
    assert m["records_in"] == 7 and m["dropped_outside_area"] == 1 and m["dropped_no_coordinates"] == 2
    assert m["records_kept"] == 4 and m["count"] == 2 and m["corroborated"] == 2
    feats = env.result.geojson["features"]
    names = {f["properties"]["name"] for f in feats}
    assert names == {"Amélie's French Bakery & Café", "Smelly Cat Coffeehouse"}
    for f in feats:
        p = f["properties"]
        assert f["geometry"]["type"] == "Point" and p["provenance"] == "provider_reported"
        assert p["corroboration"] == 2 and p["providers"] == ["foursquare", "yelp"]
        assert len(p["members"]) == 2 and {mm["provider"] for mm in p["members"]} == {"foursquare", "yelp"}
        lon, lat = f["geometry"]["coordinates"]
        assert (lat, lon) == next((mm["lat"], mm["lon"]) for mm in p["members"] if mm["id"] == p["coordinates_from"])
        assert set(p["attribution"]) == {"Powered by Foursquare", "Yelp"}
    assert {src.name for src in env.sources} == {"Foursquare Places API", "Yelp Places API"}
    assert any("not independent verification" in x for x in env.limits)
    assert m["usage_today"]["foursquare"]["calls"] == 1


def test_point_outside_polygon_is_dropped(keys, sandbox):
    objs, s, c = providers(OK_ROUTES)
    # polygon covering only the western half of NoDa: Amelie's (-80.8067) and Smelly Cat (-80.8080) are east of -80.81
    w, so, e, n = NODA_BBOX
    poly = {"type": "Polygon", "coordinates": [[[w, so], [-80.81, so], [-80.81, n], [w, n], [w, so]]]}
    env = search_places("coffee", {"geojson": poly}, conf=c, provider_objs=objs, providers=["foursquare"])
    m = env.result.metrics
    assert m["count"] == 0 and m["dropped_outside_area"] == 3 and m["dropped_no_coordinates"] == 1
    assert env.status == "ok" and env.result.geojson["features"] == []


def test_one_provider_failing_gives_partial(keys, sandbox):
    routes = {**OK_ROUTES, "api.yelp.com": [(500, {"error": "boom"})]}
    objs, s, c = providers(routes)
    env = search_places("coffee", NODA, conf=c, provider_objs=objs)
    m = env.result.metrics
    assert env.status == "partial"
    assert m["providers"]["yelp"]["status"] == "error" and m["providers"]["yelp"]["error_code"] == "upstream_error"
    assert m["providers"]["foursquare"]["status"] == "ok" and m["count"] == 2 and m["single"] == 2
    assert any(x.startswith("yelp failed") for x in env.limits)
    assert [src.name for src in env.sources] == ["Foursquare Places API"]


def test_adapter_bug_is_isolated(keys, sandbox):
    objs, s, c = providers(OK_ROUTES)

    def broken(*a, **k):
        raise KeyError("https://example.invalid/?key=SHOULD-NOT-LEAK")

    objs["yelp"].search = broken  # type: ignore[method-assign]
    env = search_places("coffee", NODA, conf=c, provider_objs=objs)
    assert env.status == "partial" and env.result.metrics["providers"]["yelp"]["error_code"] == "internal_error"
    assert "SHOULD-NOT-LEAK" not in json.dumps(env.model_dump())


def test_all_failing_is_declined(keys, sandbox):
    routes = {"places-api.foursquare.com": [(401, {})], "api.yelp.com": [(401, {})]}
    objs, s, c = providers(routes)
    env = search_places("coffee", NODA, conf=c, provider_objs=objs)
    assert env.status == "declined" and env.declined_reason.startswith("all_providers_failed")
    for v in FAKE.values():
        assert v not in json.dumps(env.model_dump())


def test_no_keys_declines_with_reasons(sandbox):
    objs, s, c = providers(OK_ROUTES)
    env = search_places("coffee", NODA, conf=c, provider_objs=objs)
    assert env.status == "declined" and "no_provider_available" in env.declined_reason
    assert "FOURSQUARE_API_KEY is not set" in env.declined_reason and not s.requests


def test_requesting_disabled_google_is_partial(keys, sandbox):
    objs, s, c = providers(OK_ROUTES)
    env = search_places("coffee", NODA, providers=["foursquare", "google"], conf=c, provider_objs=objs)
    assert env.status == "partial" and env.result.metrics["providers"]["google"]["status"] == "disabled"
    assert any(x.startswith("google was requested but not run") for x in env.limits)
    assert not any("googleapis" in str(r.url) for r in s.requests)


def test_google_enabled_corroborates_three_ways(keys, sandbox):
    objs, s, c = providers(OK_ROUTES, conf(google={"enabled": True}))
    env = search_places("bakery", NODA, ["cafe"], conf=c, provider_objs=objs)
    am = next(f for f in env.result.geojson["features"] if f["properties"]["name"].startswith("Am"))
    assert am["properties"]["corroboration"] == 3
    assert any("non-Google map" in x for x in env.limits)


def test_spend_cap_skips_provider(keys, sandbox):
    objs, s, c = providers(OK_ROUTES, conf(yelp={"daily_request_cap": 0}))
    env = search_places("coffee", NODA, conf=c, provider_objs=objs)
    assert env.status == "ok" and env.result.metrics["providers"]["yelp"]["status"] == "skipped"
    assert "spend_cap" in env.result.metrics["providers"]["yelp"]["reason"]
    assert not any("yelp" in str(r.url) for r in s.requests)


def test_cost_ordering_and_max_providers(keys, sandbox):
    c = conf()
    c["max_providers_per_search"] = 1
    objs, s, c = providers(OK_ROUTES, c)
    env = search_places("coffee", NODA, conf=c, provider_objs=objs)
    per = env.result.metrics["providers"]
    assert per["yelp"]["status"] == "ok"  # cheapest estimate per search ($7.63/1k vs $15/1k)
    assert per["foursquare"]["status"] == "skipped" and "max_providers_per_search" in per["foursquare"]["reason"]


def test_bad_inputs_declined(keys, sandbox):
    objs, s, c = providers(OK_ROUTES)
    assert search_places("", NODA, conf=c, provider_objs=objs).declined_reason.startswith("bad_request")
    bad = search_places("x", {"bbox": [1, 2, 0, 3]}, conf=c, provider_objs=objs)
    assert bad.status == "declined" and bad.declined_reason.startswith("bad_area")
    assert search_places("x", {"place": "Atlantis"}, conf=c, provider_objs=objs).status == "declined"


def test_parse_area_place_name():
    g, bbox, label = parse_area({"place": "noda"})
    assert label == "place:noda" and bbox == pytest.approx(NODA_BBOX)


# ---------------------------------------------------------------------------------------------------------- API
def api_client():
    from api import places_api

    app = FastAPI()
    app.include_router(places_api.router)
    return TestClient(app)


def test_api_providers_never_returns_key_values(keys, sandbox):
    r = api_client().get("/api/v1/places/providers")
    assert r.status_code == 200
    text = r.text
    for v in FAKE.values():
        assert v not in text
    provs = {p["provider"]: p for p in r.json()["providers"]}
    assert provs["foursquare"]["enabled"] is True and provs["foursquare"]["key_present"] is True
    assert provs["google"]["enabled"] is False and provs["google"]["key_present"] is True
    assert provs["yelp"]["optional_env_present"] == {"YELP_CLIENT_ID": True}
    assert provs["yelp"]["daily_request_cap"] == 200


def test_api_search_and_usage(keys, sandbox, monkeypatch):
    s = Script(OK_ROUTES)
    real = orchestrate.build_providers
    monkeypatch.setattr(orchestrate, "build_providers",
                        lambda conf=None, **k: real(conf, transports={n: s.transport for n in
                                                                      ("foursquare", "yelp", "google")},
                                                    sleep=lambda x: None))
    c = api_client()
    r = c.post("/api/v1/places/search", json={"query": "coffee", "area": {"place": "NoDa"}, "categories": ["cafe"],
                                              "investigation_id": "inv-api"})
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["result"]["metrics"]["count"] == 2
    u = c.get("/api/v1/places/usage").json()
    assert u["usage"]["foursquare"]["calls"] == 1 and u["usage"]["yelp"]["calls"] == 1
    assert u["caps"]["google"]["daily_request_cap"] == 100
    ledger = (sandbox / "app" / "v1" / "provider_usage.jsonl").read_text()
    assert "inv-api" in ledger and all(v not in ledger for v in FAKE.values())
    assert c.post("/api/v1/places/search", json={"query": "x"}).status_code == 422  # malformed => 4xx
