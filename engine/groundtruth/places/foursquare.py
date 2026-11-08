"""Foursquare Places API adapter (new Places API, service keys).

Docs read 2026-11-08:
- Migration guide (base URL, auth, version header, renamed fields):
  https://docs.foursquare.com/fsq-developers-places/reference/migration-guide
  Base URL ``https://places-api.foursquare.com``; auth ``Authorization: Bearer <SERVICE_KEY>``; header
  ``X-Places-Api-Version: 2025-06-17`` (the only allowed value); ``fsq_id`` -> ``fsq_place_id``;
  ``geocodes`` -> top-level ``latitude`` / ``longitude``.
- Place Search  GET /places/search  (query, ll, radius, ne, sw, near, fsq_category_ids, fields, limit 1..50, sort)
  -> ``{"results": [place, ...]}``: https://docs.foursquare.com/fsq-developers-places/reference/place-search
- Place Details GET /places/{fsq_place_id} -> the place object:
  https://docs.foursquare.com/fsq-developers-places/reference/place-details
- Place Match   GET /places/match (name, address, city, state, postal_code, cc, ll, fields)
  -> ``{"place": {...}, "match_score": n, "context": {...}}``:
  https://docs.foursquare.com/fsq-developers-places/reference/place-match
- Ask           GET /places/ask (query, ll, context, location, fields) -> ``{"results": [{"justifications": [...],
  "place": {...}}]}``: https://docs.foursquare.com/fsq-developers-places/reference/ask
  Separate pricing and a 1 QPS limit on Pay-as-you-Go. Ask is DISCOVERY ONLY here: never a spatial filter, never
  authoritative; its records are tagged ``raw.discovery_only = True`` and the justifications (derived from user tips)
  are not kept, only their count.
- Field tiers: https://docs.foursquare.com/fsq-developers-places/reference/response-fields
  Pro: tel, website, email, social_media (+ core fields). Premium: rating, price, hours, tips, photos.
  Default requests omit ``fields`` (=> all Pro fields, Pro price). ``request_premium_fields`` adds rating/price.
- Usage guidelines (caching, attribution): https://docs.foursquare.com/fsq-developers-places/reference/usage-guidelines
- Rate limits: https://docs.foursquare.com/fsq-developers-places/reference/rate-limits  (50 QPS PAYG; Ask 1 QPS)

Category filtering: Foursquare category ids are not mapped here; requested categories are appended to the text query
and results are mapped back to normalized keys by category-name keywords.
"""
from __future__ import annotations

from typing import Any

from .base import BaseProvider, PlaceRecord, ProviderError, _f, utc_now_iso, valid_coords

BASE_URL = "https://places-api.foursquare.com"
API_VERSION = "2025-06-17"
PRO_FIELDS = "fsq_place_id,name,latitude,longitude,categories,location,tel,website"
PREMIUM_EXTRA = "rating,price"


