import pytest
from jsonschema import validate
import json
from pathlib import Path

from agent.cli import run
from agent.planner import Step, validate_plan

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "agent" / "map_actions.schema.json").read_text())


@pytest.fixture(autouse=True)
def mock(monkeypatch):
    monkeypatch.setenv("GROUNDTRUTH_MOCK", "1")
    monkeypatch.setenv("AGENT_PLANNER", "rules")


@pytest.mark.parametrize("query,expected", [
    ("find pools", "show_candidates"),
    ("find pools near I-485 in Ballantyne", "show_candidates"),
    ("how many acres of parking lots in Uptown", "show_metrics"),
    ("export them as CSV", "say"),
])
def test_demo_requests(query, expected):
    result = run(query)
    assert result["status"] == "mock"
    assert expected in {action["action"] for action in result["map_actions"]}
    validate(result["map_actions"], SCHEMA)
    assert len(result["steps"]) <= 8


def test_people_data_declined_before_tools():
    result = run("who owns these pools?")
    assert result["status"] == "declined"
    assert result["steps"] == []


def test_invalid_plan_rejected():
    with pytest.raises(ValueError):
        validate_plan([Step("search_text", {"query": "x"})] * 9)
    with pytest.raises(ValueError):
        Step("invented_tool", {})
    with pytest.raises(ValueError):
        Step("search_text", {"query": "pools", "unknown": "x"})
