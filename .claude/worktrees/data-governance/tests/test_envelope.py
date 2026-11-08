import pytest
from pydantic import ValidationError

from groundtruth.envelope import Envelope, StepTimer, declined


def test_minimal_ok_envelope_has_all_contract_keys():
    env = Envelope(tool="search_text", version="0.1.0", status="ok")
    d = env.model_dump()
    assert set(d) == {"tool", "version", "status", "result", "confidence", "method", "assumptions", "limits",
                      "sources", "steps", "declined_reason"}
    assert d["result"] == {"geojson": {"type": "FeatureCollection", "features": []}, "metrics": {}}


def test_declined_requires_reason():
    with pytest.raises(ValidationError):
        Envelope(tool="x", version="1", status="declined")
    env = declined("x", "1", "outside Mecklenburg County")
    assert env.declined_reason


def test_reason_only_when_declined():
    with pytest.raises(ValidationError):
        Envelope(tool="x", version="1", status="ok", declined_reason="nope")


def test_bad_status_and_confidence_rejected():
    with pytest.raises(ValidationError):
        Envelope(tool="x", version="1", status="maybe")
    with pytest.raises(ValidationError):
        Envelope(tool="x", version="1", status="ok", confidence=1.5)


def test_geojson_must_be_feature_collection():
    with pytest.raises(ValidationError):
        Envelope(tool="x", version="1", status="ok", result={"geojson": {"type": "Feature"}})


def test_step_timer_records_ms():
    t = StepTimer()
    with t.step("embed", "3 chips"):
        sum(range(1000))
    assert t.steps[0].phase == "embed" and t.steps[0].ms >= 0
