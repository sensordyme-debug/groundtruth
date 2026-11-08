from groundtruth.planner import parse


def test_required_requests_parse():
    p = parse("find swimming pools")
    assert p.concept == "swimming_pool" and p.place is None
    p = parse("pools within 1 km of I-485 in Ballantyne")
    assert (p.concept, p.place, p.layer, p.within_m) == ("swimming_pool", "Ballantyne", "osm_i485", 1000.0)
    p = parse("parking lots in Uptown")
    assert (p.concept, p.place) == ("parking_lot", "Uptown")
    p = parse("solar on warehouses")
    assert (p.concept, p.host_layer) == ("solar_installation", "osm_warehouses")
    p = parse("pools in flood zones in Matthews")
    assert (p.concept, p.place, p.layer, p.inside_layer) == ("swimming_pool", "Matthews", "fema_flood_zones", True)


def test_phase5_phrases_parse():
    p = parse("houses within 10 minutes of Ardrey Kell High School")
    assert (p.target_kind, p.target, p.minutes, p.mode, p.origin) == ("buildings", "house", 10.0, "drive",
                                                                       "ardrey kell high school")
    p = parse("pools within a 5 minute walk of a park")
    assert (p.target_kind, p.concept, p.minutes, p.mode, p.origin) == ("concept", "swimming_pool", 5.0, "walk", "park")
    p = parse("restaurants near the light rail")
    assert (p.target_kind, p.target, p.layer, p.within_m) == ("place_category", "restaurant", "osm_light_rail", 500.0)
    p = parse("parking lots within 10 minutes of the airport")
    assert (p.target_kind, p.concept, p.minutes, p.origin) == ("concept", "parking_lot", 10.0, "the airport")


def test_height_and_open_vocabulary_parse():
    p = parse("buildings taller than 30 m in Uptown")
    assert (p.target_kind, p.target, p.min_height_m, p.place) == ("buildings", "building", 30.0, "Uptown")
    p = parse("tennis courts in Ballantyne")
    assert (p.target_kind, p.target, p.place) == ("open", "tennis courts", "Ballantyne")
    p = parse("schools in Matthews")
    assert (p.target_kind, p.target, p.place) == ("place_category", "school", "Matthews")


def test_people_requests_declined():
    from groundtruth.tools import _people

    for q in ("who owns the houses with pools in Ballantyne", "crime rates in Uptown", "who lives at 123 Main St",
              "owner names of houses with solar"):
        assert _people(q), q
    for q in ("pools within 1 km of I-485 in Ballantyne", "solar on warehouses", "parking lots in Uptown"):
        assert not _people(q), q


def test_new_since_and_year():
    p = parse("new pools since 2020 in Ballantyne")
    assert (p.concept, p.place, p.new_since, p.year) == ("swimming_pool", "Ballantyne", 2020, None)
    p = parse("pools new since 2020 in NoDa")
    assert p.new_since == 2020 and p.place == "NoDa"


def test_phase6_phrases_parse():
    p = parse("how tall are the trees in Myers Park")
    assert (p.target_kind, p.place) == ("canopy", "Myers Park")
    p = parse("warehouses over 10 m tall near the airport")
    assert (p.target_kind, p.target, p.min_height_m, p.minutes, p.origin) == ("buildings", "warehouse", 10.0, 5.0,
                                                                              "the airport")
    p = parse("solar on warehouses")
    assert (p.target_kind, p.concept, p.host_layer) == ("concept", "solar_installation", "osm_warehouses")


def test_open_vocabulary_on_place():
    p = parse("boat docks on Lake Norman")
    assert (p.target_kind, p.target, p.place) == ("open", "boat docks", "Lake Norman")


def test_derived_chain_parse():
    p = parse("houses with pools")
    assert (p.target_kind, p.target, p.place) == ("derived", "house_with_pool", None)
    p = parse("houses with pools in Lake Norman")
    assert (p.target_kind, p.place) == ("derived", "Lake Norman")
    p = parse("houses with pools in Lake Norman with lake and dock access")
    assert (p.target_kind, p.place, sorted(p.require)) == ("derived", "Lake Norman", ["has_dock", "lakefront"])
    p = parse("houses with pools in Lake Norman with lake and dock access which have docks added since 2020")
    assert p.docks_new_since == 2020
    p = parse("houses with pools in Lake Norman with lake and dock access show the biggest ones")
    assert p.sort == "pool_area"
    p = parse("with lake and dock access")
    assert p.target_kind is None and "has_dock" in p.require
