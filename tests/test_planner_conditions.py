"""Planner correctness (platform audit 2026-11-08): a condition in the objective becomes a plan step, or is reported
as not applied. It is never dropped silently (the audit found 'within 500 m of a light rail station', 'steep slopes'
and 'compare 2020 and 2022' answered as if they weren't there)."""
from groundtruth import investigate as I


def plan(text):
    p, info = I.compile_plan(text, {})
    assert p is not None, info
    return p


def wheres(p):
    return [s.args["where"] for s in p.steps if s.op == "filter"]


def test_distance_to_a_mapped_layer_is_a_step():
    p = plan("Show buildings in University City within 500 m of a light rail station")
    assert {"within_m_of": {"layer": "osm_light_rail_stations", "m": 500.0}} in wheres(p)
    assert not p.unapplied


def test_units_and_near_default_is_an_assumption():
    p = plan("pools in Ballantyne within 1 km of I-485")
    assert {"within_m_of": {"layer": "osm_i485", "m": 1000.0}} in wheres(p)
    p = plan("buildings in University City near a light rail station")
    w = wheres(p)[0]["within_m_of"]
    assert w["layer"] == "osm_light_rail_stations" and any("'Near' read as within" in x for x in p.limitations)


def test_steep_slope_and_explicit_degrees():
    p = plan("Which pools in Ballantyne sit on steep slopes?")
    assert any("slope_deg" in w for w in wheres(p)) and not p.unapplied
    assert any("15 % grade" in x for x in p.limitations)  # the threshold is stated, not hidden
    p = plan("pools in Ballantyne on slopes steeper than 12 degrees")
    assert {"slope_deg": {"min": 12.0}} in wheres(p)


def test_floodway():
    p = plan("pools in Lake Norman in the floodway")
    assert {"in_flood_zone": {"kind": "floodway"}} in wheres(p)


def test_compare_years_phrasings():
    for t in ("Compare pools in Ballantyne between 2020 and 2022", "pools in Ballantyne: 2020 vs 2022", "what's new in Ballantyne pools"):
        assert any(s.op == "compare_years" for s in plan(t).steps), t


def test_homes_with_pools_outside_lake_norman_fall_back_and_say_so():
    p = plan("Which homes in Myers Park have pools under heavy tree canopy?")
    ops = [s.op for s in p.steps]
    assert "find_derived" not in ops and "find_concept" in ops
    assert any("canopy_over_pct" in w for w in wheres(p))
    assert any("a pool is not proof of which home" in x for x in p.limitations)


def test_unknown_condition_is_reported_not_dropped():
    p = plan("pools in Ballantyne that are vacant")
    assert p.unapplied and "vacant" in p.unapplied[0]
    assert p.limitations[0].startswith("Not applied:")
    p = plan("buildings in University City within 300 m of a grocery store")  # no layer for that yet
    assert p.unapplied and "within 300 m" in p.unapplied[0]


def test_flagship_unchanged_and_scope_words_are_not_conditions():
    p = plan("Find residential properties near Lake Norman with swimming pools that intersect mapped 100-year flood "
             "zones. Rank the strongest candidates and tell me where data is missing.")
    assert [s.op for s in p.steps] == ["resolve_area", "check_coverage", "find_derived", "filter", "explain", "rank", "conclude"]
    assert not p.unapplied  # 'near Lake Norman' is the scope, not a distance condition
    assert not plan("Find pools within 30 m of the shore of Lake Norman").unapplied
