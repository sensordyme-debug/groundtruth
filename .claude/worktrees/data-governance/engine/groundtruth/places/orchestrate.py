"""search_places: run the eligible place providers for one query + area, validate geography, resolve duplicates and
return one Envelope (tool "search_places").

- Provider choice: requested names (or conf "order"), filtered by capability ("search"), enabled state (conf + key),
  and today's caps; ordered by estimated cost per search (cheapest first), at most ``max_providers_per_search``.
- Error isolation: each provider runs on its own; a failure is reported per provider and the others still return
  (status "partial"). No provider able to run -> "declined".
- Geography: every record must have valid coordinates (never invented) and fall inside the requested area
  (point-in-bbox / point-in-polygon). Others are dropped and counted.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx
from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .. import config
from ..envelope import Envelope, Result, Source, StepTimer, declined, feature_collection
from .base import BaseProvider, PlaceRecord, ProviderError, UsageLedger, ledger_path, load_conf, utc_now_iso, \
    valid_coords
from .foursquare import FoursquareProvider
from .google import GoogleProvider
from .resolve import resolve
from .yelp import YelpProvider

TOOL = "search_places"
VERSION = "0.1.0"
PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    "foursquare": FoursquareProvider, "yelp": YelpProvider, "google": GoogleProvider}
SEARCH_COST_CATEGORY = {"foursquare": "search", "yelp": "search", "google": "text_search_pro"}


def build_providers(conf: dict[str, Any] | None = None, *, transports: dict[str, httpx.BaseTransport] | None = None,
                    ledger: UsageLedger | None = None, sleep: Callable[[float], None] = time.sleep
                    ) -> dict[str, BaseProvider]:
    conf = conf or load_conf()
    ledger = ledger or UsageLedger(ledger_path(conf))
    return {name: cls(conf, transport=(transports or {}).get(name), ledger=ledger, sleep=sleep)
            for name, cls in PROVIDER_CLASSES.items()}


# ------------------------------------------------------------------------------------------------------------ area
def parse_area(area: dict[str, Any] | None) -> tuple[BaseGeometry, tuple[float, float, float, float], str]:
    """{bbox:[w,s,e,n]} | {geojson: Geometry|Feature|FeatureCollection} | {place: name in conf/places.json}
    -> (geometry WGS84, bbox, label). Raises ValueError."""
    if not isinstance(area, dict):
        raise ValueError("area must be an object with 'bbox', 'geojson' or 'place'")
    if area.get("bbox") is not None:
        b = area["bbox"]
        if not (isinstance(b, (list, tuple)) and len(b) == 4):
            raise ValueError("bbox must be [west, south, east, north]")
        w, s, e, n = (float(v) for v in b)
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ValueError("bbox must satisfy west < east and south < north in WGS84 degrees")
        return box(w, s, e, n), (w, s, e, n), "bbox"
    if area.get("geojson") is not None:
        gj = area["geojson"]
        if not isinstance(gj, dict):
            raise ValueError("geojson must be an object")
        if gj.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in gj.get("features") or [] if (f or {}).get("geometry")]
            if not geoms:
                raise ValueError("geojson FeatureCollection has no geometries")
            g = unary_union(geoms)
        elif gj.get("type") == "Feature":
            g = shape(gj["geometry"])
        else:
            g = shape(gj)
        if g.is_empty or g.area <= 0:
            raise ValueError("geojson area must be a non-empty Polygon/MultiPolygon")
        if not g.is_valid:
            g = g.buffer(0)
        w, s, e, n = g.bounds
        if not (-180 <= w <= e <= 180 and -90 <= s <= n <= 90):
            raise ValueError("geojson must be WGS84 lon/lat")
        return g, (w, s, e, n), "geojson"
    if area.get("place"):
        places = json.loads((config.SENSES_PATH.parent / "places.json").read_text(encoding="utf-8"))["places"]
        name = str(area["place"])
        hit = places.get(name) or next((v for k, v in places.items() if k.lower() == name.lower()), None)
        if not hit:
            raise ValueError(f"unknown place {name!r} (conf/places.json)")
        w, s, e, n = hit["bbox"]
        return box(w, s, e, n), (w, s, e, n), f"place:{name}"
    raise ValueError("area must have 'bbox', 'geojson' or 'place'")


# ---------------------------------------------------------------------------------------------------- features
def _member_props(r: PlaceRecord) -> dict[str, Any]:
    return {
        "id": r.id, "provider": r.provider, "provider_place_id": r.provider_place_id, "name": r.name,
        "lat": r.lat, "lon": r.lon, "address": r.address, "phone": r.phone, "categories": r.categories,
        "categories_raw": r.categories_raw, "rating": r.rating, "rating_scale_max": r.rating_scale_max,
        "review_count": r.review_count, "source_url": r.source_url, "attribution": r.attribution,
        "retrieved_at": r.retrieved_at, "raw": r.raw,
    }


def _feature(c: dict[str, Any]) -> dict[str, Any]:
    rep: PlaceRecord = c["members"][0]
    props = {
        "cluster_id": c["cluster_id"], "match_status": c["match_status"], "corroboration": c["corroboration"],
        "providers": c["providers"], "name": rep.name, "categories": rep.categories,
        "address": rep.address, "city": rep.city, "county": rep.county, "state": rep.state,
        "postal_code": rep.postal_code, "phone": rep.phone, "website": rep.website, "price": rep.price,
        "source_url": rep.source_url, "attribution": sorted({m.attribution for m in c["members"] if m.attribution}),
        "provenance": "provider_reported", "coordinates_from": rep.id,
        "members": [_member_props(m) for m in c["members"]],
        "evidence": c["evidence"], "conflicts": c["conflicts"],
    }
    return {"type": "Feature", "id": c["cluster_id"], "geometry": {"type": "Point", "coordinates": [rep.lon, rep.lat]},
            "properties": props}


LIMITS = [
    "Coordinates are provider-reported and not verified against imagery; they are never interpolated or invented.",
    "corroboration counts distinct providers; providers can share upstream data, so corroboration is not "
    "independent verification.",
    "Each provider caps results per call (Foursquare 50, Yelp 50 within a <=40 km circle, Google 20/page); "
    "results are a sample, not a census.",
    "Ratings keep each provider's own scale and are never blended; do not display Yelp ratings next to other "
    "providers' ratings (Yelp API Terms §5(c), display requirements).",
    "Display rules: show each provider's attribution (Foursquare credit; Yelp logo + link back; Google Maps "
    "attribution). Provider content must not be stored beyond each provider's caching terms.",
]


# ------------------------------------------------------------------------------------------------------- search
def search_places(query: str, area: dict[str, Any], categories: list[str] | None = None,
                  providers: list[str] | None = None, investigation_id: str | None = None, *, limit: int = 20,
                  conf: dict[str, Any] | None = None, provider_objs: dict[str, BaseProvider] | None = None) -> Envelope:
    conf = conf or load_conf()
    timer = StepTimer()
    query = (query or "").strip()
    categories = [c for c in (categories or []) if isinstance(c, str) and c]
    if not query and not categories:
        return declined(TOOL, VERSION, "bad_request: give a query or at least one category")
    known_cats = {k for k in (conf.get("categories") or {}) if not k.startswith("_")}
    unknown_cats = [c for c in categories if c not in known_cats]
    try:
        with timer.step("area", "parse requested geography"):
            geom, bbox, area_label = parse_area(area)
    except (ValueError, KeyError, TypeError) as e:
        return declined(TOOL, VERSION, f"bad_area: {e}", steps=timer.steps)

    objs = provider_objs or build_providers(conf)
    explicit = providers is not None
    names = list(providers) if explicit else list(conf.get("order") or PROVIDER_CLASSES)
    per: dict[str, dict[str, Any]] = {}
    eligible: list[tuple[float, str]] = []
    with timer.step("select", "capability, enabled state, caps, cost"):
        for name in (names if explicit else list(dict.fromkeys(names + list(objs)))):
            p = objs.get(name)
            if p is None:
                per[name] = {"status": "skipped", "reason": "unknown provider", "requested": explicit}
                continue
            st = p.status()
            if "search" not in p.capabilities:
                per[name] = {"status": "skipped", "reason": "no search capability", "requested": explicit}
            elif not st["enabled"]:
                per[name] = {"status": "disabled", "reason": st["reason"], "key_present": st["key_present"],
                             "requested": explicit}
            elif not p.http.remaining(SEARCH_COST_CATEGORY.get(name, "search")):
                per[name] = {"status": "skipped", "reason": "spend_cap: daily request or USD cap reached",
                             "requested": explicit}
            else:
                cost = p.http.price(SEARCH_COST_CATEGORY.get(name, "search"))
                eligible.append((cost if cost is not None else float("inf"), name))
        eligible.sort(key=lambda t: (t[0], names.index(t[1]) if t[1] in names else 99))
        cap_n = int(conf.get("max_providers_per_search") or len(eligible) or 1)
        for _, name in eligible[cap_n:]:
            per[name] = {"status": "skipped", "reason": f"max_providers_per_search={cap_n}", "requested": explicit}
        chosen = [name for _, name in eligible[:cap_n]]

    if not chosen:
        reasons = "; ".join(f"{k}: {v.get('reason')}" for k, v in per.items())
        return declined(TOOL, VERSION, f"no_provider_available: {reasons or 'none configured'}",
                        result=Result(metrics={"providers": per}), steps=timer.steps)

    def run(name: str) -> tuple[str, list[PlaceRecord] | None, dict[str, Any]]:
        t0 = time.perf_counter()
        try:
            recs = objs[name].search(query, bbox, categories, limit=limit, investigation_id=investigation_id)
            return name, recs, {"status": "ok", "count_returned": len(recs),
                                "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except ProviderError as e:
            return name, None, {"status": "error", "error_code": e.code, "reason": e.message,
                                "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as e:  # isolation: one provider's bug must not sink the others (no message: may hold URLs)
            return name, None, {"status": "error", "error_code": "internal_error",
                                "reason": f"unexpected {type(e).__name__} in adapter",
                                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    with timer.step("providers", ", ".join(chosen)):
        with ThreadPoolExecutor(max_workers=len(chosen)) as ex:
            outcomes = list(ex.map(run, chosen))

    records: list[PlaceRecord] = []
    dropped_no_coords = dropped_outside = 0
    with timer.step("validate", "coordinates present + inside the requested area"):
        for name, recs, info in outcomes:
            info["requested"] = explicit
            per[name] = info
            if recs is None:
                continue
            kept = 0
            for r in recs:
                if not valid_coords(r.lat, r.lon):
                    dropped_no_coords += 1
                    continue
                if not geom.covers(Point(r.lon, r.lat)):
                    dropped_outside += 1
                    continue
                records.append(r)
                kept += 1
            info["count_kept"] = kept

    with timer.step("resolve", "cross-provider de-duplication (EPSG:32617 distances)"):
        clusters = resolve(records, conf, provider_order=list(conf.get("order") or []))
    features = [_feature(c) for c in clusters]

    ok = [n for n, _, i in outcomes if i["status"] == "ok"]
    failed = [n for n, _, i in outcomes if i["status"] != "ok"]
    unavailable_requested = [n for n, v in per.items() if v.get("requested") and v["status"] in ("disabled", "skipped")]
    if not ok:
        reasons = "; ".join(f"{n}: {per[n].get('reason')}" for n in failed)
        return declined(TOOL, VERSION, f"all_providers_failed: {reasons}",
                        result=Result(metrics={"providers": per}), steps=timer.steps, limits=LIMITS)
    status = "partial" if (failed or unavailable_requested) else "ok"

    limits = list(LIMITS)
    for n in failed:
        limits.append(f"{n} failed ({per[n].get('error_code')}): {per[n].get('reason')}; its places are missing.")
    for n in unavailable_requested:
        limits.append(f"{n} was requested but not run: {per[n].get('reason')}")
    if unknown_cats:
        limits.append(f"Unknown categories ignored for provider filters: {', '.join(unknown_cats)}")
    if "yelp" in ok:
        limits.append("Yelp searches a circle covering the area (radius <= 40 km); points outside the area were "
                      "dropped.")
    if "google" in ok:
        limits.append("Google Maps content: do not display with or near a non-Google map (Maps Platform terms); this "
                      "app's map is MapLibre/NAIP. Lat/lng may be cached at most 30 days.")

    today = utc_now_iso()[:10]
    sources = [Source(name=objs[n].source_name, url=objs[n].source_url, date=today, licence=objs[n].licence)
               for n in ok]
    ledger = next(iter(objs.values())).ledger
    metrics = {
        "count": len(features), "records_in": sum(len(r or []) for _, r, _ in outcomes),
        "records_kept": len(records), "dropped_outside_area": dropped_outside,
        "dropped_no_coordinates": dropped_no_coords,
        "corroborated": sum(1 for c in clusters if c["match_status"] == "corroborated"),
        "conflict": sum(1 for c in clusters if c["match_status"] == "conflict"),
        "single": sum(1 for c in clusters if c["match_status"] == "single"),
        "area": area_label, "bbox": list(bbox), "query": query, "categories": categories,
        "providers": per, "usage_today": ledger.usage_today(), "investigation_id": investigation_id,
    }
    return Envelope(
        tool=TOOL, version=VERSION, status=status, result=Result(geojson=feature_collection(features), metrics=metrics),
        confidence=None,
        method="Queried third-party place APIs for the area, dropped results without valid coordinates or outside the "
               "area, and merged cross-provider duplicates by distance (EPSG:32617) + name, or address + name.",
        assumptions=["Provider categories are mapped to normalized keys via conf/providers.json 'categories'.",
                     "Cluster coordinates are the reported coordinates of the highest-priority provider in the "
                     "cluster (conf 'order'), not an average."],
        limits=limits, sources=sources, steps=timer.steps,
    )
