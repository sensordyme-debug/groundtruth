"""Geography resolver: a place mention → a real geometry with a stated kind and source, or an explicit
ambiguity / "can't resolve". Never a guess.

    from groundtruth.geography import resolve, contains, filter_features, distance_m
    r = resolve("restaurants in Mecklenburg County")      # kind=county, the county polygon (never a neighbourhood)
    r = resolve("pools within 5 miles of Lake Norman")    # kind=radius, 8 046.72 m from the water polygon's edge
    r = resolve("9201 University City Blvd", geocoder=g)  # kind=address (no geocoder → ambiguous, no coordinate)
    contains(r, -80.84, 35.22); filter_features(r, feature_collection)

Data: data/boundaries/<version>/ (scripts/build_boundaries.py; version from conf/data_versions.json "boundaries",
default v1). GeoJSON is EPSG:4326; every distance / buffer / area is computed in EPSG:32617 (UTM 17N).

Deterministic rules (documented in docs/GEOSPATIAL_VALIDATION_REPORT.md):
  * Which mention: a place named in the text beats context.area; if several places are named and they nest
    ("Myers Park, Charlotte"), the most specific wins; if they do not nest, the first is used and ambiguous=True.
  * Which reading of one name, by layer priority: county > city/municipality (official boundary) > water body >
    landmark > OSM neighbourhood polygon > Nominatim neighbourhood bbox (approximate). Exact beats approximate.
  * ambiguous=True when another reading of the same name is a *different* entity whose geometry differs materially
    (IoU < 0.5), or a different kind of thing (a lake's water vs the lakeside area). An approximate bbox of the same
    OSM object as the chosen polygon is listed as a superseded alternative and does not make the result ambiguous.
  * Names that are common words (OSM neighbourhood names not in conf/places.json, towns outside the county) only
    count when introduced by a locative word ("in", "near", "around", "of", ...).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import shapely
from shapely.geometry import Point, box, mapping, shape
from shapely.geometry.base import BaseGeometry

from groundtruth import config
from groundtruth.geo import MEASURE_CRS, WGS84, to_measure, to_wgs84, transformer

VERSION = "0.1.0"
KINDS = ("county", "city", "municipality", "neighbourhood", "zip", "address", "radius", "polygon", "water_body",
         "landmark", "point")
MATERIAL_IOU = 0.5  # two readings of a name with IoU below this are "materially different"
NEAR_COUNTY_M = 50_000.0  # coordinates further than this from the county envelope are outside coverage
BUFFER_QUAD_SEGS = 64  # display polygon for radii; containment uses the exact distance, not this polygon
SIMPLIFY_M = 5.0  # simplification of very large radius anchors for the display polygon only

Geocoder = Callable[[str], Any]


class InvalidCoordinate(ValueError):
    """A coordinate that is non-finite, out of the EPSG:4326 range, swapped, or outside coverage."""


# ============================================================================ Resolution
@dataclass
class Resolution:
    kind: str | None  # one of KINDS, or None when nothing could be resolved
    name: str | None
    geometry: dict[str, Any] | None  # GeoJSON geometry, EPSG:4326
    bbox: tuple[float, float, float, float] | None
    source: str | None
    source_date: str | None
    approximate: bool = False
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""
    licence: str | None = None
    source_id: str | None = None
    matched_text: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    _shape: BaseGeometry | None = field(default=None, repr=False, compare=False)
    _anchor_m: BaseGeometry | None = field(default=None, repr=False, compare=False)  # radius anchor in EPSG:32617
    _shape_m: BaseGeometry | None = field(default=None, repr=False, compare=False)  # cached EPSG:32617 geometry

    @property
    def resolved(self) -> bool:
        return self.geometry is not None

    @property
    def status(self) -> str:
        """resolved | ambiguous (a geometry was chosen by rule, alternatives listed, or none could be chosen) |
        unresolved."""
        if self.ambiguous:
            return "ambiguous"
        return "resolved" if self.resolved else "unresolved"

    def shape(self) -> BaseGeometry:
        if self.geometry is None:
            raise ValueError(f"unresolved place: {self.reason}")
        if self._shape is None:
            self._shape = shape(self.geometry)
        return self._shape

    def shape_m(self) -> BaseGeometry:
        """The geometry in EPSG:32617 (metres), cached."""
        if self._shape_m is None:
            self._shape_m = to_measure(self.shape())
        return self._shape_m

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "geometry": self.geometry,
                "bbox": list(self.bbox) if self.bbox else None, "source": self.source,
                "source_date": self.source_date, "licence": self.licence, "source_id": self.source_id,
                "approximate": self.approximate, "alternatives": self.alternatives, "ambiguous": self.ambiguous,
                "status": self.status, "reason": self.reason, "matched_text": self.matched_text,
                "detail": self.detail, "version": VERSION}


def _unresolved(reason: str, *, kind: str | None = None, name: str | None = None, ambiguous: bool = False,
                matched: str | None = None, alternatives: list | None = None, detail: dict | None = None) -> Resolution:
    return Resolution(kind=kind, name=name, geometry=None, bbox=None, source=None, source_date=None,
                      ambiguous=ambiguous, reason=reason, matched_text=matched, alternatives=alternatives or [],
                      detail=detail or {})


def _gj(g: BaseGeometry) -> dict[str, Any]:
    """GeoJSON geometry dict with plain lists (what json.dumps / a client would produce)."""
    return json.loads(shapely.to_geojson(g))


def _bbox(g: BaseGeometry) -> tuple[float, float, float, float]:
    return tuple(round(v, 7) for v in g.bounds)  # type: ignore[return-value]


# ============================================================================ coordinates + CRS
def validate_coordinate(lon: Any, lat: Any, *, near_county_m: float | None = NEAR_COUNTY_M) -> tuple[float, float]:
    """→ (lon, lat) as floats, or raise InvalidCoordinate. Checks: numeric + finite; lon ∈ [-180, 180],
    lat ∈ [-90, 90]; a likely (lat, lon) swap; and (if near_county_m) within that distance of the county envelope."""
    try:
        x, y = float(lon), float(lat)
    except (TypeError, ValueError) as e:
        raise InvalidCoordinate(f"not a number: lon={lon!r}, lat={lat!r}") from e
    if not (math.isfinite(x) and math.isfinite(y)):
        raise InvalidCoordinate(f"non-finite coordinate: lon={x}, lat={y}")
    if not (-180.0 <= x <= 180.0) or not (-90.0 <= y <= 90.0):
        hint = " (looks like lat/lon are swapped)" if (-90 <= x <= 90 and -180 <= y <= 180) else ""
        raise InvalidCoordinate(f"out of EPSG:4326 range: lon={x}, lat={y}{hint}")
    if near_county_m is not None:
        try:
            gz = gazetteer()
        except FileNotFoundError:
            return x, y
        d = gz.county_env_m.distance(Point(*transformer(WGS84, MEASURE_CRS).transform(x, y)))
        if d > near_county_m:
            sw = gz.county_env_m.distance(Point(*transformer(WGS84, MEASURE_CRS).transform(y, x))) if (
                -180 <= y <= 180 and -90 <= x <= 90) else float("inf")
            hint = " (looks like lat/lon are swapped)" if sw <= near_county_m else ""
            raise InvalidCoordinate(f"outside coverage: ({x:.6f}, {y:.6f}) is {d / 1000:.1f} km from the Mecklenburg "
                                    f"County envelope (limit {near_county_m / 1000:.0f} km){hint}")
    return x, y


def is_valid_coordinate(lon: Any, lat: Any, *, near_county_m: float | None = NEAR_COUNTY_M) -> bool:
    try:
        validate_coordinate(lon, lat, near_county_m=near_county_m)
        return True
    except InvalidCoordinate:
        return False


def lonlat_to_utm(lon: float, lat: float) -> tuple[float, float]:
    """EPSG:4326 → EPSG:32617 (metres)."""
    return transformer(WGS84, MEASURE_CRS).transform(lon, lat)


def utm_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """EPSG:32617 → EPSG:4326."""
    return transformer(MEASURE_CRS, WGS84).transform(x, y)


def roundtrip_error_m(lon: float, lat: float) -> float:
    """Distance in metres between a point and itself after 4326 → 32617 → 4326 (measured in 32617)."""
    x, y = lonlat_to_utm(lon, lat)
    lon2, lat2 = utm_to_lonlat(x, y)
    x2, y2 = lonlat_to_utm(lon2, lat2)
    return float(math.hypot(x2 - x, y2 - y))


def _as_geom(a: Any) -> BaseGeometry:
    if isinstance(a, Resolution):
        return a.shape()
    if isinstance(a, BaseGeometry):
        return a
    if isinstance(a, dict):
        if a.get("type") == "Feature":
            return shape(a["geometry"])
        return shape(a)
    if isinstance(a, (tuple, list)) and len(a) == 2:
        return Point(*validate_coordinate(a[0], a[1], near_county_m=None))
    raise TypeError(f"cannot interpret {type(a).__name__} as a geometry")


def _as_geom_m(a: Any) -> BaseGeometry:
    if isinstance(a, Resolution):
        return a.shape_m()
    return to_measure(_as_geom(a))


def distance_m(a: Any, b: Any) -> float:
    """Shortest distance in metres (EPSG:32617) between two things: Resolution, shapely geometry (4326), GeoJSON
    geometry/Feature, or a (lon, lat) pair. 0 when they touch/overlap. A radius Resolution is its buffer polygon."""
    return float(_as_geom_m(a).distance(_as_geom_m(b)))


# ============================================================================ gazetteer
@dataclass
class Entry:
    name: str
    kind: str  # county | city | municipality | neighbourhood | zip | water_body | landmark | outside
    layer: str
    props: dict[str, Any]
    geom: BaseGeometry | None
    strict: bool = False  # only matched after a locative word ("in X")
    _geom_m: BaseGeometry | None = field(default=None, repr=False)

    @property
    def geom_m(self) -> BaseGeometry | None:
        if self._geom_m is None and self.geom is not None:
            self._geom_m = to_measure(self.geom)
        return self._geom_m

    @property
    def approximate(self) -> bool:
        return bool(self.props.get("approximate", False))

    @property
    def ids(self) -> set[str]:
        s = set(self.props.get("same_as") or [])
        if self.props.get("source_id"):
            s.add(self.props["source_id"])
        return s

    def ref(self) -> dict[str, Any]:
        return {"layer": self.layer, "name": self.name, "source_id": self.props.get("source_id")}


LAYER_PRIORITY = {"county": 0, "municipalities": 1, "water_bodies": 2, "landmarks": 3, "neighbourhoods_osm": 4,
                  "neighbourhoods_bbox": 5, "zcta": 6, "outside": 9}

# alias (normalised) → (canonical name, preferred layer or None)
ALIASES: dict[str, tuple[str, str | None]] = {
    "south park": ("SouthPark", None),
    "southpark": ("SouthPark", None),
    "uptown charlotte": ("Uptown", None),
    "downtown charlotte": ("Uptown", None),
    "center city": ("Uptown", None),
    "centre city": ("Uptown", None),
    "noda": ("NoDa", None),
    "north davidson": ("NoDa", None),
    "plaza midwood": ("Plaza Midwood", None),
    "uncc": ("UNC Charlotte", None),
    "unc charlotte": ("UNC Charlotte", None),
    "university of north carolina at charlotte": ("UNC Charlotte", None),
    "lkn": ("Lake Norman", None),
    "lake norman area": ("Lake Norman", "neighbourhoods_bbox"),
    "the lake norman area": ("Lake Norman", "neighbourhoods_bbox"),
    "town of matthews": ("Matthews", "municipalities"),
    "town of huntersville": ("Huntersville", "municipalities"),
    "town of cornelius": ("Cornelius", "municipalities"),
    "town of davidson": ("Davidson", "municipalities"),
    "town of mint hill": ("Mint Hill", "municipalities"),
    "town of pineville": ("Pineville", "municipalities"),
    "city of charlotte": ("Charlotte", "municipalities"),
    "charlotte city limits": ("Charlotte", "municipalities"),
}
COUNTY_PHRASES = ("mecklenburg county", "mecklenburg", "meck county", "charlotte mecklenburg", "the county",
                  "countywide", "county wide", "the whole county", "the entire county", "whole county",
                  "entire county", "all of mecklenburg", "all of the county")
METRO_PHRASES = ("greater charlotte", "charlotte metro", "charlotte metropolitan area", "charlotte area",
                 "charlotte region", "the charlotte area", "metro charlotte")
NOT_A_COUNTY = {"mecklenburg", "meck", "the", "whole", "entire", "this", "that", "our", "my", "same", "a", "any",
                "each", "every", "per", "by", "one", "which", "what", "other"}
LOCATIVE = r"(?:in|inside|near|around|at|of|from|within|throughout|across|to|by|about|outside)\s+(?:the\s+)?$"


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("`", "'")
    s = re.sub(r"-(?![\d.])|[_/]", " ", s)  # keep the sign of "-80.84"
    s = re.sub(r"[^\w'.,\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_key(s: str) -> str:
    return re.sub(r"[.,']", "", _norm(s))


class Gazetteer:
    """Every named geometry from data/boundaries/<version>/, indexed by normalised name."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not (self.root / "meta.json").exists():
            raise FileNotFoundError(f"boundary data not built: {self.root} (run scripts/build_boundaries.py)")
        self.meta = json.loads((self.root / "meta.json").read_text())
        self.entries: list[Entry] = []
        places_names = set()
        for layer in ("county", "municipalities", "water_bodies", "landmarks", "neighbourhoods_bbox",
                      "neighbourhoods_osm", "zcta"):
            p = self.root / f"{layer}.geojson"
            if not p.exists():
                continue
            for f in json.loads(p.read_text())["features"]:
                pr = f["properties"]
                kind = {"neighbourhood_bbox": "neighbourhood"}.get(pr["kind"], pr["kind"])
                e = Entry(pr["name"], kind, layer, pr, shape(f["geometry"]))
                if layer == "neighbourhoods_bbox":
                    places_names.add(_name_key(e.name))
                self.entries.append(e)
        for e in self.entries:  # OSM-only neighbourhood names are often ordinary words ("Cherry", "Gateway")
            if e.layer == "neighbourhoods_osm" and _name_key(e.name) not in places_names:
                e.strict = True
        p = self.root / "outside_municipalities.json"
        for n in (json.loads(p.read_text()) if p.exists() else []):
            self.entries.append(Entry(n, "outside", "outside", {"source": "OpenStreetMap admin_level=8 (outside "
                                                                          "Mecklenburg County)"}, None, strict=True))
        self.by_key: dict[str, list[Entry]] = {}
        for e in self.entries:
            if e.layer != "zcta":
                self.by_key.setdefault(_name_key(e.name), []).append(e)
        self.zips = {e.name: e for e in self.entries if e.layer == "zcta"}
        self.county = next(e for e in self.entries if e.layer == "county")
        self.county_m = to_measure(self.county.geom)
        self.county_env_m = to_measure(box(*self.county.geom.bounds))
        # one alternation, longest first → leftmost-longest, non-overlapping mention spans
        keys = set(self.by_key) | set(ALIASES)
        self._names_re = re.compile(r"(?<![\w'])(" + "|".join(re.escape(k) for k in sorted(keys, key=lambda k: (-len(k), k)))
                                    + r")(?![\w'])")

    def lookup(self, key: str) -> tuple[list[Entry], str | None]:
        canon, prefer = ALIASES.get(key, (None, None))
        cands = list(self.by_key.get(_name_key(canon) if canon else key, []))
        return cands, prefer

    def by_ref(self, ref: dict[str, Any]) -> Entry | None:
        for e in self.entries:
            if e.layer == ref.get("layer") and e.name == ref.get("name") and (
                    ref.get("source_id") is None or e.props.get("source_id") == ref.get("source_id")):
                return e
        return None


