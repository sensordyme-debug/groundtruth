"""Geographic correctness benchmark for groundtruth.geography (inclusion AND exclusion).

Needs data/boundaries/v1 (scripts/build_boundaries.py). Coordinates are real places, each verified against the
boundary layers (docs/GEOSPATIAL_VALIDATION_REPORT.md lists them with their sources).
"""

from __future__ import annotations

import json
import math

import pytest
from shapely.geometry import Point

from groundtruth import geography as G
from groundtruth.geo import to_measure

try:
    G.gazetteer()
except FileNotFoundError as e:  # pragma: no cover
    pytest.skip(f"boundary data missing: {e}", allow_module_level=True)

# ---- real reference points (lon, lat)
UPTOWN = (-80.8431, 35.2271)            # Trade & Tryon ("the Square"), uptown Charlotte (ZCTA 28280, a unique ZIP)
ROMARE_BEARDEN = (-80.8487, 35.2272)    # Romare Bearden Park, uptown (ZCTA 28202)
GASTONIA = (-81.1873, 35.2621)          # downtown Gastonia, Gaston County
CONCORD = (-80.5795, 35.4088)           # downtown Concord, Cabarrus County
HUNTERSVILLE_DT = (-80.8428, 35.4107)   # downtown Huntersville (Main St / town hall)
MATTHEWS_DT = (-80.7237, 35.1168)       # downtown Matthews (Trade St)
MINT_HILL_DT = (-80.6473, 35.1796)      # Mint Hill town centre
QUEENS_UNIV = (-80.8318, 35.1888)       # Queens University, in Myers Park
SOUTHPARK_MALL = (-80.8295, 35.1534)    # SouthPark Mall (south of Myers Park)
DAVIDSON_COLLEGE = (-80.8445, 35.5005)  # Davidson College, town of Davidson
LN_WATER = (-80.9100, 35.5050)          # open water of Lake Norman, 838 m from shore, inside the county
CORNELIUS_3KM = (-80.8580, 35.4600)     # in the town of Cornelius, 2.98 km from Lake Norman's shore


def offset(lonlat, dx_m: float, dy_m: float):
    x, y = G.lonlat_to_utm(*lonlat)
    return G.utm_to_lonlat(x + dx_m, y + dy_m)


LN_20KM_SOUTH = offset(LN_WATER, 0, -20_000)

# (case id, query, point, expected inside?)
CASES = [
    ("county:uptown-in", "Mecklenburg County", UPTOWN, True),
    ("county:gastonia-out", "Mecklenburg County", GASTONIA, False),
    ("county:concord-out", "Mecklenburg County", CONCORD, False),
    ("charlotte:uptown-in", "Charlotte", UPTOWN, True),
    ("charlotte:huntersville-dt-out", "Charlotte", HUNTERSVILLE_DT, False),
    ("charlotte:matthews-dt-out", "Charlotte", MATTHEWS_DT, False),
    ("myers-park:queens-univ-in", "Myers Park", QUEENS_UNIV, True),
    ("myers-park:uptown-out", "Myers Park", UPTOWN, False),
    ("myers-park:southpark-mall-out", "Myers Park", SOUTHPARK_MALL, False),
    ("huntersville:downtown-in", "Huntersville", HUNTERSVILLE_DT, True),
    ("huntersville:uptown-out", "Huntersville", UPTOWN, False),
    ("huntersville:davidson-college-out", "Huntersville", DAVIDSON_COLLEGE, False),
    ("matthews:downtown-in", "Matthews", MATTHEWS_DT, True),
    ("matthews:uptown-out", "Matthews", UPTOWN, False),
    ("matthews:mint-hill-out", "Matthews", MINT_HILL_DT, False),
    ("lake-norman:water-in", "Lake Norman", LN_WATER, True),
    ("lake-norman:20km-south-out", "Lake Norman", LN_20KM_SOUTH, False),
    ("5mi-lake-norman:cornelius-3km-in", "within 5 miles of Lake Norman", CORNELIUS_3KM, True),
    ("5mi-lake-norman:huntersville-6km-in", "within 5 miles of Lake Norman", HUNTERSVILLE_DT, True),
    ("5mi-lake-norman:uptown-out", "within 5 miles of Lake Norman", UPTOWN, False),
    ("zip-28202:romare-bearden-park-in", "28202", ROMARE_BEARDEN, True),
    ("zip-28202:trade-and-tryon-out(28280)", "28202", UPTOWN, False),
    ("zip-28202:matthews-out", "28202", MATTHEWS_DT, False),
    ("restaurants-county:matthews-in", "restaurants in Mecklenburg County", MATTHEWS_DT, True),
    ("restaurants-county:gastonia-out", "restaurants in Mecklenburg County", GASTONIA, False),
]


