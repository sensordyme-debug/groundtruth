"""The single response envelope every Groundtruth tool returns (see docs/CONTRACT.md)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, model_validator

Status = Literal["ok", "partial", "declined"]


class Source(BaseModel):
    name: str
    url: str
    date: str
    licence: str


class Step(BaseModel):
    phase: str
    detail: str = ""
    ms: float = 0.0


class Result(BaseModel):
    geojson: dict[str, Any] = Field(
        default_factory=lambda: {"type": "FeatureCollection", "features": []}
    )
    metrics: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel):
    tool: str
    version: str
    status: Status
    result: Result = Field(default_factory=Result)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    method: str = ""
    assumptions: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    declined_reason: str | None = None

    @model_validator(mode="after")
    def _declined_needs_reason(self) -> "Envelope":
        if self.status == "declined" and not self.declined_reason:
            raise ValueError("declined envelopes must carry declined_reason")
        if self.status != "declined" and self.declined_reason:
            raise ValueError("declined_reason is only allowed when status == 'declined'")
        gj = self.result.geojson
        if gj.get("type") != "FeatureCollection" or not isinstance(gj.get("features"), list):
            raise ValueError("result.geojson must be a GeoJSON FeatureCollection")
        return self


class StepTimer:
    """Collects timed steps for an envelope: ``with t.step("embed", "64 chips"): ...``."""

    def __init__(self) -> None:
        self.steps: list[Step] = []

    @contextmanager
    def step(self, phase: str, detail: str = "") -> Iterator[Step]:
        s = Step(phase=phase, detail=detail)
        t0 = time.perf_counter()
        try:
            yield s
        finally:
            s.ms = round((time.perf_counter() - t0) * 1000, 2)
            self.steps.append(s)


def declined(tool: str, version: str, reason: str, **kw: Any) -> Envelope:
    return Envelope(tool=tool, version=version, status="declined", declined_reason=reason, **kw)


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}