@lru_cache(maxsize=4)
def _gazetteer(root: str) -> Gazetteer:
    return Gazetteer(Path(root))


def boundaries_dir() -> Path:
    return config.data_dir("boundaries")


def gazetteer() -> Gazetteer:
    return _gazetteer(str(boundaries_dir()))


# ============================================================================ building a Resolution from entries
def _iou_m(am: BaseGeometry, bm: BaseGeometry) -> float:
    """IoU of two EPSG:32617 geometries."""
    u = am.union(bm).area
    return float(am.intersection(bm).area / u) if u > 0 else 0.0


def _alt(chosen: Entry, e: Entry) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": e.kind, "name": e.name, "layer": e.layer, "ref": e.ref(),
                         "source": e.props.get("source"), "approximate": e.approximate}
    if e.geom is None:
        d.update(material=True, superseded=False, note="outside Mecklenburg County (no geometry in coverage)")
        return d
    d["bbox"] = list(_bbox(e.geom))
    same = bool(chosen.ids & e.ids)
    iou = _iou_m(chosen.geom_m, e.geom_m) if chosen.geom is not None else 0.0
    d["iou_with_chosen"] = round(iou, 3)
    d["same_entity"] = same
    if same and chosen.kind == "water_body" and e.kind != "water_body":
        d.update(material=True, superseded=False,
                 note="same OSM object, but this reading is the lakeside AREA (land + water bbox), not the water")
    elif same and e.approximate and not chosen.approximate:
        d.update(material=False, superseded=True, note="approximate bbox of the same object; superseded")
    else:
        d.update(material=iou < MATERIAL_IOU, superseded=False,
                 note=("different entity with the same name" if not same else "same entity") +
                      (f"; geometries differ materially (IoU {iou:.2f})" if iou < MATERIAL_IOU else ""))
    return d


