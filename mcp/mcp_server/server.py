"""MCP tools for Groundtruth's versioned geospatial search envelope."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field

LOGGER = logging.getLogger("groundtruth.mcp")
SERVER = FastMCP("Groundtruth", instructions="Search and analyze aerial imagery through Groundtruth. Use pick_candidates for ambiguous concepts before find_similar. Requests involving identifying people or property owners are declined.")
FIXTURES = Path(__file__).resolve().parents[1] / "mock_fixtures"
MAX_INLINE_FEATURES = 50
FEATURE_WARNING_THRESHOLD = 200
PEOPLE_PATTERN = re.compile(r"\b(who owns?|owner(?:ship)?|resident(?:s)?|people|person|homeowner|identify .{0,20}(?:person|owner))\b", re.I)


class ToolInput(BaseModel):
    """Base for strict, typed tool inputs."""

    model_config = ConfigDict(extra="forbid")


class SearchTextInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=50, ge=1, le=500)
    bbox: list[float] | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)


class QueryInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    n: int = Field(default=6, ge=1, le=50)
    pool: int = Field(default=200, ge=1, le=10000)


class FindSimilarInput(ToolInput):
    chip_ids: list[str] = Field(min_length=1, max_length=100)
    exclude_ids: list[str] = Field(default_factory=list, max_length=100)
    k: int = Field(default=50, ge=1, le=1000)


class IdsInput(ToolInput):
    chip_ids: list[str] = Field(default_factory=list, max_length=500)
    bbox: list[float] | None = None
    concept: str = Field(min_length=1)


class RefineInput(ToolInput):
    session_id: str = Field(min_length=1, max_length=200)
    accept_ids: list[str] = Field(default_factory=list, max_length=500)
    reject_ids: list[str] = Field(default_factory=list, max_length=500)


class AddLayerInput(ToolInput):
    name: str = Field(min_length=1, max_length=120)
    bbox: list[float] | None = None


class FilterInput(ToolInput):
    features: dict[str, Any]
    where: dict[str, Any]


class MeasureInput(ToolInput):
    features: dict[str, Any]
    what: str = Field(min_length=1)


class ExportInput(ToolInput):
    features: dict[str, Any]
    format: str = Field(pattern="^(csv|geojson|kml)$")


def _valid_bbox(bbox: list[float] | None) -> list[float] | None:
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError("bbox must contain [west, south, east, north]")
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox must be ordered [west, south, east, north] within EPSG:4326")
    return bbox


def _fixture(tool: str) -> dict[str, Any]:
    with (FIXTURES / f"{tool}.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def _declined(reason: str = "people_data") -> dict[str, Any]:
    payload = _fixture("declined")
    payload["declined_reason"] = reason
    return payload


def _has_people_request(values: Any) -> bool:
    if isinstance(values, str):
        return bool(PEOPLE_PATTERN.search(values))
    if isinstance(values, dict):
        return any(_has_people_request(key) or _has_people_request(value) for key, value in values.items())
    if isinstance(values, (list, tuple)):
        return any(_has_people_request(value) for value in values)
    return False


def _summary(envelope: dict[str, Any]) -> str:
    result = envelope.get("result") or {}
    metrics = result.get("metrics") or {}
    parts = [f"status={envelope.get('status', 'unknown')}"]
    if "count" in metrics:
        parts.append(f"count={metrics['count']}")
    for name in ("area_acres", "area_acres_total"):
        if name in metrics:
            parts.append(f"{name}={metrics[name]}")
    if "confidence" in envelope:
        parts.append(f"confidence={envelope['confidence']}")
    limits = envelope.get("limits") or []
    if limits:
        parts.append(f"limits={'; '.join(map(str, limits))}")
    if envelope.get("declined_reason"):
        parts.append(f"declined_reason={envelope['declined_reason']}")
    return "; ".join(parts)


def _mcp_result(envelope: dict[str, Any]) -> CallToolResult:
    """Expose the unchanged envelope as structured data and a compact text summary."""
    return CallToolResult(
        content=[TextContent(type="text", text=_summary(envelope))],
        structuredContent=envelope,
    )


def _trim_large_geojson(envelope: dict[str, Any]) -> dict[str, Any]:
    geojson = (envelope.get("result") or {}).get("geojson")
    if not isinstance(geojson, dict):
        return envelope
    features = geojson.get("features")
    if not isinstance(features, list) or len(features) <= FEATURE_WARNING_THRESHOLD:
        return envelope
    original_count = len(features)
    geojson["features"] = features[:MAX_INLINE_FEATURES]
    limits = envelope.setdefault("limits", [])
    note = f"GeoJSON truncated from {original_count} features to the first {MAX_INLINE_FEATURES}; metrics and count are preserved."
    if note not in limits:
        limits.append(note)
    return envelope


async def _request_api(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("GROUNDTRUTH_API_URL")
    if not base_url:
        raise RuntimeError("GROUNDTRUTH_API_URL is not configured; docs/CONTRACT.md does not specify a base URL")
    timeout = float(os.getenv("GROUNDTRUTH_TIMEOUT_S", "60"))
    async with httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=timeout) as client:
        for attempt in range(3):
            response = await client.post(tool, json=arguments)
            if response.status_code < 500 or attempt == 2:
                response.raise_for_status()
                return response.json()
            await asyncio.sleep(0.1 * (2**attempt))
    raise RuntimeError("API retry loop exhausted")


async def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if _has_people_request(arguments):
        envelope = _declined()
    elif os.getenv("GROUNDTRUTH_MOCK", "0") == "1":
        envelope = _fixture(tool)
    else:
        try:
            envelope = await _request_api(tool, arguments)
        except (httpx.HTTPError, TimeoutError, RuntimeError, ValueError) as exc:
            LOGGER.warning("Groundtruth API unavailable for %s (%s); serving mock fixture", tool, exc)
            envelope = _fixture(tool)
            envelope.setdefault("limits", []).append("API unavailable; mock fixture returned.")
    return _trim_large_geojson(envelope)


def _check_bbox(bbox: list[float] | None) -> None:
    _valid_bbox(bbox)


@SERVER.tool()
async def search_text(query: str, k: int = 50, bbox: list[float] | None = None, year: int | None = None) -> CallToolResult:
    """Rank aerial image chips matching a text query. Use for direct text search; use pick_candidates first when the concept is visually ambiguous."""
    args = SearchTextInput(query=query, k=k, bbox=bbox, year=year)
    _check_bbox(args.bbox)
    return _mcp_result(await _call("search_text", args.model_dump()))


@SERVER.tool()
async def pick_candidates(query: str, n: int = 6, pool: int = 200) -> CallToolResult:
    """Return six visually different interpretations of an ambiguous concept (for example pools). Ask the user to choose before calling find_similar."""
    args = QueryInput(query=query, n=n, pool=pool)
    return _mcp_result(await _call("pick_candidates", args.model_dump()))


@SERVER.tool()
async def find_similar(chip_ids: list[str], exclude_ids: list[str] | None = None, k: int = 50) -> CallToolResult:
    """Find chips like accepted examples, excluding rejected examples. Requires at least one positive candidate ID."""
    args = FindSimilarInput(chip_ids=chip_ids, exclude_ids=exclude_ids or [], k=k)
    return _mcp_result(await _call("find_similar", args.model_dump()))


@SERVER.tool()
async def outline(concept: str, chip_ids: list[str] | None = None, bbox: list[float] | None = None) -> CallToolResult:
    """Generate SAM 2 polygons for objects in chips or selected features. Provide chip_ids or feature_ids."""
    args = IdsInput(chip_ids=chip_ids or [], bbox=bbox, concept=concept)
    if not args.chip_ids and not args.bbox:
        raise ValueError("provide chip_ids or bbox")
    _check_bbox(args.bbox)
    return _mcp_result(await _call("outline", args.model_dump()))


@SERVER.tool()
async def refine(session_id: str, accept_ids: list[str] | None = None, reject_ids: list[str] | None = None) -> CallToolResult:
    """Re-rank search results using a session's accepted and rejected examples."""
    args = RefineInput(session_id=session_id, accept_ids=accept_ids or [], reject_ids=reject_ids or [])
    return _mcp_result(await _call("refine", args.model_dump()))