@pytest.mark.parametrize("cid,query,pt,expected", CASES, ids=[c[0] for c in CASES])
def test_containment_benchmark(cid, query, pt, expected):
    r = G.resolve(query)
    assert r.resolved, r.reason
    assert G.contains(r, *pt) is expected


# ---------------------------------------------------------------- kinds, sources, the bug
def test_restaurants_in_county_is_the_county_not_myers_park():
    for q in ("restaurants in Mecklenburg County", "Find restaurants in Mecklenburg County", "restaurants countywide",
              "restaurants in the county"):
        r = G.resolve(q)
        assert (r.kind, r.name) == ("county", "Mecklenburg County"), q
        assert not r.approximate and not r.ambiguous and "TIGERweb" in r.source
    # a neighbourhood on screen (context) must not override a place named in the text
    r = G.resolve("restaurants in Mecklenburg County", {"area": {"place": "Myers Park"}})
    assert r.kind == "county"
    assert abs(to_measure(r.shape()).area / 1e6 - 1413.18) < 2.0  # the whole county, ~1413 km²
    assert not G.contains(r, *GASTONIA) and G.contains(r, *QUEENS_UNIV) and G.contains(r, *HUNTERSVILLE_DT)


def test_charlotte_is_the_city_not_the_county():
    r = G.resolve("parks in Charlotte")
    assert (r.kind, r.name, r.approximate) == ("city", "Charlotte", False)
    county = G.resolve("Mecklenburg County")
    assert to_measure(r.shape()).area < 0.9 * to_measure(county.shape()).area
    a = G.resolve("the Charlotte area")
    assert a.kind == "city" and a.ambiguous and any(x["kind"] == "county" for x in a.alternatives)


@pytest.mark.parametrize("name", ["Charlotte", "Cornelius", "Davidson", "Huntersville", "Matthews", "Mint Hill",
                                  "Pineville"])
def test_every_municipality_resolves_to_its_boundary(name):
    r = G.resolve(name)
    assert r.kind == ("city" if name == "Charlotte" else "municipality") and r.name == name
    assert not r.approximate and r.source and r.source_date and r.licence
    assert r.detail["fraction_in_county"] > 0.5


def test_matthews_bbox_is_superseded_not_ambiguous():
    r = G.resolve("Matthews")
    assert r.kind == "municipality" and not r.ambiguous
    alt = [a for a in r.alternatives if a["layer"] == "neighbourhoods_bbox"]
    assert alt and alt[0]["superseded"] and alt[0]["same_entity"] and not alt[0]["material"]


def test_lake_norman_is_ambiguous_water_vs_area():
    r = G.resolve("Lake Norman")
    assert r.kind == "water_body" and not r.approximate
    assert r.ambiguous and any(a["layer"] == "neighbourhoods_bbox" and a["material"] for a in r.alternatives)
    assert r.detail["area_km2"] > 100  # the whole reservoir, not only the county part
    area = G.resolve("homes in the Lake Norman area")
    assert area.kind == "neighbourhood" and area.approximate and not area.ambiguous


def test_myers_park_is_an_approximate_neighbourhood():
    r = G.resolve("pools in Myers Park")
    assert (r.kind, r.name, r.approximate) == ("neighbourhood", "Myers Park", True)
    assert "APPROXIMATE" in r.reason


def test_within_5_miles_of_lake_norman_measures_from_the_water_edge():
    r = G.resolve("pools within 5 miles of Lake Norman")
    assert r.kind == "radius" and r.detail["radius_m"] == pytest.approx(8046.72, abs=0.01)
    assert r.detail["anchor"]["kind"] == "water_body" and r.detail["measured_from"] == "edge"
    ln = G.resolve("Lake Norman")
    d = G.distance_m(ln, CORNELIUS_3KM)
    assert 2700 < d < 3300  # "~3 km from the shore"
    assert G.contains(r, *CORNELIUS_3KM) and not G.contains(r, *UPTOWN)
    # exactness at the 8 046.72 m edge: walk east from the Cornelius point to just inside / just outside
    base = G.distance_m(ln, CORNELIUS_3KM)
    inside = offset(CORNELIUS_3KM, 0, 0)
    for dx in range(0, 12_000, 50):
        p = offset(CORNELIUS_3KM, dx, 0)
        if G.distance_m(ln, p) > 8046.72 * 1.01:
            assert not G.contains(r, *p)
            break
        if G.distance_m(ln, p) < 8046.72 * 0.99:
            inside = p
            assert G.contains(r, *p)
            assert r.shape().covers(Point(*p))  # the display polygon is a superset of the exact set
    assert inside != CORNELIUS_3KM and base < 8046.72
    assert r.detail["display_polygon_margin_m"] <= 25  # how much larger than exact the display polygon may be