def _from_entries(cands: list[Entry], matched: str, prefer_layer: str | None = None) -> Resolution:
    cands = sorted(cands, key=lambda e: (0 if prefer_layer and e.layer == prefer_layer else 1,
                                         LAYER_PRIORITY.get(e.layer, 8), e.approximate,
                                         -(e.geom_m.area if e.geom is not None else 0.0),
                                         str(e.props.get("source_id"))))
    inside = [e for e in cands if e.geom is not None]
    if not inside:
        e = cands[0]
        return _unresolved(f"'{matched}' is {e.name}, outside Mecklenburg County (the area this app covers)",
                           kind=None, name=e.name, matched=matched,
                           alternatives=[{"kind": "outside", "name": e.name, "source": e.props.get("source")}])
    chosen = inside[0]
    alts = [_alt(chosen, e) for e in cands if e is not chosen]
    if prefer_layer and chosen.layer == prefer_layer:  # the wording itself picked the reading ("Lake Norman area")
        for a in alts:
            if a["material"]:
                a.update(material=False, note=a["note"] + "; not chosen: the wording selects the other reading")
    ambiguous = any(a["material"] for a in alts)
    rule = (f"layer priority (preferred '{prefer_layer}' by wording)" if prefer_layer else "layer priority "
            "county > municipality > water body > landmark > OSM neighbourhood polygon > neighbourhood bbox")
    why = f"'{matched}' → {chosen.name} ({chosen.kind}, {chosen.props.get('source')})"
    if alts:
        why += f"; chosen by {rule}"
    if ambiguous:
        why += "; AMBIGUOUS: " + "; ".join(f"{a['name']} [{a['layer']}]: {a['note']}" for a in alts if a["material"])
    if chosen.approximate:
        why += "; geometry is APPROXIMATE (a bounding box, not a mapped boundary)"
    if chosen.kind == "water_body" and chosen.props.get("area_in_county_km2") is not None:
        why += (f"; the water body extends beyond the county ({chosen.props['area_in_county_km2']} of "
                f"{chosen.props['area_km2']} km² inside)")
    return _resolution_of(chosen, matched, why, alts, ambiguous)


