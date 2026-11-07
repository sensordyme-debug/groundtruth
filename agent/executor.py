"""Execute bounded plans against mock envelopes or the future HTTP contract."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .planner import Step, validate_plan
from .policy import declined_reason

FIXTURES = Path(__file__).resolve().parents[1] / "mcp" / "mock_fixtures"
REQUIRED = {"tool", "version", "status", "result", "confidence", "method", "assumptions", "limits", "sources", "steps", "declined_reason"}


@dataclass
class ExecutedStep:
    ref: str
    tool: str
    arguments: dict[str, Any]
    envelope: dict[str, Any]
    ms: int


def validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not REQUIRED <= value.keys():
        raise ValueError(f"Invalid Groundtruth envelope; missing {sorted(REQUIRED - value.keys()) if isinstance(value, dict) else 'object'}")
    if value["status"] not in {"ok", "partial", "declined"}:
        raise ValueError("Invalid envelope status")
    return value


def call_tool(step: Step) -> dict[str, Any]:
    if any(declined_reason(value) for value in step.arguments.values() if isinstance(value, str)):
        with (FIXTURES / "declined.json").open(encoding="utf-8") as stream:
            envelope = json.load(stream)
        envelope["tool"] = step.tool
        return validate_envelope(envelope)
    if os.getenv("GROUNDTRUTH_MOCK", "0") == "1":
        with (FIXTURES / f"{step.tool}.json").open(encoding="utf-8") as stream:
            envelope = json.load(stream)
        envelope["limits"].append("Mock mode: illustrative result, not a live detection.")
        return validate_envelope(envelope)
    base = os.getenv("GROUNDTRUTH_API_URL")
    if not base:
        raise ValueError("GROUNDTRUTH_API_URL is required outside mock mode; live route awaits docs/CONTRACT.md")
    if not (Path(__file__).resolve().parents[1] / "docs" / "CONTRACT.md").exists():
        raise ValueError("Live agent dispatch requires docs/CONTRACT.md; endpoint routing is unverified")
    if not base.startswith(("http://", "https://")):
        raise ValueError("GROUNDTRUTH_API_URL must be an HTTP URL")
    req = Request(f"{base.rstrip('/')}/{step.tool}", data=json.dumps(step.arguments).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=float(os.getenv("GROUNDTRUTH_TIMEOUT_S", "60"))) as response:
        return validate_envelope(json.load(response))


def execute(steps: list[Step]) -> list[ExecutedStep]:
    validate_plan(steps)
    results = []
    for number, step in enumerate(steps, start=1):
        started = time.monotonic()
        envelope = call_tool(step)
        results.append(ExecutedStep(f"step_{number}", step.tool, step.arguments, envelope, round((time.monotonic() - started) * 1000)))
        if envelope["status"] == "declined":
            break
    return results
