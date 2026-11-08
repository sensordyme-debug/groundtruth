"""Source dates come from the registry (conf/sources.json), never from a file's modification time; unknown stays
unknown."""
import re
import json

from groundtruth.providers import public as P


def test_osm_uses_data_timestamp_not_file_mtime():
    d = P.registry_date("osm_geofabrik_nc")
    assert d.startswith("2026-11-06") and "data timestamp" in d  # the pbf header timestamp, not the 2026-11-07 copy date


def test_overture_release_date_not_empty():
    for sid in ("overture_places", "overture_buildings"):
        d = P.registry_date(sid)
        assert d.startswith("2026-10-21") and "release 2026-10-21.0" in d


def test_unknown_source_is_unavailable_not_invented():
    assert P.registry_date("no_such_source") == "unavailable"


def test_registry_without_any_date_is_unavailable(tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "sources.json").write_text(json.dumps({"sources": [{"source_id": "x", "acquisition_date": "not recorded"}]}))
    fake = tmp_path / "providers" / "public.py"
    fake.parent.mkdir()
    fake.write_text("")
    monkeypatch.setattr(P, "__file__", str(fake))
    assert P.registry_date("x") == "unavailable"


def test_provider_sources_use_registry_dates():
    prov = P.PublicDataProvider.__new__(P.PublicDataProvider)  # no I/O: the two methods only read the registry
    assert "data timestamp" in prov.routing_source().date
    assert "release" in prov.overture_source("places").date and "release" in prov.overture_source("buildings").date


def test_fema_effective_dates_from_registry_not_fetch_date():
    from groundtruth import investigate as I
    s = I._fema_effective()
    assert s == "effective 2009-03-02 to 2018-11-16 (FEMA FIRM panels)"
    assert "2026" not in s  # never the fetch or file date


def test_each_result_carries_its_firm_panel_effective_date():
    """A Lake Norman point lies on one FIRM panel; its effective date is one of the published panel dates."""
    from shapely.geometry import Point
    from groundtruth import risk as R
    pan, eff = R.firm_panel(Point(-80.93, 35.47))
    assert pan and re.fullmatch(r"37[0-9]{8}[A-Z]", pan)  # NC FIRM panel numbering, e.g. 3710462300L
    assert eff in ("2009-03-02", "2014-02-19", "2015-09-02", "2018-11-16")
    assert R.firm_panel(Point(-70.0, 40.0)) == (None, None)  # off every panel: unknown, never guessed