def _resolution_of(e: Entry, matched: str | None, reason: str, alts: list | None = None,
                   ambiguous: bool = False) -> Resolution:
    pr = e.props
    detail = {"layer": e.layer}
    for k in ("area_km2", "fraction_in_county", "clipped_to_county", "municipal_type", "place", "osm_type",
              "area_in_county_km2"):
        if k in pr:
            detail[k] = pr[k]
    return Resolution(kind=e.kind, name=e.name, geometry=_gj(e.geom), bbox=_bbox(e.geom), source=pr.get("source"),
                      source_date=pr.get("source_date"), approximate=e.approximate, alternatives=alts or [],
                      ambiguous=ambiguous, reason=reason, licence=pr.get("licence"), source_id=pr.get("source_id"),
                      matched_text=matched, detail=detail, _shape=e.geom, _shape_m=e.geom_m)


# ============================================================================ parsing
UNIT_M = {"mile": 1609.344, "miles": 1609.344, "mi": 1609.344, "km": 1000.0, "kms": 1000.0, "kilometre": 1000.0,
          "kilometres": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0, "m": 1.0, "metre": 1.0, "metres": 1.0,
          "meter": 1.0, "meters": 1.0, "ft": 0.3048, "feet": 0.3048, "foot": 0.3048, "yard": 0.9144,
          "yards": 0.9144, "yd": 0.9144, "yds": 0.9144}
WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "half a": 0.5, "half an": 0.5, "a half": 0.5, "a quarter": 0.25, "quarter of a": 0.25}
_NUM = r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?|\.\d+|half an?|a half|quarter of a|a quarter|an?|one|two|three|four|five|six|seven|eight|nine|ten)"
_UNIT = r"(?P<unit>miles?|mi|kilometres?|kilometers?|kms?|metres?|meters?|m|feet|foot|ft|yards?|yds?)"
RADIUS_RE = re.compile(
    rf"(?:\bwithin|\binside|\bless than|\bunder|\bup to|\bat most)?\s*(?:a\s+)?\b{_NUM}\s*{_UNIT}(?![\w])\s*"
    rf"(?:radius\s+)?(?:of|from|around|to)\s+(?P<rest>.+)$")
RADIUS_BARE_RE = re.compile(rf"\bwithin\s+(?:a\s+)?\b{_NUM}\s*{_UNIT}(?![\w])\s*(?:radius)?\s*$")
STREET_SFX = (r"street|st|road|rd|avenue|ave|boulevard|blvd|drive|dr|lane|ln|way|parkway|pkwy|court|ct|place|pl|"
              r"highway|hwy|circle|cir|trail|trl|terrace|ter|loop|run|square|sq|crossing|xing|commons|row|pike|"
              r"path|plaza|alley|aly|bend|cove|cv|point|pt|ridge")
ADDRESS_RE = re.compile(
    rf"\b(?P<addr>\d{{1,6}}[a-z]?\s+(?:[a-z0-9'.]+\s+){{0,5}}?(?:{STREET_SFX})\b\.?"
    r"(?:\s*,?\s*(?:suite|ste|unit|apt|#)\s*[\w-]+)?"
    r"(?:\s*,\s*[a-z][a-z .]{1,30}?)?(?:\s*,?\s*(?:nc|north carolina))?(?:\s*,?\s*\d{5}(?:\s\d{4})?)?)(?![\w])")
ADDRESS_STOP = {"in", "on", "near", "at", "of", "with", "within", "the", "and", "for", "along", "by", "from", "around",
                "to", "inside", "than", "miles", "mile", "km", "m", "minutes", "minute"}
ZIP_RE = re.compile(r"(?:\bzip(?:\s*code)?\s*|\bzcta\s*)?(?<![\d.,])(?P<zip>\d{5})(?:\s\d{4})?(?![\d.,]*\d)(?!\s*(?:m|km|ft|feet|miles?|sq|acres?|%)\b)")
COORD_RE = re.compile(r"(?<![\d.])(?P<a>-?\d{1,3}\.\d+)\s*[, ]\s*(?P<b>-?\d{1,3}\.\d+)(?![\d.])")
OTHER_COUNTY_RE = re.compile(r"\b(?P<w>[a-z]+) county\b")


def _num(s: str) -> float:
    s = s.strip()
    if s in WORDNUM:
        return float(WORDNUM[s])
    return float(s.replace(",", ""))


@dataclass
class _Mention:
    start: int
    end: int
    text: str
    res: Resolution
    geom_m: BaseGeometry | None = None