@SERVER.tool()
async def add_layer(name: str, bbox: list[float] | None = None) -> CallToolResult:
    """Load a context layer such as osm_roads, fema_flood, or light_rail for map analysis."""
    args = AddLayerInput(name=name, bbox=bbox)
    _check_bbox(args.bbox)
    return _mcp_result(await _call("add_layer", args.model_dump()))


@SERVER.tool(name="filter")
async def filter_results(features: dict[str, Any], where: dict[str, Any]) -> CallToolResult:
    """Filter a result collection by distance, area, or containment."""
    args = FilterInput(features=features, where=where)
    return _mcp_result(await _call("filter", args.model_dump()))


@SERVER.tool()
async def measure(features: dict[str, Any], what: str) -> CallToolResult:
    """Calculate requested counts, areas, or distances for a result collection."""
    args = MeasureInput(features=features, what=what)
    return _mcp_result(await _call("measure", args.model_dump()))


@SERVER.tool()
async def export(features: dict[str, Any], format: str) -> CallToolResult:
    """Export a result collection summary as CSV, GeoJSON, or PDF."""
    args = ExportInput(features=features, format=format)
    return _mcp_result(await _call("export", args.model_dump()))


@SERVER.resource("groundtruth://about")
def about() -> str:
    """Coverage, available concepts and layers, and data policy."""
    return ("Groundtruth searches NAIP aerial imagery over Mecklenburg County, North Carolina (playbook target: 2022 imagery). "
            "Concepts include pools, ponds, parking lots, construction sites, warehouses, and solar installations. "
            "Context layers may include OSM roads, FEMA flood zones, light rail, water, and UNC Charlotte. "
            "Policy: do not identify people, residents, homeowners, or property owners; such requests are declined.")


def main() -> None:
    logging.basicConfig(level=os.getenv("GROUNDTRUTH_LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s %(message)s")
    if os.getenv("GROUNDTRUTH_MOCK", "0") == "1":
        LOGGER.warning("GROUNDTRUTH_MOCK=1: serving local fixtures")
    SERVER.run()


if __name__ == "__main__":
    main()
