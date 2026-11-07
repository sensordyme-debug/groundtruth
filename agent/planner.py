"""Bounded rules planner and optional OpenAI-compatible plan adapter."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .providers.openai import plan_with_openai

CONFIG = Path(__file__).parent / "config" / "mecklenburg.yaml"
MAX_STEPS = 8
TOOL_NAMES = {"search_text", "pick_candidates", "find_similar", "outline", "refine", "add_layer", "filter", "measure", "export"}
REQUIRED_ARGS = {"search_text": {"query"}, "pick_candidates": {"query"}, "find_similar": {"chip_ids"}, "outline": {"concept"}, "refine": {"session_id"}, "add_layer": {"name"}, "filter": {"features", "where"}, "measure": {"features", "what"}, "export": {"features", "format"}}
ALLOWED_ARGS = {"search_text": {"query", "k", "bbox", "year"}, "pick_candidates": {"query", "n", "pool"}, "find_similar": {"chip_ids", "exclude_ids", "k"}, "outline": {"chip_ids", "bbox", "concept"}, "refine": {"session_id", "accept_ids", "reject_ids"}, "add_layer": {"name", "bbox"}, "filter": {"features", "where"}, "measure": {"features", "what"}, "export": {"features", "format"}}


@dataclass(frozen=True)
class Step:
    tool: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if self.tool not in TOOL_NAMES:
            raise ValueError(f"Unknown tool: {self.tool}")
        if not isinstance(self.arguments, dict):
            raise ValueError("Step arguments must be an object")
        if not REQUIRED_ARGS[self.tool] <= self.arguments.keys() or not self.arguments.keys() <= ALLOWED_ARGS[self.tool]:
            raise ValueError(f"Invalid arguments for {self.tool}")
        if self.tool == "outline" and not (self.arguments.get("chip_ids") or self.arguments.get("bbox")):
            raise ValueError("outline requires chip_ids or bbox")
        if self.tool == "export" and self.arguments["format"] not in {"csv", "geojson", "kml"}:
            raise ValueError("Unsupported export format")


def load_config() -> dict[str, Any]:
    # JSON is a YAML subset; this keeps the demo config usable without another runtime dependency.
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def place_for(request: str, config: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    for name in sorted(config["places"], key=len, reverse=True):
        if name in request.lower():
            return name, config["places"][name]
    return None


def concept_for(request: str) -> str:
    match = re.search(r"(?:find|count|measure|how many acres of|how many)\s+(.+?)(?:\s+(?:within|near|in|around|and|as)\b|$)", request, re.I)
    return (match.group(1) if match else request).strip()


def rules_plan(request: str, config: dict[str, Any]) -> list[Step]:
    text = request.lower()
    place = place_for(request, config)
    bbox = place[1]["bbox"] if place else config["bbox"]
    query = concept_for(request)
    if text.startswith("export them"):
        return [Step("export", {"features": {"type": "FeatureCollection", "features": []}, "format": "csv"})]
    if "compare" in text or "since 20" in text or "new since" in text:
        raise ValueError("Compare-years tool is not present in docs/CONTRACT.md")
    if "pool" in query.lower() and not ("picked" in text or "selected" in text):
        steps = [Step("pick_candidates", {"query": query})]
        if "i-485" in text:
            steps.append(Step("add_layer", {"name": "osm_roads", "bbox": bbox}))
        return steps
    steps = [Step("search_text", {"query": query, "bbox": bbox, "k": 20})]
    if "i-485" in text:
        steps.append(Step("add_layer", {"name": "osm_roads", "bbox": bbox}))
    if "acres" in text or "how many" in text:
        steps.append(Step("measure", {"features": {"$step": 1}, "what": "area_acres"}))
    if "export" in text:
        steps.append(Step("export", {"features": {"$step": 1}, "format": "csv" if "csv" in text else "geojson"}))
    return steps


def validate_plan(steps: list[Step]) -> list[Step]:
    if not steps or len(steps) > MAX_STEPS:
        raise ValueError(f"Plan must contain 1–{MAX_STEPS} steps")
    return steps


def plan(request: str, config: dict[str, Any]) -> list[Step]:
    mode = os.getenv("AGENT_PLANNER", "auto")
    if mode not in {"rules", "llm", "auto"}:
        raise ValueError("AGENT_PLANNER must be rules, llm, or auto")
    if mode in {"llm", "auto"} and os.getenv("AGENT_LLM_API_URL"):
        try:
            return validate_plan(plan_with_openai(request, config, Step, TOOL_NAMES))
        except Exception:
            if mode == "llm":
                raise
    elif mode == "llm":
        raise ValueError("AGENT_LLM_API_URL is required for llm mode")
    return validate_plan(rules_plan(request, config))
