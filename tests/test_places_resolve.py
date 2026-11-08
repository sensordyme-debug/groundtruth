"""Cross-provider place resolution (engine/groundtruth/places/resolve.py)."""
from __future__ import annotations

from groundtruth.places.base import PlaceRecord
from groundtruth.places.resolve import (address_similarity, name_similarity, normalize_address, normalize_phone,
                                        resolve)


def rec(provider, pid, name, lat, lon, address=None, phone=None, categories=None):
    return PlaceRecord(id=f"{provider}:{pid}", provider=provider, provider_place_id=pid, name=name, lat=lat, lon=lon,
                       address=address, phone=phone, categories=categories or [], retrieved_at="2026-11-08T00:00:00Z")


# ~0.0001 deg lat = 11 m; 0.0027 deg lat = 300 m
def test_same_place_across_providers_is_one_corroborated_cluster():
    recs = [rec("foursquare", "f1", "Amélie's French Bakery & Café", 35.24680, -80.80670, "2424 N Davidson St",
                "(704) 555-0101", ["cafe"]),
            rec("yelp", "y1", "Amelie's French Bakery", 35.24685, -80.80665, "2424 North Davidson Street",
                "+17045550101", ["cafe"]),
            rec("google", "g1", "Amélie's French Bakery & Café", 35.24683, -80.80668, "2424 N Davidson St",
                None, ["cafe"])]
    cl = resolve(recs)
    assert len(cl) == 1
    c = cl[0]
    assert c["match_status"] == "corroborated" and c["corroboration"] == 3
    assert c["providers"] == ["foursquare", "yelp", "google"] and c["representative_id"] == "foursquare:f1"
    assert {m.id for m in c["members"]} == {"foursquare:f1", "yelp:y1", "google:g1"}  # provenance kept
    assert all(m.match_status == "corroborated" for m in c["members"])
    assert c["evidence"] and all(e["rule"] for e in c["evidence"]) and all(e["dist_m"] < 15 for e in c["evidence"])


def test_similar_names_300m_apart_are_not_merged():
    recs = [rec("foursquare", "f1", "Brixx Wood Fired Pizza", 35.2200, -80.8500, "1801 South Blvd"),
            rec("yelp", "y1", "Brixx Wood Fired Pizza + Craft Bar", 35.2227, -80.8500, "225 E 6th St")]
    cl = resolve(recs)
    assert len(cl) == 2 and all(c["match_status"] == "single" and c["corroboration"] == 1 for c in cl)


def test_same_name_same_spot_but_never_same_provider():
    recs = [rec("yelp", "a", "Starbucks", 35.2200, -80.8500), rec("yelp", "b", "Starbucks", 35.22001, -80.85001)]
    assert len(resolve(recs)) == 2


def test_name_alone_never_merges_far_records_even_with_same_address_text_missing():
    recs = [rec("foursquare", "f1", "Starbucks", 35.2200, -80.8500), rec("yelp", "y1", "Starbucks", 35.2300, -80.8500)]
    assert len(resolve(recs)) == 2


def test_address_plus_name_merges_when_coordinates_are_offset():
    # rooftop vs parcel-centroid geocodes ~150 m apart, same address, similar name
    recs = [rec("foursquare", "f1", "Freedom Park Tennis Center", 35.1950, -80.8440, "1900 East Blvd"),
            rec("yelp", "y1", "Freedom Park Tennis", 35.19635, -80.8440, "1900 East Boulevard")]
    cl = resolve(recs)
    assert len(cl) == 1 and cl[0]["evidence"][0]["rule"] == "address+name"
    assert cl[0]["match_status"] == "conflict"  # merged members > conflict_dist_m (120 m) apart are flagged
    assert "apart" in cl[0]["conflicts"][0]


def test_incompatible_categories_block_merge():
    recs = [rec("foursquare", "f1", "Lakeside Marina", 35.4500, -80.9000, categories=["marina"]),
            rec("yelp", "y1", "Lakeside Marina", 35.45001, -80.90001, categories=["restaurant"])]
    assert len(resolve(recs)) == 2


def test_different_phones_flag_conflict():
    recs = [rec("foursquare", "f1", "Common Market", 35.2100, -80.8600, phone="(704) 555-0111"),
            rec("yelp", "y1", "Common Market", 35.21002, -80.86002, phone="+17045550199")]
    cl = resolve(recs)
    assert len(cl) == 1 and cl[0]["match_status"] == "conflict" and "phone" in cl[0]["conflicts"][0]


def test_greedy_keeps_one_record_per_provider_per_cluster():
    recs = [rec("foursquare", "f1", "Coffee Lab", 35.2000, -80.8000),
            rec("yelp", "y1", "Coffee Lab", 35.20001, -80.80001),
            rec("yelp", "y2", "Coffee Lab", 35.20003, -80.80003)]
    cl = resolve(recs)
    assert sorted(len(c["members"]) for c in cl) == [1, 2]
    pair = next(c for c in cl if len(c["members"]) == 2)
    assert {m.id for m in pair["members"]} == {"foursquare:f1", "yelp:y1"}  # the closest pair wins


def test_deterministic_ids_and_order():
    recs = [rec("foursquare", "f1", "A Cafe", 35.20, -80.80), rec("yelp", "y1", "B Bar", 35.21, -80.81)]
    a, b = resolve(recs), resolve(list(reversed(recs)))
    assert [c["cluster_id"] for c in a] == [c["cluster_id"] for c in b]


def test_normalizers():
    assert name_similarity("Amélie's French Bakery & Café", "Amelie's French Bakery") >= 0.82
    assert name_similarity("Brixx Pizza", "Bojangles") < 0.6
    assert normalize_address("2424 North Davidson Street, Charlotte") == "2424 n davidson st"
    assert normalize_address("100 N Tryon St Suite 200") == "100 n tryon st"
    assert address_similarity("2424 N Davidson St", "2424 North Davidson Street") == 1.0
    assert address_similarity("2424 N Davidson St", "2426 N Davidson St") == 0.0
    assert normalize_phone("+1 (704) 555-0101") == normalize_phone("704.555.0101") == "7045550101"
    assert normalize_phone("123") == ""