# ---------------------------------------------------------------- radius exactness
def test_radius_2km_point_exact_within_1_percent():
    r = G.resolve({"point": list(UPTOWN), "radius_m": 2000})
    t = G.resolve("within 2 km of 35.2271, -80.8431")  # text form, "lat, lon"
    assert r.kind == t.kind == "radius" and t.detail["radius_m"] == 2000.0
    for res in (r, t):
        for bearing in range(0, 360, 30):
            dx, dy = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
            p_in, p_out = offset(UPTOWN, 1980 * dx, 1980 * dy), offset(UPTOWN, 2020 * dx, 2020 * dy)
            assert G.contains(res, *p_in) and not G.contains(res, *p_out)
            assert G.distance_m(UPTOWN, p_in) == pytest.approx(1980, rel=0.01)
        # the display polygon's area is π r² within 1 %
        assert to_measure(res.shape()).area == pytest.approx(math.pi * 2000 ** 2, rel=0.01)


@pytest.mark.parametrize("text,metres", [("within 500 m of UNC Charlotte", 500), ("within 1.5 km of Uptown", 1500),
                                         ("within a mile of Matthews", 1609.344), ("within 300 ft of Dilworth", 91.44),
                                         ("within half a mile of NoDa", 804.672)])
def test_radius_units(text, metres):
    r = G.resolve(text)
    assert r.kind == "radius" and r.detail["radius_m"] == pytest.approx(metres)


# ---------------------------------------------------------------- addresses
def test_address_with_stub_geocoder():
    calls = []

    def geocoder(addr):
        calls.append(addr)
        return {"lon": -80.7337, "lat": 35.3074, "label": "9201 University City Blvd, Charlotte, NC 28223",
                "score": 0.98, "source": "stub-geocoder", "source_date": "2026-11-08"}

    r = G.resolve("9201 University City Blvd, Charlotte, NC 28223", geocoder=geocoder)
    assert r.kind == "address" and r.geometry["type"] == "Point" and not r.ambiguous
    assert r.geometry["coordinates"] == [-80.7337, 35.3074] and r.source == "stub-geocoder"
    assert calls == ["9201 university city blvd, charlotte, nc 28223"]
    rr = G.resolve("homes within 1 km of 9201 University City Blvd", geocoder=geocoder)
    assert rr.kind == "radius" and G.contains(rr, *offset((-80.7337, 35.3074), 900, 0))
    assert not G.contains(rr, *offset((-80.7337, 35.3074), 1100, 0))


def test_address_without_geocoder_is_ambiguous_with_no_coordinate():
    r = G.resolve("123 Main St, Huntersville, NC 28078")
    assert r.kind == "address" and r.ambiguous and r.geometry is None and r.bbox is None
    assert r.reason == "address geocoding unavailable"
    with pytest.raises(ValueError):
        G.contains(r, *HUNTERSVILLE_DT)
    rr = G.resolve("within 1 km of 123 Main St")
    assert rr.kind == "radius" and rr.geometry is None and rr.ambiguous


def test_geocoder_bad_or_conflicting_answers_are_not_guessed():
    assert G.resolve("1 Elm St", geocoder=lambda a: {"lon": float("nan"), "lat": 35.2}).geometry is None
    assert G.resolve("1 Elm St", geocoder=lambda a: {"lon": -122.4, "lat": 37.8}).geometry is None  # San Francisco
    assert G.resolve("1 Elm St", geocoder=lambda a: None).geometry is None
    two = G.resolve("1 Elm St", geocoder=lambda a: [{"lon": -80.84, "lat": 35.22, "score": 0.6},
                                                     {"lon": -80.72, "lat": 35.11, "score": 0.9}])
    assert two.ambiguous and two.geometry["coordinates"] == [-80.72, 35.11] and len(two.alternatives) == 1


def test_counted_nouns_are_not_addresses():
    r = G.resolve("3 restaurants on South Blvd in Matthews")
    assert r.kind == "municipality" and r.name == "Matthews"


