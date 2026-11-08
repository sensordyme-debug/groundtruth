"""Google Maps Platform adapter: Geocoding, Places API (New) Text Search / Place Details, Address Validation.

DISABLED BY DEFAULT (conf/providers.json ``google.enabled = false``):
1. The project owner's standing rule is "no Google data" (docs/HANDOFF.md, docs/LICENCE_GATE.md).
2. Google Maps Platform Terms §3.2.3 "No Use With Non-Google Maps" (as quoted from the terms; the live terms page
   could not be fetched on 2026-11-08, so the exact current wording/numbering MUST be checked by a human):
   "Customer will not use the Google Maps Core Services with or near a non-Google Map in a Customer Application",
   with the example of displaying Places listings/content on a non-Google map. This app's map is MapLibre over NAIP
   imagery, i.e. a non-Google map. https://cloud.google.com/maps-platform/terms
   https://cloud.google.com/maps-platform/terms/maps-service-terms
The code works when enabled, but enabling it is a human/legal decision (docs/compliance/API_TERMS_REVIEW.md).

Docs read 2026-11-08:
- Text Search (New): POST https://places.googleapis.com/v1/places:searchText, headers X-Goog-Api-Key and
  X-Goog-FieldMask (required; no spaces), body {textQuery, locationRestriction.rectangle{low,high}, includedType,
  pageSize 1..20} -> {places[], nextPageToken}. Max 60 results across pages.
  https://developers.google.com/maps/documentation/places/web-service/text-search
  SKU by field: Pro = displayName, formattedAddress, location, types, primaryType, googleMapsUri, addressComponents;
  Enterprise = rating, priceLevel, nationalPhoneNumber, websiteUri (billed at the highest SKU requested).
- Place Details (New): GET https://places.googleapis.com/v1/places/{id}, field mask without the "places." prefix.
  https://developers.google.com/maps/documentation/places/web-service/place-details
- Geocoding: GET https://maps.googleapis.com/maps/api/geocode/json?address=...&key=... ; HTTP 200 with body
  ``status`` OK | ZERO_RESULTS | OVER_DAILY_LIMIT | OVER_QUERY_LIMIT | REQUEST_DENIED | INVALID_REQUEST | UNKNOWN_ERROR.
  https://developers.google.com/maps/documentation/geocoding/requests-geocoding
  The key travels as a query parameter here, so the HTTP helper never logs URLs or exception text.
- Address Validation: POST https://addressvalidation.googleapis.com/v1:validateAddress (only when
  ``address_validation_enabled``). https://developers.google.com/maps/documentation/address-validation
- Policies (caching, attribution): place IDs may be stored indefinitely; latitude/longitude may be cached for up to
  30 consecutive calendar days; attribution = Google Maps logo, or the text "Google Maps" where space is limited,
  plus any third-party attributions returned.
  https://developers.google.com/maps/documentation/places/web-service/policies
  https://developers.google.com/maps/documentation/geocoding/policies
- Pricing: https://developers.google.com/maps/billing-and-pricing/pricing
"""
from __future__ import annotations

from typing import Any

from .base import BaseProvider, PlaceRecord, ProviderError, _f, utc_now_iso, valid_coords

PLACES_URL = "https://places.googleapis.com/v1"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ADDRESS_VALIDATION_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"
PRO_FIELDS = ["id", "displayName", "formattedAddress", "location", "types", "primaryType", "googleMapsUri",
              "addressComponents"]
