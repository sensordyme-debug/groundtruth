"""Yelp Places API (formerly Fusion) adapter. Public endpoints only; no partner APIs, no scraping.

Docs read 2026-11-08:
- Intro / endpoint list / auth: https://docs.developer.yelp.com/docs/fusion-intro
  Base ``https://api.yelp.com/v3``; private API key sent as ``Authorization: Bearer <API_KEY>``.
  The Client ID (YELP_CLIENT_ID) is an app identifier, NOT a credential: it is never sent.
- Business Search GET /businesses/search (term, latitude+longitude or location, radius <= 40000 m, categories,
  limit 0..50, offset 0..1000) -> {businesses[], total, region}: https://docs.developer.yelp.com/reference/v3_business_search
- Business Match  GET /businesses/matches (name, address1, city, state, country required; postal_code, latitude,
  longitude, phone, limit 1..10, match_threshold) -> {businesses[]}: https://docs.developer.yelp.com/reference/v3_business_match
- Business Details GET /businesses/{id}
- Reviews GET /businesses/{id}/reviews -> {total, reviews[{id,url,text,rating,time_created,user{...}}],
  possible_languages}; needs the Enhanced or Premium plan: https://docs.developer.yelp.com/reference/v3_business_reviews
- Autocomplete GET /autocomplete (text, latitude, longitude, locale) -> {terms[], businesses[{id,name}],
  categories[{alias,title}]}: https://docs.developer.yelp.com/reference/v3_autocomplete
- FAQ (daily limits, 24 h caching, trial): https://docs.developer.yelp.com/docs/places-faq
- API Terms (last updated 2026-09-22): https://terms.yelp.com/developers/api_terms/
- Display requirements: https://terms.yelp.com/developers/display_requirements/

People data: review ``user`` objects (names, ids, profile/photo URLs) are discarded on receipt; ``image_url`` business
photos are not kept (they can show people). Review excerpts are kept only when conf ``store_review_excerpts`` is true
(default false), and then only text/rating/time/link, no author fields.
"""
from __future__ import annotations

import math
from typing import Any

import httpx

from .base import BaseProvider, PlaceRecord, ProviderError, _f, utc_now_iso, valid_coords

BASE_URL = "https://api.yelp.com/v3"
MAX_RADIUS_M = 40000


def _quota_exhausted(resp: httpx.Response) -> bool:
    """Yelp returns 429 with error.code ACCESS_LIMIT_REACHED when the daily quota is gone: retrying cannot help."""
    try:
        code = ((resp.json() or {}).get("error") or {}).get("code")
    except (ValueError, AttributeError):
        return False
    return code == "ACCESS_LIMIT_REACHED"


def bbox_center_radius(bbox: tuple[float, float, float, float]) -> tuple[float, float, int]:
    """(lat, lon, radius_m) of the circle that covers the bbox (half its diagonal, haversine), capped at 40 km."""
    w, s, e, n = bbox
    lat, lon = (s + n) / 2, (w + e) / 2
    r = 6371008.8
    p1, p2 = math.radians(s), math.radians(n)
    dphi, dl = p2 - p1, math.radians(e - w)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    diag = 2 * r * math.asin(min(1.0, math.sqrt(a)))
    return lat, lon, int(min(MAX_RADIUS_M, math.ceil(diag / 2)))