# ---------------------------------------------------------------- custom polygon / bbox
def test_custom_polygon_inclusion_exclusion_and_boundary():
    w, s, e, n = -80.86, 35.21, -80.83, 35.24
    r = G.resolve({"bbox": [w, s, e, n], "label": "My box"})
    assert r.kind == "polygon" and r.name == "My box"
    assert G.contains(r, *UPTOWN)
    assert not G.contains(r, *QUEENS_UNIV)
    # documented: polygons are CLOSED sets, a point exactly on an edge or vertex is inside
    assert G.contains(r, w, 35.225) and G.contains(r, w, s)
    assert not G.contains(r, w - 1e-7, 35.225)
    tri = {"type": "Polygon", "coordinates": [[[-80.9, 35.1], [-80.7, 35.1], [-80.8, 35.3], [-80.9, 35.1]]]}
    rt = G.resolve("pools", {"area": {"geojson": tri}})  # no place in the text → context area
    assert rt.kind == "polygon" and G.contains(rt, -80.8, 35.15) and not G.contains(rt, -80.89, 35.28)


def test_invalid_polygons_are_rejected_or_repaired_with_a_note():
    assert G.resolve({"bbox": [-80.8, 35.2, -80.9, 35.3]}).geometry is None  # west > east
    assert G.resolve({"bbox": [-80.8, 35.2, -80.7, 95.0]}).geometry is None  # lat 95
    assert G.resolve({"type": "Point", "coordinates": [-80.8, 35.2]}).geometry is None
    bow = {"type": "Polygon", "coordinates": [[[-80.9, 35.1], [-80.8, 35.2], [-80.8, 35.1], [-80.9, 35.2], [-80.9, 35.1]]]}
    r = G.resolve(bow)
    assert r.kind == "polygon" and "repaired" in r.reason


# ---------------------------------------------------------------- CRS + coordinate validation
@pytest.mark.parametrize("pt", [UPTOWN, GASTONIA, LN_WATER, (-81.06, 35.0), (-80.55, 35.52), (-84.0, 30.0)])
def test_crs_roundtrip_error_below_1cm(pt):
    assert G.roundtrip_error_m(*pt) < 0.01
    x, y = G.lonlat_to_utm(*pt)
    lon, lat = G.utm_to_lonlat(x, y)
    assert abs(lon - pt[0]) < 1e-9 and abs(lat - pt[1]) < 1e-9


@pytest.mark.parametrize("lon,lat", [(float("nan"), 35.2), (-80.8, float("nan")), (-80.8, 95.0), (200.0, 35.2),
                                     (float("inf"), 35.0), ("abc", 35.0), (None, 35.0)])
def test_invalid_coordinates_rejected(lon, lat):
    with pytest.raises(G.InvalidCoordinate):
        G.validate_coordinate(lon, lat)
    with pytest.raises(G.InvalidCoordinate):
        G.contains(G.resolve("Mecklenburg County"), lon, lat)


def test_coordinates_outside_coverage_and_swapped():
    with pytest.raises(G.InvalidCoordinate, match="outside coverage"):
        G.validate_coordinate(0.0, 0.0)
    with pytest.raises(G.InvalidCoordinate, match="swapped"):
        G.validate_coordinate(35.2271, -80.8431)
    assert G.validate_coordinate(*UPTOWN) == UPTOWN
    assert G.validate_coordinate(*GASTONIA) == GASTONIA  # near the county: valid (and simply outside it)


def test_distance_m():
    assert G.distance_m(UPTOWN, offset(UPTOWN, 3000, 4000)) == pytest.approx(5000, rel=1e-6)
    assert G.distance_m(G.resolve("Charlotte"), UPTOWN) == 0.0
    # uptown ↔ Gastonia ≈ 31.5 km great-circle; UTM 17N scale error here is < 0.1 %
    assert G.distance_m(UPTOWN, GASTONIA) == pytest.approx(31_500, rel=0.01)


# ---------------------------------------------------------------- ZIP, outside places, nesting, ambiguity
def test_zip():
    r = G.resolve("homes in 28202")
    assert r.kind == "zip" and r.name == "28202" and r.approximate and "ZCTA" in r.reason
    assert G.contains(r, *ROMARE_BEARDEN) and not G.contains(r, *UPTOWN)  # the Square is unique-ZIP ZCTA 28280
    assert G.resolve("28280").kind == "zip"
    for z in ("zip 90210", "28201"):  # outside the county / PO-box-only ZIP (no ZCTA)
        u = G.resolve(z)
        assert u.kind == "zip" and u.geometry is None


def test_places_outside_the_county_are_not_guessed():
    for q in ("restaurants in Gastonia", "Gastonia", "pools in Gaston County", "Davidson County"):
        r = G.resolve(q)
        assert r.geometry is None and "outside Mecklenburg County" in r.reason, q