def _find_address(t: str) -> tuple[int, int, str] | None:
    for m in ADDRESS_RE.finditer(t):
        words = m.group("addr").split()
        core = []
        for w in words[1:]:
            core.append(w.strip(".,"))
            if re.fullmatch(STREET_SFX, core[-1]):
                break
        if any(w in ADDRESS_STOP for w in core[:-1]):
            continue
        return m.start("addr"), m.end("addr"), m.group("addr").strip(" ,")
    return None


def _resolve_address(addr: str, geocoder: Geocoder | None) -> Resolution:
    if geocoder is None:
        return _unresolved("address geocoding unavailable", kind="address", name=addr, ambiguous=True, matched=addr)
    try:
        out = geocoder(addr)
    except Exception as e:  # noqa: BLE001 - a failing geocoder must not become a guess
        return _unresolved(f"address geocoder failed: {type(e).__name__}: {e}", kind="address", name=addr,
                           ambiguous=True, matched=addr)
    cands = [] if out is None else (list(out) if isinstance(out, (list, tuple)) else [out])
    good, bad = [], []
    for c in cands:
        try:
            lon, lat = validate_coordinate(c.get("lon"), c.get("lat"))
            good.append(c | {"lon": lon, "lat": lat})
        except (InvalidCoordinate, AttributeError) as e:
            bad.append(str(e))
    if not good:
        why = "geocoder returned no match" if not cands else "geocoder returned no usable coordinate (" + "; ".join(bad) + ")"
        return _unresolved(why, kind="address", name=addr, matched=addr)
    good.sort(key=lambda c: -float(c.get("score", 0.0)))  # stable: the geocoder's own order breaks ties
    best = good[0]
    pt = Point(best["lon"], best["lat"])
    bm = to_measure(pt)
    alts = []
    for c in good[1:]:
        d = bm.distance(to_measure(Point(c["lon"], c["lat"])))
        alts.append({"kind": "address", "name": c.get("label") or addr, "lon": c["lon"], "lat": c["lat"],
                     "score": c.get("score"), "dist_m_from_chosen": round(d, 1), "material": d > 50.0})
    ambiguous = any(a["material"] for a in alts)
    in_county = gazetteer().county.geom.intersects(pt)
    reason = f"address '{addr}' geocoded by {best.get('source', 'the injected geocoder')}"
    if ambiguous:
        reason += f"; AMBIGUOUS: {sum(a['material'] for a in alts)} other candidate(s) > 50 m away; highest score used"
    if not in_county:
        reason += "; NOTE: the geocoded point is outside Mecklenburg County"
    reason += "; an address is a point: use 'within N m of <address>' to search around it"
    return Resolution(kind="address", name=best.get("label") or addr, geometry=_gj(pt), bbox=_bbox(pt),
                      source=best.get("source", "injected geocoder"), source_date=best.get("source_date"),
                      approximate=bool(best.get("approximate", False)), alternatives=alts, ambiguous=ambiguous,
                      reason=reason, licence=best.get("licence"), matched_text=addr,
                      detail={"in_county": in_county, "score": best.get("score")}, _shape=pt)


def _coord_mention(m: re.Match) -> Resolution:
    a, b = float(m.group("a")), float(m.group("b"))
    tried = []
    for lon, lat in ((b, a), (a, b)):  # text usually says "lat, lon"; accept either order if only one is valid
        try:
            lon, lat = validate_coordinate(lon, lat)
            pt = Point(lon, lat)
            return Resolution(kind="point", name=f"{lat:.6f}, {lon:.6f}", geometry=_gj(pt), bbox=_bbox(pt),
                              source="coordinate given in the request", source_date=None, matched_text=m.group(0),
                              reason=f"coordinate read as lon={lon}, lat={lat}", _shape=pt)
        except InvalidCoordinate as e:
            tried.append(str(e))
    return _unresolved("invalid coordinate: " + " / ".join(tried), matched=m.group(0))


def _mentions(t: str, gz: Gazetteer, geocoder: Geocoder | None) -> list[_Mention]:
    """All place mentions in normalised text t, left to right, non-overlapping."""
    out: list[_Mention] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in taken)

    def add(s: int, e: int, res: Resolution) -> None:
        taken.append((s, e))
        out.append(_Mention(s, e, t[s:e], res))

    a = _find_address(t)
    if a:
        add(a[0], a[1], _resolve_address(a[2], geocoder))
    for m in COORD_RE.finditer(t):
        if free(m.start(), m.end()):
            add(m.start(), m.end(), _coord_mention(m))
    for m in re.finditer(r"(?<![\w])(" + "|".join(re.escape(p) for p in sorted(METRO_PHRASES, key=len, reverse=True)) + r")(?![\w])", t):
        if free(m.start(), m.end()):
            r = _from_entries(gz.lookup("charlotte")[0], m.group(0), "municipalities")
            r.ambiguous = True
            r.alternatives = r.alternatives + [{"kind": "county", "name": "Mecklenburg County", "layer": "county",
                                                "ref": gz.county.ref(), "material": True,
                                                "note": "the Charlotte metro area is larger than the city; the county "
                                                        "is the widest area covered"}]
            r.reason += "; AMBIGUOUS: 'Charlotte area' could mean the city limits or the wider county; city used"
            add(m.start(), m.end(), r)
    for m in re.finditer(r"(?<![\w])(" + "|".join(re.escape(p) for p in sorted(COUNTY_PHRASES, key=len, reverse=True)) + r")(?![\w])", t):
        if free(m.start(), m.end()):
            if m.group(0) == "mecklenburg" and re.match(r"\s+(?:ave|avenue|st|street|rd|road)\b", t[m.end():]):
                continue  # Mecklenburg Ave is a street, not the county
            r = _resolution_of(gz.county, m.group(0), f"'{m.group(0)}' → Mecklenburg County (county boundary, "
                                                       f"{gz.county.props.get('source')})")
            add(m.start(), m.end(), r)
    for m in OTHER_COUNTY_RE.finditer(t):
        if m.group("w") not in NOT_A_COUNTY and free(m.start(), m.end()):
            name = m.group("w").title() + " County"
            add(m.start(), m.end(), _unresolved(f"'{m.group(0)}' is {name}, outside Mecklenburg County (the area "
                                                f"this app covers)", name=name, matched=m.group(0)))
    for m in gz._names_re.finditer(t):
        s, e = m.span()
        if not free(s, e):
            continue
        key = m.group(0)
        cands, prefer = gz.lookup(key)
        if not cands:
            continue
        # a strict name counts after a locative word, or when it is the whole request ("Gastonia")
        locative = bool(re.search(r"\b" + LOCATIVE, t[:s])) or not (t[:s] + t[e:]).strip(" ,.?!")
        if not locative:
            if all(c.strict for c in cands):
                continue  # common-word names (OSM-only neighbourhoods, outside towns) count only after "in / near ..."
            cands = [c for c in cands if not c.strict]
        add(s, e, _from_entries(cands, key, prefer))
    for m in ZIP_RE.finditer(t):
        s, e = m.span("zip")
        z = m.group("zip")
        explicit = bool(re.match(r"\s*(zip|zcta)", m.group(0)))
        if not free(s, e) or not (explicit or "27006" <= z <= "28909"):
            continue
        if z in gz.zips:
            ze = gz.zips[z]
            why = (f"ZIP {z} → 2020 Census ZCTA {z} (approximates the USPS ZIP area)"
                   + ("; clipped to the county" if ze.props.get("clipped_to_county") else ""))
            add(s, e, _resolution_of(ze, z, why))
        else:
            add(s, e, _unresolved(f"ZIP {z}: no 2020 Census ZCTA {z} inside Mecklenburg County (outside the county, or "
                                  f"a PO-box/unique ZIP with no ZCTA)", kind="zip", name=z, matched=z))
    out.sort(key=lambda mm: mm.start)
    return out


