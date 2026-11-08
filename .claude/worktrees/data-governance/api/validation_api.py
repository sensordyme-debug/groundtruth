"""Validation Studio API: human review of AI detections (plain JSON, no envelope). See docs/VALIDATION_STUDIO.md.

The lead mounts it with ``app.include_router(validation_api.router)``. Storage: groundtruth.validation.ValidationStore
in data/validation/v1/ (samples immutable; reviews = append-only journal + atomic snapshot + backups). Reviewers are
identified only by the anonymous X-GT-Owner browser id. Nothing here touches data/labels/.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from groundtruth import validation as V

router = APIRouter(prefix="/api/v1")
_STORE: V.ValidationStore | None = None
_ROOT: Path | None = None
_INIT = threading.Lock()


def store() -> V.ValidationStore:
    global _STORE
    s = _STORE
    if s is None or (not s.samples and (s.root / "samples.jsonl").exists()):  # samples built after first use
        with _INIT:
            if _STORE is None or _STORE is s:
                _STORE = V.ValidationStore(_ROOT)
    return _STORE


def reset_for_tests(root: Path | None = None) -> None:
    """Point the router at another folder (tests) and drop the cached store."""
    global _STORE, _ROOT
    _STORE, _ROOT = None, root


def _reviewer(x: str | None) -> str:
    try:
        return V.check_reviewer(x)
    except V.ValidationError as e:
        raise HTTPException(400, f"X-GT-Owner header: {e}") from None


class ReviewIn(BaseModel):
    sample_id: str = Field(..., max_length=128)
    decision: Literal["accept", "reject", "uncertain", "needs_review", "corrected"]
    polygon: dict[str, Any] | None = None
    note: str = Field("", max_length=V.MAX_NOTE)
    review_id: str | None = Field(None, max_length=64, description="optional client id: retries are idempotent")


class CensusIn(BaseModel):
    tile_id: str = Field(..., max_length=128)
    polygons: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    complete: bool = True
    note: str = Field("", max_length=V.MAX_NOTE)


@router.get("/validation/samples")
def list_samples(cls: str | None = None, region: str | None = None,
                 status: str = Query("unreviewed", description="unreviewed | reviewed | all | <decision>"),
                 limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    if cls is not None and cls not in V.CLASSES:
        raise HTTPException(400, f"cls must be one of {V.CLASSES}")
    try:
        return store().list_samples(cls=cls, region=region, status=status, limit=limit, offset=offset)
    except V.ValidationError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/validation/samples/{sample_id}")
def get_sample(sample_id: str) -> dict[str, Any]:
    s = store()
    if sample_id not in s.samples:
        raise HTTPException(404, "unknown sample")
    return s.sample_view(sample_id, with_history=True)


@router.post("/validation/reviews")
def post_review(b: ReviewIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    rv = _reviewer(x_gt_owner)
    s = store()
    if b.sample_id not in s.samples:
        raise HTTPException(404, "unknown sample")
    try:
        r = s.add_review(sample_id=b.sample_id, reviewer=rv, decision=b.decision, polygon=b.polygon, note=b.note,
                         review_id=b.review_id)
    except V.ValidationError as e:
        raise HTTPException(422, str(e)) from None
    return {"review": r, "sample": s.sample_view(b.sample_id)}


@router.get("/validation/census")
def list_census() -> dict[str, Any]:
    s = store()
    lc = s.latest_census()
    return {"tiles": [{**t, "census": lc.get(tid)} for tid, t in sorted(s.census_tiles.items())]}


@router.post("/validation/census")
def post_census(b: CensusIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    rv = _reviewer(x_gt_owner)
    s = store()
    if b.tile_id not in s.census_tiles:
        raise HTTPException(404, "unknown census tile")
    try:
        return {"census": s.add_census(tile_id=b.tile_id, reviewer=rv, polygons=b.polygons, complete=b.complete,
                                       note=b.note)}
    except V.ValidationError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/validation/progress")
def progress() -> dict[str, Any]:
    return store().progress()


@router.get("/validation/metrics")
def get_metrics(exclude_label_cluster: bool = False) -> dict[str, Any]:
    return store().metrics(exclude_label_cluster=exclude_label_cluster)


@router.get("/validation/export")
def export(format: Literal["geojson", "csv"] = "geojson"):  # noqa: A002
    s = store()
    if format == "csv":
        return PlainTextResponse(s.export_csv(), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="validation_{V.VERSION}.csv"'})
    return JSONResponse(s.export_geojson(), headers={
        "Content-Disposition": f'attachment; filename="validation_{V.VERSION}.geojson"'})