ENTERPRISE_FIELDS = ["rating", "userRatingCount", "priceLevel", "nationalPhoneNumber", "websiteUri"]
PRICE_LEVELS = {"PRICE_LEVEL_FREE": None, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
                "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}


def _geocode_status(body: Any) -> str | None:
    st = body.get("status") if isinstance(body, dict) else None
    if st in ("OK", "ZERO_RESULTS"):
        return None
    if st == "REQUEST_DENIED":
        return "auth"
    if st in ("OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"):
        return "rate_limited"
    return str(st or "missing_status")


class GoogleProvider(BaseProvider):
    name = "google"
    capabilities = frozenset({"search", "details", "geocode", "address_validation"})
    source_name = "Google Maps Platform (Places API New / Geocoding)"
    source_url = "https://developers.google.com/maps/documentation/places/web-service/text-search"
    licence = "Google Maps Platform Terms of Service (proprietary; lat/lng cache <= 30 days; place_id storable; " \
              "no use with or near a non-Google map)"
    attribution = "Google Maps"

    def _enterprise(self) -> bool:
        return bool(self.pconf.get("request_enterprise_fields"))

    def _mask(self, prefix: str = "") -> str:
        fields = PRO_FIELDS + (ENTERPRISE_FIELDS if self._enterprise() else [])
        return ",".join(f"{prefix}{f}" for f in fields)

    def _headers(self, key: str, mask: str | None = None) -> dict[str, str]:
        h = {"X-Goog-Api-Key": key, "Content-Type": "application/json"}
        if mask:
            h["X-Goog-FieldMask"] = mask
        return h

    # ------------------------------------------------------------------------------------------------ endpoints
    def search(self, query: str, bbox: tuple[float, float, float, float], categories: list[str] | None = None,
               limit: int = 20, investigation_id: str | None = None, *, allow_when_disabled: bool = False
               ) -> list[PlaceRecord]:
        key = self.require_enabled(allow_when_disabled=allow_when_disabled)
        w, s, e, n = bbox
        types = self.provider_filters(categories, "google")
        text = query.strip() or (categories[0].replace("_", " ") if categories else "")
        if not text:
            raise ProviderError(self.name, "not_supported", "Text Search needs a query or a category")
        body: dict[str, Any] = {
            "textQuery": text,
            "locationRestriction": {"rectangle": {"low": {"latitude": s, "longitude": w},
                                                  "high": {"latitude": n, "longitude": e}}},
            "pageSize": max(1, min(int(limit), 20)),
        }
        if types:
            body["includedType"] = types[0]  # the API takes a single Table-A type
        cat = "text_search_enterprise" if self._enterprise() else "text_search_pro"
        resp = self.http.request("POST", f"{PLACES_URL}/places:searchText", endpoint="places:searchText", category=cat,
                                 headers=self._headers(key, self._mask("places.")), json_body=body,
                                 investigation_id=investigation_id)
        places = resp.get("places", []) if isinstance(resp, dict) else None  # empty result => {} (no 'places')
        if not isinstance(places, list):
            raise ProviderError(self.name, "bad_response", "text search response 'places' is not a list")
        return [self.normalize(p) for p in places if isinstance(p, dict)]

    def details(self, place_id: str, investigation_id: str | None = None) -> PlaceRecord:
        key = self.require_enabled()
        cat = "details_enterprise" if self._enterprise() else "details_pro"
        resp = self.http.request("GET", f"{PLACES_URL}/places/{place_id}", endpoint="places/details", category=cat,
                                 headers=self._headers(key, self._mask()), investigation_id=investigation_id)
        if not isinstance(resp, dict):
            raise ProviderError(self.name, "bad_response", "details response is not an object")
        return self.normalize(resp)

    def geocode(self, address: str, bbox: tuple[float, float, float, float] | None = None,
                investigation_id: str | None = None, *, allow_when_disabled: bool = False) -> list[dict[str, Any]]:
        """[{formatted_address, lat, lon, place_id, types}] (bounds only BIAS results; the caller must filter)."""
        key = self.require_enabled(allow_when_disabled=allow_when_disabled)
        params: dict[str, Any] = {"address": address, "key": key}
        if bbox:
            w, s, e, n = bbox
            params["bounds"] = f"{s},{w}|{n},{e}"
        resp = self.http.request("GET", GEOCODE_URL, endpoint="geocode", category="geocode", params=params,
                                 investigation_id=investigation_id, ok_body=_geocode_status)
        out = []
        for r in resp.get("results") or []:
            loc = ((r.get("geometry") or {}).get("location") or {}) if isinstance(r, dict) else {}
            if valid_coords(loc.get("lat"), loc.get("lng")):
                out.append({"formatted_address": r.get("formatted_address"), "lat": float(loc["lat"]),
                            "lon": float(loc["lng"]), "place_id": r.get("place_id"), "types": r.get("types") or []})
        return out

    def validate_address(self, address_lines: list[str], region_code: str = "US",
                         investigation_id: str | None = None, *, allow_when_disabled: bool = False) -> dict[str, Any]:
        if not self.pconf.get("address_validation_enabled") and not allow_when_disabled:
            raise ProviderError(self.name, "disabled", "Address Validation is not enabled (conf/providers.json "
                                "google.address_validation_enabled)")
        key = self.require_enabled(allow_when_disabled=allow_when_disabled)
        resp = self.http.request("POST", ADDRESS_VALIDATION_URL, endpoint="validateAddress",
                                 category="address_validation", headers=self._headers(key),
                                 json_body={"address": {"regionCode": region_code, "addressLines": address_lines}},
                                 investigation_id=investigation_id)
        res = resp.get("result") if isinstance(resp, dict) else None
        if not isinstance(res, dict):
            raise ProviderError(self.name, "bad_response", "address validation response has no 'result'")
        verdict = res.get("verdict") or {}
        addr = res.get("address") or {}
        return {"formatted_address": addr.get("formattedAddress"), "verdict": verdict}

    def probe_apis(self, investigation_id: str | None = "probe") -> dict[str, str]:
        """Which Google APIs answer for this key: one minimal call each (live tests only). Values: ok | auth |
        rate_limited | http_error | ... (error codes, never messages with the key)."""
        bbox = (-80.85, 35.22, -80.83, 35.235)  # Uptown Charlotte
        probes = {
            "geocoding": lambda: self.geocode("600 E 4th St, Charlotte, NC", investigation_id=investigation_id,
                                              allow_when_disabled=True),
            "places_new": lambda: self.search("coffee", bbox, limit=1, investigation_id=investigation_id,
                                              allow_when_disabled=True),
            "address_validation": lambda: self.validate_address(["600 E 4th St", "Charlotte, NC 28202"],
                                                                investigation_id=investigation_id,
                                                                allow_when_disabled=True),
        }
        out: dict[str, str] = {}
        for name, fn in probes.items():
            try:
                fn()
                out[name] = "ok"
            except ProviderError as e:
                out[name] = e.code
        return out

    # ------------------------------------------------------------------------------------------------ normalize
    def normalize(self, p: dict[str, Any]) -> PlaceRecord:
        loc = p.get("location") if isinstance(p.get("location"), dict) else {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if not valid_coords(lat, lon):
            lat = lon = None
        comps = {}
        for c in p.get("addressComponents") or []:
            if isinstance(c, dict):
                for t in c.get("types") or []:
                    comps.setdefault(t, c)
        street = " ".join(x for x in [(comps.get("street_number") or {}).get("longText"),
                                      (comps.get("route") or {}).get("shortText")] if x) or None
        types = [t for t in p.get("types") or [] if isinstance(t, str)]
        dn = p.get("displayName") if isinstance(p.get("displayName"), dict) else {}
        raw: dict[str, Any] = {}
        for k in ("primaryType", "formattedAddress"):
            if p.get(k):
                raw[k] = p[k]
        rc = p.get("userRatingCount")
        return PlaceRecord(
            id=f"google:{p.get('id')}", provider="google", provider_place_id=p.get("id"), name=dn.get("text"),
            categories=self.normalize_categories(types, "google"), categories_raw=types,
            lat=_f(lat), lon=_f(lon), address=street,
            city=(comps.get("locality") or {}).get("longText"),
            county=(comps.get("administrative_area_level_2") or {}).get("longText"),
            state=(comps.get("administrative_area_level_1") or {}).get("shortText"),
            postal_code=(comps.get("postal_code") or {}).get("longText"),
            phone=p.get("nationalPhoneNumber"), website=p.get("websiteUri"),
            rating=_f(p.get("rating")), rating_scale_max=5.0 if p.get("rating") is not None else None,
            review_count=int(rc) if isinstance(rc, int) else None, price=PRICE_LEVELS.get(p.get("priceLevel")),
            source_url=p.get("googleMapsUri"), retrieved_at=utc_now_iso(), attribution=self.attribution, raw=raw,
        )
