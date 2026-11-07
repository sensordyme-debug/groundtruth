from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from mcp_server import server

@pytest.fixture(autouse=True)
def mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUNDTRUTH_MOCK", "1")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_text", {"query": "pools"}),
        ("pick_candidates", {"query": "pools"}),
        ("find_similar", {"chip_ids": ["pool-1"]}),
        ("outline", {"chip_ids": ["chip-1"], "concept": "pool"}),
        ("refine", {"session_id": "session-1"}),
        ("add_layer", {"name": "osm_roads"}),
        ("filter", {"features": {"type": "FeatureCollection", "features": []}, "where": {}}),
        ("measure", {"features": {"type": "FeatureCollection", "features": []}, "what": "count"}),
        ("export", {"features": {"type": "FeatureCollection", "features": []}, "format": "csv"}),
    ],
)
@pytest.mark.asyncio
async def test_each_tool_returns_its_envelope(tool: str, arguments: dict[str, object]) -> None:
    response = await server._call(tool, arguments)
    assert response["tool"] == tool
    assert response["version"] == "0.1.0"
    assert response["status"] in {"ok", "partial", "declined"}
    assert "limits" in response and "sources" in response


def test_bad_inputs_are_rejected() -> None:
    with pytest.raises(ValidationError):
        server.SearchTextInput(query="", k=0)
    with pytest.raises(ValueError, match="bbox"):
        server._valid_bbox([1, 2, 3])
    with pytest.raises(ValidationError):
        server.ExportInput(features={}, format="shp")


def test_published_contract_inputs() -> None:
    assert server.QueryInput(query="pools").model_dump() == {"query": "pools", "n": 6, "pool": 200}
    assert server.FindSimilarInput(chip_ids=["one"]).model_dump() == {"chip_ids": ["one"], "exclude_ids": [], "k": 50}
    with pytest.raises(ValidationError):
        server.FindSimilarInput(positive_ids=["old-name"])


@pytest.mark.asyncio
async def test_people_data_decline_is_passed_through() -> None:
    response = await server._call("search_text", {"query": "who owns these pools?"})
    assert response["status"] == "declined"
    assert response["declined_reason"] == "people_data"
    assert "people" in response["limits"][0].lower()


def test_large_geojson_truncation_preserves_metrics() -> None:
    envelope = {"result": {"geojson": {"type": "FeatureCollection", "features": [{"id": i} for i in range(250)]}, "metrics": {"count": 250, "area_acres": 12.5}}}
    result = server._trim_large_geojson(envelope)
    assert len(result["result"]["geojson"]["features"]) == 50
    assert result["result"]["metrics"] == {"count": 250, "area_acres": 12.5}
    assert any("truncated" in item for item in result["limits"])


@respx.mock
@pytest.mark.asyncio
async def test_api_retries_5xx_and_returns_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROUNDTRUTH_MOCK")
    monkeypatch.setenv("GROUNDTRUTH_API_URL", "https://api.example.test")
    route = respx.post("https://api.example.test/search_text").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"tool": "search_text", "status": "ok"})]
    )
    monkeypatch.setattr(server.asyncio, "sleep", lambda _: _resolved())
    result = await server._request_api("search_text", {"query": "pools"})
    assert result["status"] == "ok"
    assert route.call_count == 2


async def _resolved() -> None:
    return None


@respx.mock
@pytest.mark.asyncio
async def test_api_timeout_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROUNDTRUTH_MOCK")
    monkeypatch.setenv("GROUNDTRUTH_API_URL", "https://api.example.test")
    respx.post("https://api.example.test/search_text").mock(side_effect=httpx.TimeoutException("late"))
    result = await server._call("search_text", {"query": "pools"})
    assert result["tool"] == "search_text"
    assert any("API unavailable" in item for item in result["limits"])


def test_every_fixture_is_valid_envelope() -> None:
    fixture_dir = Path(server.FIXTURES)
    for fixture_path in fixture_dir.glob("*.json"):
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert {"tool", "version", "status", "result", "limits", "sources", "declined_reason"} <= payload.keys()