class YelpProvider(BaseProvider):
    name = "yelp"
    capabilities = frozenset({"search", "details", "match", "reviews", "autocomplete"})
    source_name = "Yelp Places API"
    source_url = "https://docs.developer.yelp.com/reference/v3_business_search"
    licence = "Yelp API Terms of Use (proprietary; 24 h cache max; display requirements; commercial use needs " \
              "Yelp's written consent)"
    attribution = "Yelp"

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def _get(self, path: str, endpoint: str, category: str, params: dict[str, Any] | None,
             investigation_id: str | None) -> Any:
        key = self.require_enabled()
        return self.http.request("GET", f"{BASE_URL}{path}", endpoint=endpoint, category=category,
                                 headers=self._headers(key), params=params, investigation_id=investigation_id,
                                 no_retry=_quota_exhausted)

    # ------------------------------------------------------------------------------------------------ endpoints
    def search(self, query: str, bbox: tuple[float, float, float, float], categories: list[str] | None = None,
               limit: int = 20, investigation_id: str | None = None) -> list[PlaceRecord]:
        lat, lon, radius = bbox_center_radius(bbox)
        params: dict[str, Any] = {"latitude": round(lat, 6), "longitude": round(lon, 6), "radius": max(1, radius),
                                  "limit": max(1, min(int(limit), 50))}
        if query.strip():
            params["term"] = query.strip()[:300]
        aliases = self.provider_filters(categories, "yelp")
        if aliases:
            params["categories"] = ",".join(aliases)
        body = self._get("/businesses/search", "businesses/search", "search", params, investigation_id)
        items = body.get("businesses") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ProviderError(self.name, "bad_response", "search response has no 'businesses' list")
        out = [self.normalize(b) for b in items if isinstance(b, dict)]
        for r in out:
            r.raw["search_radius_m"] = radius
        return out

    def match(self, name: str, address1: str, city: str, state: str, postal_code: str | None = None,
              phone: str | None = None, ll: tuple[float, float] | None = None, limit: int = 3,
              investigation_id: str | None = None) -> list[PlaceRecord]:
        params: dict[str, Any] = {"name": name[:64], "address1": (address1 or "")[:64], "city": city[:64],
                                  "state": state[:3], "country": "US", "limit": max(1, min(int(limit), 10))}
        if postal_code:
            params["postal_code"] = postal_code[:12]
        if phone:
            params["phone"] = phone[:32]
        if ll:
            params["latitude"], params["longitude"] = ll
        body = self._get("/businesses/matches", "businesses/matches", "match", params, investigation_id)
        items = body.get("businesses") if isinstance(body, dict) else body  # the doc example shows a bare array
        if not isinstance(items, list):
            raise ProviderError(self.name, "bad_response", "match response has no 'businesses' list")
        return [self.normalize(b) for b in items if isinstance(b, dict)]

    def details(self, business_id: str, investigation_id: str | None = None) -> PlaceRecord:
        body = self._get(f"/businesses/{business_id}", "businesses/details", "details", None, investigation_id)
        if not isinstance(body, dict):
            raise ProviderError(self.name, "bad_response", "details response is not an object")
        return self.normalize(body)

    def reviews_summary(self, business_id: str, investigation_id: str | None = None) -> dict[str, Any]:
        """Aggregate only by default: {total, excerpts_returned, possible_languages, excerpts: []}.
        Reviewer names, user ids, profile and photo URLs are dropped on receipt and never stored."""
        body = self._get(f"/businesses/{business_id}/reviews", "businesses/reviews", "reviews", None,
                         investigation_id)
        if not isinstance(body, dict):
            raise ProviderError(self.name, "bad_response", "reviews response is not an object")
        reviews = [r for r in body.get("reviews") or [] if isinstance(r, dict)]
        excerpts: list[dict[str, Any]] = []
        if self.pconf.get("store_review_excerpts"):
            for r in reviews:  # whitelist: no 'user', no 'id'
                excerpts.append({"text": r.get("text"), "rating": _f(r.get("rating")),
                                 "time_created": r.get("time_created"), "url": r.get("url")})
        return {"provider_place_id": business_id, "total": body.get("total"), "excerpts_returned": len(reviews),
                "possible_languages": body.get("possible_languages") or [], "excerpts": excerpts,
                "attribution": self.attribution}

    def autocomplete(self, text: str, ll: tuple[float, float] | None = None,
                     investigation_id: str | None = None) -> dict[str, Any]:
        """Terms / categories / business id+name suggestions. No coordinates: never used as a place result."""
        params: dict[str, Any] = {"text": text}
        if ll:
            params["latitude"], params["longitude"] = ll
        body = self._get("/autocomplete", "autocomplete", "autocomplete", params, investigation_id)
        if not isinstance(body, dict):
            raise ProviderError(self.name, "bad_response", "autocomplete response is not an object")
        return {
            "terms": [t.get("text") for t in body.get("terms") or [] if isinstance(t, dict)],
            "categories": [{"alias": c.get("alias"), "title": c.get("title")} for c in body.get("categories") or []
                           if isinstance(c, dict)],
            "businesses": [{"id": b.get("id"), "name": b.get("name")} for b in body.get("businesses") or []
                           if isinstance(b, dict)],
        }

    # ------------------------------------------------------------------------------------------------ normalize
    def normalize(self, b: dict[str, Any]) -> PlaceRecord:
        coords = b.get("coordinates") if isinstance(b.get("coordinates"), dict) else {}
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if not valid_coords(lat, lon):
            lat = lon = None
        loc = b.get("location") if isinstance(b.get("location"), dict) else {}
        cats = [c for c in b.get("categories") or [] if isinstance(c, dict)]
        aliases = [c.get("alias") for c in cats if c.get("alias")]
        price = b.get("price")
        price_n = len(price) if isinstance(price, str) and price and set(price) == {"$"} and len(price) <= 4 else None
        raw: dict[str, Any] = {"alias": b.get("alias"), "category_titles": [c.get("title") for c in cats]}
        for k in ("is_closed", "distance", "transactions"):
            if b.get(k) is not None:
                raw[k] = b[k]
        if loc.get("display_address"):
            raw["display_address"] = loc["display_address"]
        rc = b.get("review_count")
        return PlaceRecord(
            id=f"yelp:{b.get('id')}", provider="yelp", provider_place_id=b.get("id"), name=b.get("name"),
            categories=self.normalize_categories(aliases, "yelp"), categories_raw=aliases,
            lat=_f(lat), lon=_f(lon), address=loc.get("address1") or None, city=loc.get("city"), county=None,
            state=loc.get("state"), postal_code=loc.get("zip_code"), phone=b.get("phone") or None, website=None,
            rating=_f(b.get("rating")), rating_scale_max=5.0 if b.get("rating") is not None else None,
            review_count=int(rc) if isinstance(rc, int) else None, price=price_n,
            source_url=b.get("url"), retrieved_at=utc_now_iso(), attribution=self.attribution, raw=raw,
        )