def _nests(im: BaseGeometry, om: BaseGeometry) -> bool:
    """inner ⊂ outer (EPSG:32617): ≥ 90 % of the inner area inside, or a point within 1 m."""
    if im.area == 0:
        return om.buffer(1.0).intersects(im)
    return im.intersection(om).area / im.area >= 0.9


def _choose(ms: list[_Mention], t: str) -> Resolution:
    rs = [m for m in ms if m.res.resolved]
    if len(ms) == 1:
        return ms[0].res
    if not rs:  # several mentions, none resolvable: report the first, list the rest
        r = ms[0].res
        r.alternatives = r.alternatives + [{"kind": m.res.kind, "name": m.res.name, "reason": m.res.reason} for m in ms[1:]]
        return r
    by_area = sorted(rs, key=lambda m: (m.res.shape_m().area, m.start))
    unres = [m for m in ms if not m.res.resolved]
    if not unres and all(_nests(by_area[i].res.shape_m(), by_area[i + 1].res.shape_m()) for i in range(len(by_area) - 1)):
        r = by_area[0].res
        r.reason += ("; most specific of nested places (" + " ⊂ ".join(f"{m.res.name}" for m in by_area) + ")")
        r.detail = r.detail | {"within": [m.res.name for m in by_area[1:]]}
        return r
    first = ms[0]
    r = first.res
    others = [m for m in ms if m is not first]
    r.ambiguous = True
    r.alternatives = r.alternatives + [{"kind": m.res.kind, "name": m.res.name, "matched_text": m.text,
                                        "ref": None, "material": True, "note": "another place named in the request"}
                                       for m in others]
    r.reason += "; AMBIGUOUS: several places named that do not nest (" + ", ".join(
        repr(m.text) for m in ms) + "); the first is used"
    return r


def _radius(anchor: Resolution, radius_m: float, rtext: str) -> Resolution:
    if radius_m <= 0 or not math.isfinite(radius_m):
        return _unresolved(f"invalid radius {rtext!r}", kind="radius")
    if not anchor.resolved:
        return _unresolved(f"radius {rtext}: anchor not resolved ({anchor.reason})", kind="radius",
                           ambiguous=anchor.ambiguous, name=f"within {rtext} of {anchor.name or '?'}",
                           alternatives=anchor.alternatives,
                           detail={"radius_m": radius_m, "anchor": anchor.to_dict() | {"geometry": None}})
    am = anchor.shape_m()
    # display / prefilter polygon. For big anchors (a lake: ~10^5 vertices) buffer a simplified copy by r + tol so
    # the polygon is a SUPERSET of the exact set; contains()/filter_features() use the exact distance to `am`.
    # Positive buffers compose exactly ((A ⊕ D_a) ⊕ D_b = A ⊕ D_(a+b)), so a big radius is built in growing steps,
    # simplifying by SIMPLIFY_M between steps and adding that to the remaining radius (GEOS is ~50x faster this way).
    tol = 0.0
    if shapely.get_num_coordinates(am) > 2000:
        g, done, tol = am.simplify(SIMPLIFY_M, preserve_topology=False), 0.0, SIMPLIFY_M
        step = 100.0
        while done + step <= radius_m / 2:
            g = g.buffer(step, quad_segs=8).simplify(SIMPLIFY_M, preserve_topology=False)
            done, tol, step = done + step, tol + SIMPLIFY_M, step * 4
        poly_m = g.buffer(radius_m - done + tol, quad_segs=16)
    else:
        poly_m = am.buffer(radius_m, quad_segs=BUFFER_QUAD_SEGS)
    poly = to_wgs84(poly_m)
    edge = "the edge of " if anchor.kind in ("water_body", "county", "city", "municipality", "neighbourhood",
                                              "polygon", "landmark", "zip") else ""
    reason = (f"within {radius_m:,.1f} m ({rtext}) of {edge}{anchor.name} [{anchor.kind}], measured in EPSG:32617; "
              f"containment uses the exact distance; anchor: {anchor.reason}")
    return Resolution(kind="radius", name=f"within {rtext} of {anchor.name}", geometry=_gj(poly), bbox=_bbox(poly),
                      source=anchor.source, source_date=anchor.source_date, approximate=anchor.approximate,
                      alternatives=anchor.alternatives, ambiguous=anchor.ambiguous, reason=reason,
                      licence=anchor.licence, source_id=anchor.source_id, matched_text=None,
                      detail={"radius_m": radius_m, "radius_text": rtext,
                              "anchor": anchor.to_dict() | {"geometry": None},
                              "measured_from": "edge" if edge else "point",
                              "display_polygon_margin_m": tol},
                      _shape=poly, _shape_m=poly_m, _anchor_m=am)


