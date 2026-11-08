"""Scenario engine (synthetic what-ifs). The connected bathtub is tested on small synthetic grids; the API and every
record must say the data is simulated."""
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from groundtruth import scenario as S


def grid():
    # column 0 is the lake (seed); a ridge at column 3 (elev 10) separates a low basin at columns 4-5 (elev 1)
    dtm = np.array([[0, 1, 2, 10, 1, 1],
                    [0, 1, 2, 10, 1, 1],
                    [0, 1, 3, 10, 1, 1]], dtype="float32")
    seed = np.zeros(dtm.shape, bool)
    seed[:, 0] = True
    return dtm, np.ones(dtm.shape, bool), seed


def test_connected_bathtub_only_wets_connected_low_ground():
    dtm, meas, seed = grid()
    w = S.connected_wet(dtm, meas, seed, 2.0)
    assert w[:, 1].all() and w[:2, 2].all() and not w[2, 2]  # 3 m cell stays dry at 2 m
    assert not w[:, 3:].any()  # the basin behind the ridge is low but NOT connected: dry
    assert not w[:, 0].any()  # the seed water itself is not counted as land


def test_lidar_gap_blocks_water_and_is_never_wet():
    dtm, meas, seed = grid()
    meas[:, 1] = False  # no ground return in column 1
    w = S.connected_wet(dtm, meas, seed, 5.0)
    assert not w.any()  # water cannot cross unmeasured ground


def test_levels_are_monotonic():
    dtm, meas, seed = grid()
    a = [S.connected_wet(dtm, meas, seed, lv).sum() for lv in (0.5, 1, 2, 3, 11)]
    assert a == sorted(a) and a[-1] == 15  # at 11 m the ridge is overtopped: every land cell connects


def test_definition_is_explicitly_hypothetical():
    d = next(x for x in S.conf()["scenarios"] if x["id"] == "ln_water_level")
    assert d["reference"]["value_m"] == 231.648 and d["reference"]["datum"] == "NAVD88"  # FEMA static BFE, as published
    assert any("Hypothetical" in a for a in d["assumptions"])
    assert any("unknown, never dry" in a for a in d["assumptions"])
    assert S.level_key(1.5) == "plus_1_5m"


def test_every_record_carries_provenance():
    d = next(x for x in S.conf()["scenarios"] if x["id"] == "ln_water_level")
    r = S.record(d, "x", "2026-11-08T00:00:00", "water_extent", level_m=232.6)
    for k in ("synthetic_id", "is_synthetic", "scenario_id", "description", "created_at", "assumptions", "real_sources",
              "visualization", "simulation_status"):
        assert k in r, k
    assert r["is_synthetic"] is True and r["simulation_status"].startswith("SIMULATED")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import scenarios_api
    sd = tmp_path / "ln_water_level"
    sd.mkdir()
    (sd / "meta.json").write_text(json.dumps({"scenario_id": "ln_water_level", "is_synthetic": True, "levels": []}))
    (sd / "extent_plus_1_0m.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    monkeypatch.setattr(S, "scenario_dir", lambda sid: tmp_path / sid)
    app = FastAPI()
    app.include_router(scenarios_api.router)
    return TestClient(app)


def test_api(client):
    ls = client.get("/api/v1/scenarios").json()
    assert ls["is_synthetic"] is True and ls["scenarios"][0]["built"] is True
    assert client.get("/api/v1/scenarios/ln_water_level").json()["is_synthetic"] is True
    assert client.get("/api/v1/scenarios/ln_water_level/extent?offset_m=1").status_code == 200
    assert client.get("/api/v1/scenarios/ln_water_level/extent?offset_m=7").status_code == 422  # not a defined level
    assert client.get("/api/v1/scenarios/nope").status_code == 404