class FoursquareProvider(BaseProvider):
    name = "foursquare"
    capabilities = frozenset({"search", "details", "match", "ask"})
    source_name = "Foursquare Places API"
    source_url = "https://docs.foursquare.com/fsq-developers-places/reference/place-search"
    licence = "Foursquare Places API terms (proprietary; no caching of attributes other than fsq_place_id on " \
              "Pay-as-you-Go/Sandbox; attribution required)"
    attribution = "Powered by Foursquare"

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "X-Places-Api-Version": API_VERSION, "Accept": "application/json"}

    def _fields(self) -> str | None:
        if self.pconf.get("request_premium_fields"):
            return f"{PRO_FIELDS},{PREMIUM_EXTRA}"
        return None  # omit => all Pro fields (Pro SKU)

    def _cat(self, base: str) -> str:
        return f"{base}_premium" if self.pconf.get("request_premium_fields") else base

    # ------------------------------------------------------------------------------------------------ endpoints
    def search(self, query: str, bbox: tuple[float, float, float, float], categories: list[str] | None = None,
               limit: int = 20, investigation_id: str | None = None) -> list[PlaceRecord]:
        key = self.require_enabled()
        w, s, e, n = bbox
        words = self.provider_filters(categories, "fsq_words")[:1]
        q = " ".join(x for x in [query.strip(), *words] if x).strip()
        params: dict[str, Any] = {"ne": f"{n},{e}", "sw": f"{s},{w}", "limit": max(1, min(int(limit), 50))}
        if q:
            params["query"] = q
        if self._fields():
            params["fields"] = self._fields()
        body = self.http.request("GET", f"{BASE_URL}/places/search", endpoint="places/search",
                                 category=self._cat("search"), headers=self._headers(key), params=params,
                                 investigation_id=investigation_id)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ProviderError(self.name, "bad_response", "search response has no 'results' list")
        return [self.normalize(p) for p in results if isinstance(p, dict)]

    def details(self, fsq_place_id: str, investigation_id: str | None = None) -> PlaceRecord:
        key = self.require_enabled()
        params = {"fields": self._fields()} if self._fields() else None
        body = self.http.request("GET", f"{BASE_URL}/places/{fsq_place_id}", endpoint="places/details",
                                 category=self._cat("details"), headers=self._headers(key), params=params,
                                 investigation_id=investigation_id)
        if not isinstance(body, dict):
            raise ProviderError(self.name, "bad_response", "details response is not an object")
        return self.normalize(body)

    def match(self, name: str, address: str, city: str, state: str | None = None, postal_code: str | None = None,
              ll: tuple[float, float] | None = None, investigation_id: str | None = None) -> PlaceRecord | None:
        key = self.require_enabled()
        params: dict[str, Any] = {"name": name, "address": address, "city": city, "cc": "US"}
        if state:
            params["state"] = state
        if postal_code:
            params["postal_code"] = postal_code
        if ll:
            params["ll"] = f"{ll[0]},{ll[1]}"
        if self._fields():
            params["fields"] = self._fields()
        body = self.http.request("GET", f"{BASE_URL}/places/match", endpoint="places/match",
                                 category=self._cat("match"), headers=self._headers(key), params=params,
                                 investigation_id=investigation_id)
        place = body.get("place") if isinstance(body, dict) else None
        if not isinstance(place, dict):
            return None
        rec = self.normalize(place)
        rec.raw["match_score"] = _f(body.get("match_score"))
        return rec

    def ask(self, query: str, ll: tuple[float, float] | None = None, location: str | None = None,
            investigation_id: str | None = None) -> list[PlaceRecord]:
        """Natural-language DISCOVERY only. Results must still pass the orchestrator's geography check and are never
        treated as an authoritative spatial filter."""
        key = self.require_enabled()
        params: dict[str, Any] = {"query": query}
        if ll:
            params["ll"] = f"{ll[0]},{ll[1]}"
        if location:
            params["location"] = location
        if self._fields():
            params["fields"] = self._fields()
        body = self.http.request("GET", f"{BASE_URL}/places/ask", endpoint="places/ask", category=self._cat("ask"),
                                 headers=self._headers(key), params=params, investigation_id=investigation_id)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ProviderError(self.name, "bad_response", "ask response has no 'results' list")
        out = []
        for r in results:
            if isinstance(r, dict) and isinstance(r.get("place"), dict):
                rec = self.normalize(r["place"])
                rec.raw["discovery_only"] = True
                rec.raw["ask_justification_count"] = len(r.get("justifications") or [])
                out.append(rec)
        return out

    # ------------------------------------------------------------------------------------------------ normalize
    def normalize(self, p: dict[str, Any]) -> PlaceRecord:
        pid = p.get("fsq_place_id") or p.get("fsq_id")
        lat, lon = p.get("latitude"), p.get("longitude")
        if not valid_coords(lat, lon):
            lat = lon = None
        loc = p.get("location") if isinstance(p.get("location"), dict) else {}
        raw_cats = [c.get("name") for c in p.get("categories") or [] if isinstance(c, dict) and c.get("name")]
        price = p.get("price")
        raw: dict[str, Any] = {}
        for k in ("date_closed", "distance"):
            if p.get(k) is not None:
                raw[k] = p[k]
        cat_ids = [c.get("fsq_category_id") for c in p.get("categories") or [] if isinstance(c, dict)]
        if any(cat_ids):
            raw["fsq_category_ids"] = [c for c in cat_ids if c]
        if loc.get("formatted_address"):
            raw["formatted_address"] = loc["formatted_address"]
        return PlaceRecord(
            id=f"foursquare:{pid}", provider="foursquare", provider_place_id=pid, name=p.get("name"),
            categories=self.normalize_categories(raw_cats, "fsq_words"), categories_raw=raw_cats,
            lat=_f(lat), lon=_f(lon), address=loc.get("address"), city=loc.get("locality"), county=None,
            state=loc.get("region"), postal_code=loc.get("postcode"), phone=p.get("tel"), website=p.get("website"),
            rating=_f(p.get("rating")), rating_scale_max=10.0 if p.get("rating") is not None else None,
            price=int(price) if isinstance(price, (int, float)) and 1 <= price <= 4 else None,
            source_url=None, retrieved_at=utc_now_iso(), attribution=self.attribution, raw=raw,
        )