# ============================================================================ explicit specs (context / dict)
def _polygon_spec(spec: dict[str, Any], label: str | None = None) -> Resolution:
    try:
        if "bbox" in spec and spec["bbox"] is not None:
            b = [float(v) for v in spec["bbox"]]
            if len(b) != 4:
                raise ValueError("bbox needs 4 numbers [west, south, east, north]")
            validate_coordinate(b[0], b[1]); validate_coordinate(b[2], b[3])
            if not (b[0] < b[2] and b[1] < b[3]):
                raise ValueError(f"bbox must be [west, south, east, north] with west<east, south<north: {b}")
            g, what = box(*b), "bbox"
        else:
            gj = spec.get("geojson", spec.get("polygon", spec))
            if isinstance(gj, dict) and gj.get("type") == "FeatureCollection":
                g = shapely.union_all([shape(f["geometry"]) for f in gj["features"] if f.get("geometry")])
            elif isinstance(gj, dict) and gj.get("type") == "Feature":
                g = shape(gj["geometry"])
            else:
                g = shape(gj)
            what = "polygon"
            xy = shapely.get_coordinates(g)
            if xy.size == 0 or not np.isfinite(xy).all():
                raise ValueError("polygon has no / non-finite coordinates")
            for x, y in (xy.min(axis=0), xy.max(axis=0)):
                validate_coordinate(x, y)
    except (InvalidCoordinate, ValueError, TypeError, KeyError, AttributeError, shapely.errors.GEOSException) as e:
        return _unresolved(f"invalid area spec: {e}", kind="polygon")
    note = ""
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        return _unresolved(f"area spec must be a Polygon/MultiPolygon/bbox, got {g.geom_type}", kind="polygon")
    if not g.is_valid:
        a0 = to_measure(g).area
        g = shapely.make_valid(g)
        g = shapely.union_all([p for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")])
        note = f"; the polygon was invalid (e.g. self-intersecting) and was repaired (area {a0:.0f} → {to_measure(g).area:.0f} m²)"
    if g.is_empty or to_measure(g).area <= 0:
        return _unresolved("area spec has zero area", kind="polygon")
    gz = gazetteer()
    if not gz.county_m.intersects(to_measure(g)):
        note += "; NOTE: the area does not overlap Mecklenburg County"
    return Resolution(kind="polygon", name=label or spec.get("label") or f"Custom area ({what})",
                      geometry=_gj(g), bbox=_bbox(g), source="user-supplied " + what, source_date=None,
                      reason=f"explicit {what} from the request/context (closed set: points on the boundary are inside)"
                             + note, detail={"area_m2": round(to_measure(g).area, 1)}, _shape=g)


def _spec(spec: dict[str, Any], context: dict[str, Any] | None, geocoder: Geocoder | None) -> Resolution:
    gz = gazetteer()
    if spec.get("ref"):
        e = gz.by_ref(spec["ref"])
        if e is None or e.geom is None:
            return _unresolved(f"unknown reference {spec['ref']}")
        return _resolution_of(e, None, f"explicit reference to {e.name} [{e.layer}]")
    if spec.get("place"):
        return resolve(str(spec["place"]), None, geocoder=geocoder)
    if spec.get("address"):
        return _resolve_address(str(spec["address"]), geocoder)
    if spec.get("point") is not None or ("lon" in spec and "lat" in spec):
        lon, lat = spec["point"] if spec.get("point") is not None else (spec["lon"], spec["lat"])
        try:
            lon, lat = validate_coordinate(lon, lat)
        except InvalidCoordinate as e:
            return _unresolved(f"invalid point: {e}", kind="point")
        pt = Point(lon, lat)
        anchor = Resolution(kind="point", name=spec.get("label") or f"{lat:.6f}, {lon:.6f}", geometry=_gj(pt),
                            bbox=_bbox(pt), source="user-supplied point", source_date=None,
                            reason="explicit point", _shape=pt)
        if spec.get("radius_m") is not None:
            return _radius(anchor, float(spec["radius_m"]), f"{float(spec['radius_m']):g} m")
        return anchor
    if spec.get("bbox") is not None or spec.get("geojson") is not None or spec.get("polygon") is not None or \
            spec.get("type") in ("Polygon", "MultiPolygon", "Feature", "FeatureCollection"):
        r = _polygon_spec(spec, spec.get("label"))
        if r.resolved and spec.get("radius_m") is not None:
            return _radius(r, float(spec["radius_m"]), f"{float(spec['radius_m']):g} m")
        return r
    return _unresolved(f"unrecognised area spec keys: {sorted(spec)}")


# ============================================================================ resolve
def resolve(text_or_spec: str | dict[str, Any], context: dict[str, Any] | None = None, *,
            geocoder: Geocoder | None = None) -> Resolution:
    """Resolve a request's place to a geometry. ``text_or_spec`` is free text ("restaurants in Mecklenburg County")
    or a spec dict ({bbox} | {geojson} | GeoJSON | {place} | {address} | {point, radius_m?} | {ref}).
    ``context``: {"area": {place|bbox|geojson|label}, "point": [lon, lat] (anchor for a bare "within 2 km"),
    "geocoder": callable}. A place named in the text beats context.area. ``geocoder(address) -> None | {lon, lat,
    label?, score?, source?, source_date?, licence?} | [those]``; without one an address is never given a coordinate."""
    ctx = dict(context or {})
    geocoder = geocoder or ctx.get("geocoder")
    if isinstance(text_or_spec, dict):
        return _spec(text_or_spec, ctx, geocoder)
    if not isinstance(text_or_spec, str):
        return _unresolved(f"cannot resolve a {type(text_or_spec).__name__}")
    gz = gazetteer()
    t = _norm(text_or_spec)

    # radius: "within N unit of <anchor>"
    m = RADIUS_RE.search(t)
    if m:
        rtext = f"{m.group('num')} {m.group('unit')}"
        radius_m = _num(m.group("num")) * UNIT_M[m.group("unit")]
        rest = m.group("rest")
        before = t[: m.start()]
        ms = _mentions(rest, gz, geocoder)
        if not ms:
            return _unresolved(f"radius {rtext}: no recognisable place after '{m.group(0)[:40]}'", kind="radius",
                               detail={"radius_m": radius_m})
        anchor = ms[0].res
        r = _radius(anchor, radius_m, rtext)
        r.matched_text = m.group(0).strip()
        extra = [mm for mm in ms[1:]] + _mentions(before, gz, geocoder)
        if extra:
            r.ambiguous = True
            r.alternatives = r.alternatives + [{"kind": x.res.kind, "name": x.res.name, "matched_text": x.text,
                                                "material": True, "note": "also named in the request; NOT applied"}
                                               for x in extra]
            r.reason += "; AMBIGUOUS: other places also named and not applied: " + ", ".join(repr(x.text) for x in extra)
        return r
    m = RADIUS_BARE_RE.search(t)
    if m and ctx.get("point") is not None:
        rtext = f"{m.group('num')} {m.group('unit')}"
        anchor = _spec({"point": ctx["point"], "label": "the point on the map"}, None, None)
        return _radius(anchor, _num(m.group("num")) * UNIT_M[m.group("unit")], rtext)

    ms = _mentions(t, gz, geocoder)
    if ms:
        return _choose(ms, t)
    area = ctx.get("area") or {k: ctx[k] for k in ("bbox", "geojson", "polygon") if ctx.get(k) is not None}
    if area:
        r = _spec(area if isinstance(area, dict) else {"place": area}, None, geocoder)
        r.reason = "no place named in the text; using context.area: " + r.reason
        return r
    if m:
        return _unresolved(f"radius '{m.group(0).strip()}' without an anchor place or context point", kind="radius")
    return _unresolved("no place named in the request and no area in context")


# ============================================================================ containment
def _xy(lon: Any, lat: Any) -> tuple[float, float]:
    return validate_coordinate(lon, lat, near_county_m=None)


def contains(resolution: Resolution, lon: float, lat: float) -> bool:
    """Exact containment of one point. Polygons are CLOSED sets: a point exactly on the boundary is inside.
    A radius uses the exact EPSG:32617 distance to its anchor (≤ radius), not the display polygon."""
    x, y = _xy(lon, lat)
    if resolution._anchor_m is not None:
        return bool(shapely.dwithin(resolution._anchor_m, Point(*lonlat_to_utm(x, y)), resolution.detail["radius_m"]))
    g = resolution.shape()
    shapely.prepare(g)
    return bool(shapely.intersects_xy(g, x, y))


def _feature_geoms(features: Any) -> list[dict[str, Any]]:
    if isinstance(features, dict):
        return list(features.get("features", []))
    return list(features)


def filter_features(resolution: Resolution, features: Any, predicate: str = "representative_point") -> list[dict]:
    """Features (FeatureCollection or list) inside the resolution, in input order. predicate:
      representative_point (default): points by their location, other geometries by shapely's representative
          point, so each feature lands in exactly one of a set of non-overlapping areas;
      intersects: any overlap.
    Features with no / non-finite geometry are dropped."""
    feats = _feature_geoms(features)
    if not feats:
        return []
    geoms = []
    for f in feats:
        try:
            g = shape(f["geometry"]) if f.get("geometry") else None
        except Exception:  # noqa: BLE001
            g = None
        geoms.append(g)
    ok = np.array([g is not None and not g.is_empty and np.isfinite(shapely.get_coordinates(g)).all() for g in geoms])
    keep = np.zeros(len(feats), dtype=bool)
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        return []
    radius = resolution._anchor_m is not None
    if predicate == "representative_point":
        pts = [geoms[i] if geoms[i].geom_type == "Point" else geoms[i].representative_point() for i in idx]
        xs = np.array([p.x for p in pts]); ys = np.array([p.y for p in pts])
        if radius:
            ux, uy = transformer(WGS84, MEASURE_CRS).transform(xs, ys)
            keep[idx] = shapely.dwithin(resolution._anchor_m, shapely.points(ux, uy), resolution.detail["radius_m"])
        else:
            g = resolution.shape(); shapely.prepare(g)
            keep[idx] = shapely.intersects_xy(g, xs, ys)
    elif predicate == "intersects":
        arr = np.array([geoms[i] for i in idx], dtype=object)
        if radius:
            keep[idx] = shapely.dwithin(resolution._anchor_m, np.array([to_measure(g) for g in arr], dtype=object),
                                        resolution.detail["radius_m"])
        else:
            g = resolution.shape(); shapely.prepare(g)
            keep[idx] = shapely.intersects(g, arr)
    else:
        raise ValueError(f"unknown predicate {predicate!r}")
    return [f for f, k in zip(feats, keep) if k]


def points_mask(resolution: Resolution, lons: Sequence[float], lats: Sequence[float]) -> np.ndarray:
    """Vectorised contains() for arrays of coordinates (non-finite → False)."""
    xs, ys = np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)
    fin = np.isfinite(xs) & np.isfinite(ys) & (np.abs(xs) <= 180) & (np.abs(ys) <= 90)
    out = np.zeros(xs.shape, dtype=bool)
    if not fin.any():
        return out
    if resolution._anchor_m is not None:
        ux, uy = transformer(WGS84, MEASURE_CRS).transform(xs[fin], ys[fin])
        out[fin] = shapely.dwithin(resolution._anchor_m, shapely.points(ux, uy), resolution.detail["radius_m"])
    else:
        g = resolution.shape(); shapely.prepare(g)
        out[fin] = shapely.intersects_xy(g, xs[fin], ys[fin])
    return out


def names(kind: str | None = None) -> list[str]:
    """All resolvable names (optionally of one kind), sorted."""
    return sorted({e.name for e in gazetteer().entries if e.geom is not None and (kind is None or e.kind == kind)})


__all__: Iterable[str] = ("Resolution", "InvalidCoordinate", "resolve", "contains", "filter_features", "points_mask",
                          "distance_m", "validate_coordinate", "is_valid_coordinate", "lonlat_to_utm", "utm_to_lonlat",
                          "roundtrip_error_m", "gazetteer", "names", "KINDS", "VERSION")