def test_nested_places_pick_the_most_specific_and_disjoint_places_are_ambiguous():
    r = G.resolve("pools in Myers Park, Charlotte")
    assert r.name == "Myers Park" and not r.ambiguous and r.detail["within"] == ["Charlotte"]
    r = G.resolve("parks in Huntersville, Mecklenburg County")
    assert r.name == "Huntersville" and not r.ambiguous
    r = G.resolve("restaurants in Matthews and Mint Hill")
    assert r.name == "Matthews" and r.ambiguous and any(a["name"] == "Mint Hill" for a in r.alternatives)


def test_common_word_neighbourhood_names_need_a_locative():
    assert G.resolve("cherry trees").geometry is None
    assert G.resolve("pools in Cherry").name == "Cherry"
    r = G.resolve("in Ravenwood")  # two OSM polygons share the name
    assert r.ambiguous


def test_no_place_is_unresolved_not_a_default():
    r = G.resolve("find swimming pools")
    assert r.geometry is None and r.kind is None and "no place" in r.reason
    r = G.resolve("find swimming pools", {"area": {"bbox": [-80.86, 35.21, -80.83, 35.24]}})
    assert r.kind == "polygon" and "context.area" in r.reason


def test_neighbourhood_upgrades_and_same_name_conflicts():
    for n in ("Uptown", "University City"):  # OSM polygon of the same OSM object as the bbox → exact, not ambiguous
        r = G.resolve(n)
        assert r.kind == "neighbourhood" and not r.approximate and not r.ambiguous
    r = G.resolve("South End")  # OSM polygon is a different object from Nominatim's point → ambiguous
    assert r.ambiguous and any(a["layer"] == "neighbourhoods_bbox" for a in r.alternatives)


# ---------------------------------------------------------------- feature filtering
def test_filter_features_points_and_polygons():
    def pt(name, lonlat):
        return {"type": "Feature", "properties": {"name": name}, "geometry": {"type": "Point", "coordinates": list(lonlat)}}

    fc = {"type": "FeatureCollection", "features": [
        pt("uptown", UPTOWN), pt("gastonia", GASTONIA), pt("matthews", MATTHEWS_DT),
        {"type": "Feature", "properties": {"name": "bad"}, "geometry": {"type": "Point", "coordinates": [float("nan"), 35.2]}},
        {"type": "Feature", "properties": {"name": "nogeom"}, "geometry": None},
        {"type": "Feature", "properties": {"name": "uptown-block"}, "geometry": {"type": "Polygon", "coordinates": [[
            [-80.844, 35.226], [-80.842, 35.226], [-80.842, 35.228], [-80.844, 35.228], [-80.844, 35.226]]]}},
    ]}
    names = lambda fs: [f["properties"]["name"] for f in fs]  # noqa: E731
    assert names(G.filter_features(G.resolve("Mecklenburg County"), fc)) == ["uptown", "matthews", "uptown-block"]
    assert names(G.filter_features(G.resolve("Charlotte"), fc)) == ["uptown", "uptown-block"]
    assert names(G.filter_features(G.resolve("Matthews"), fc["features"])) == ["matthews"]
    assert names(G.filter_features(G.resolve({"point": list(UPTOWN), "radius_m": 500}), fc, "intersects")) == \
        ["uptown", "uptown-block"]
    mask = G.points_mask(G.resolve("Charlotte"), [UPTOWN[0], GASTONIA[0], float("nan")], [UPTOWN[1], GASTONIA[1], 35.0])
    assert mask.tolist() == [True, False, False]


# ---------------------------------------------------------------- determinism + data provenance
def test_deterministic():
    for q in ("Lake Norman", "within 5 miles of Lake Norman", "Matthews", "restaurants in Mecklenburg County"):
        assert json.dumps(G.resolve(q).to_dict(), sort_keys=True) == json.dumps(G.resolve(q).to_dict(), sort_keys=True)


def test_every_layer_feature_has_provenance():
    gz = G.gazetteer()
    for e in gz.entries:
        if e.geom is None:
            continue
        for k in ("name", "kind", "source", "source_date", "licence"):
            assert e.props.get(k), (e.layer, e.name, k)
        assert e.geom.is_valid, (e.layer, e.name)
        b = e.geom.bounds
        assert -82 < b[0] < b[2] < -80 and 34.5 < b[1] < b[3] < 36, (e.layer, e.name)  # EPSG:4326, near the county
    meta = gz.meta
    assert all(s.get("licence") for s in meta["sources"])
    assert {"county", "municipalities", "zcta", "water_bodies", "neighbourhoods_bbox"} <= set(meta["layers"])
