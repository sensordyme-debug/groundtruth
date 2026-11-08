"""Test helpers: response bodies shaped like the documented schemas (see the adapter module docstrings for the doc
URLs) and a scripted httpx.MockTransport. Used only by tests/test_places*.py; never by runtime code.
All names/addresses are of public businesses (no people data); phone numbers are fictitious 555 numbers."""
from __future__ import annotations

import copy
from typing import Any, Callable

import httpx

NODA_BBOX = (-80.825777, 35.226994, -80.785777, 35.266994)

FSQ_SEARCH = {"results": [
    {"fsq_place_id": "fsq_amelies_0001", "name": "Amélie's French Bakery & Café", "latitude": 35.24680,
     "longitude": -80.80670,
     "categories": [{"fsq_category_id": "cat-bakery", "name": "Bakery", "short_name": "Bakery",
                     "plural_name": "Bakeries", "icon": {"prefix": "x", "suffix": ".png"}},
                    {"fsq_category_id": "cat-cafe", "name": "Café", "short_name": "Café", "plural_name": "Cafés",
                     "icon": {}}],
     "location": {"address": "2424 N Davidson St", "locality": "Charlotte", "region": "NC", "postcode": "28205",
                  "country": "US", "formatted_address": "2424 N Davidson St, Charlotte, NC 28205"},
     "tel": "(704) 555-0101", "website": "https://example.com/amelies", "distance": 120},
    {"fsq_place_id": "fsq_smellycat_0002", "name": "Smelly Cat Coffeehouse", "latitude": 35.24700,
     "longitude": -80.80800, "categories": [{"fsq_category_id": "cat-coffee", "name": "Coffee Shop"}],
     "location": {"address": "514 E 36th St", "locality": "Charlotte", "region": "NC", "postcode": "28205"},
     "tel": "(704) 555-0102"},
    {"fsq_place_id": "fsq_nocoords_0003", "name": "Ghost Kitchen", "categories": [],
     "location": {"locality": "Charlotte", "region": "NC"}},
    {"fsq_place_id": "fsq_outside_0004", "name": "Far Away Cafe", "latitude": 35.30, "longitude": -80.70,
     "categories": [{"name": "Café"}], "location": {"address": "1 Elsewhere Rd", "locality": "Charlotte"}},
]}

YELP_SEARCH = {"businesses": [
    {"id": "yelpAmelies22charsXXXXX", "alias": "amelies-french-bakery-charlotte", "name": "Amelie's French Bakery",
     "image_url": "https://example.com/photo.jpg", "is_closed": False,
     "url": "https://www.yelp.com/biz/amelies-french-bakery-charlotte?adjust_creative=t", "review_count": 3000,
     "categories": [{"alias": "bakeries", "title": "Bakeries"}, {"alias": "cafes", "title": "Cafes"}],
     "rating": 4.5, "coordinates": {"latitude": 35.24685, "longitude": -80.80665}, "transactions": [],
     "price": "$", "location": {"address1": "2424 N Davidson St", "address2": "Ste 102", "address3": "",
                                "city": "Charlotte", "zip_code": "28205", "country": "US", "state": "NC",
                                "display_address": ["2424 N Davidson St", "Ste 102", "Charlotte, NC 28205"]},
     "phone": "+17045550101", "display_phone": "(704) 555-0101", "distance": 50.2},
    {"id": "yelpSmellyCat22charsXXXX", "alias": "smelly-cat-coffee-charlotte", "name": "Smelly Cat Coffee",
     "url": "https://www.yelp.com/biz/smelly-cat-coffee-charlotte", "review_count": 900,
     "categories": [{"alias": "coffee", "title": "Coffee & Tea"}], "rating": 4.5,
     "coordinates": {"latitude": 35.24702, "longitude": -80.80795}, "price": "$$",
     "location": {"address1": "514 E 36th St", "city": "Charlotte", "zip_code": "28205", "state": "NC",
                  "country": "US", "display_address": ["514 E 36th St", "Charlotte, NC 28205"]},
     "phone": "+17045550102", "display_phone": "(704) 555-0102"},
    {"id": "yelpNullCoords22charsXXX", "alias": "x", "name": "Null Coords Diner",
     "coordinates": {"latitude": None, "longitude": None}, "location": {"address1": ""}, "phone": "",
     "display_phone": ""},
], "total": 3, "region": {"center": {"latitude": 35.247, "longitude": -80.806}}}

YELP_REVIEWS = {"total": 3000, "possible_languages": ["en"], "reviews": [
    {"id": "rev1", "url": "https://www.yelp.com/biz/amelies?hrid=rev1", "text": "Great pastries and coffee...",
     "rating": 5, "time_created": "2026-10-01 10:00:00",
     "user": {"id": "user123", "profile_url": "https://www.yelp.com/user_details?userid=user123",
              "image_url": "https://example.com/u.jpg", "name": "Pat Q."}}]}

GOOGLE_TEXT_SEARCH = {"places": [
    {"id": "ChIJ_amelies_test", "displayName": {"text": "Amélie's French Bakery & Café", "languageCode": "en"},
     "formattedAddress": "2424 N Davidson St #102, Charlotte, NC 28205, USA",
     "location": {"latitude": 35.24683, "longitude": -80.80668},
     "types": ["bakery", "cafe", "food", "point_of_interest", "establishment"], "primaryType": "bakery",
     "googleMapsUri": "https://maps.google.com/?cid=1",
     "addressComponents": [
         {"longText": "2424", "shortText": "2424", "types": ["street_number"]},
         {"longText": "North Davidson Street", "shortText": "N Davidson St", "types": ["route"]},
         {"longText": "Charlotte", "shortText": "Charlotte", "types": ["locality", "political"]},
         {"longText": "Mecklenburg County", "shortText": "Mecklenburg County",
          "types": ["administrative_area_level_2", "political"]},
         {"longText": "North Carolina", "shortText": "NC", "types": ["administrative_area_level_1", "political"]},
         {"longText": "28205", "shortText": "28205", "types": ["postal_code"]}]},
]}

GOOGLE_GEOCODE_OK = {"status": "OK", "results": [
    {"formatted_address": "600 E 4th St, Charlotte, NC 28202, USA",
     "geometry": {"location": {"lat": 35.2207, "lng": -80.8366}}, "place_id": "ChIJ_geo", "types": ["street_address"]}]}


def body(b: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(b)


class Script:
    """A MockTransport that answers from a list of responses/exceptions per (host, path-prefix) and records the
    requests it saw (for header assertions)."""

    def __init__(self, routes: dict[str, list[Any] | Callable[[httpx.Request], Any]]):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.requests: list[httpx.Request] = []

    def _answer(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        target = f"{req.url.host}{req.url.path}"
        for prefix in sorted(self.routes, key=len, reverse=True):
            if target.startswith(prefix):
                r = self.routes[prefix]
                item = r(req) if callable(r) else (r.pop(0) if len(r) > 1 else r[0])
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, httpx.Response):
                    return item
                status, payload, *rest = item if isinstance(item, tuple) else (200, item)
                return httpx.Response(status, json=payload, headers=rest[0] if rest else {})
        return httpx.Response(404, json={"error": "no route in test script"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)
