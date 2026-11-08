"""Investigations over HTTP (engine/groundtruth/investigate.py). Plain JSON (each step's tool output stays inside the
engine; the investigation carries the plan, step states, events, evidence, findings and the final feature set).

POST /api/v1/investigations            {objective, context}  → the investigation (status planning/running/…); runs in
                                         the background. {"fallback": "chat"} when the objective is a plain chat turn.
GET  /api/v1/investigations/{id}       → current state (poll it: step states change as tools actually finish)
POST /api/v1/investigations/{id}/cancel
GET  /api/v1/investigations/{id}/features/{fid}/evidence
POST /api/v1/investigations/{id}/feedback {feature_id, verdict: confirm|reject, note}
GET  /api/v1/investigations/{id}/report.md
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1")


class InvIn(BaseModel):
    objective: str = Field(..., min_length=2, max_length=600)
    context: dict[str, Any] = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    feature_id: str = Field(..., max_length=200)
    verdict: Literal["confirm", "reject"]
    note: str = Field("", max_length=400)


def _policy(text: str) -> str | None:
    from groundtruth.tools import _people
    if _people(text):
        return ("I can't help with information about people (owners, residents, demographics, crime or school ratings). "
                "Ask about things visible from above.")
    return None


def _owned(inv_id: str, owner: str | None):
    """The investigation if it belongs to the caller, else 404 (never 403: an id must not reveal that it exists).
    With auth on, the owner header is set by the server from the authenticated user (api/auth.py), never the client."""
    from api import auth
    from groundtruth import investigate as I
    inv = I.get(inv_id)
    if inv is None:
        raise HTTPException(404, "no such investigation")
    inv_owner = (inv.context or {}).get("_owner")
    if auth.enabled() and (not inv_owner or inv_owner != owner):
        raise HTTPException(404, "no such investigation")
    if not auth.enabled() and inv_owner and owner and inv_owner != owner:
        raise HTTPException(404, "no such investigation")
    return inv


def _view(inv, features: bool) -> dict[str, Any]:
    d = inv.model_dump(mode="json")
    if not features and d.get("result"):
        d["result"] = {"metrics": d["result"].get("metrics", {}), "count": len(d["result"].get("features", [])), "features": []}
    return d


@router.post("/investigations")
def create(b: InvIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    from api.app import eng
    from groundtruth import investigate as I

    if len(json.dumps(b.context)) > 200_000:
        raise HTTPException(413, "context too large")
    ctx = {**b.context, "_owner": (x_gt_owner or "")[:64]}  # the owner id (server-derived when auth is on), never a person
    if ctx.get("previous"):  # a follow-up may only start from the caller's own investigation
        try:
            _owned(str(ctx["previous"]), x_gt_owner)
        except HTTPException:
            ctx.pop("previous")
    inv = I.start(b.objective, ctx, lambda year: eng(None, year), _policy)
    if inv.status == "needs_input" and inv.summary == "fallback:chat":
        return {"fallback": "chat", "id": inv.id}
    return _view(inv, features=False)


@router.get("/investigations")
def list_mine(limit: int = 30, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    """This browser's investigations that produced a plan (newest first): id, objective, status, scope."""
    from groundtruth import investigate as I
    if not x_gt_owner:
        return {"investigations": []}
    out = []
    for p in sorted(I.store_dir().glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True)[:400]:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if (d.get("context") or {}).get("_owner") != x_gt_owner or not d.get("plan"):
            continue
        out.append({"id": d["id"], "objective": d["objective"], "status": d["status"], "updated": d["updated"],
                    "scope": ((d.get("plan") or {}).get("scope") or {}).get("label")})
        if len(out) >= limit:
            break
    return {"investigations": out}


@router.get("/investigations/{inv_id}")
def read(inv_id: str, features: bool = Query(False), x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    inv = _owned(inv_id, x_gt_owner)
    return _view(inv, features=features and inv.status not in ("planning", "running"))


@router.post("/investigations/{inv_id}/cancel")
def cancel(inv_id: str, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    from groundtruth import investigate as I
    _owned(inv_id, x_gt_owner)
    I.cancel(inv_id)
    return {"cancelling": inv_id, "note": "the step already running finishes; later steps are skipped"}


@router.get("/investigations/{inv_id}/features/{fid}/evidence")
def evidence(inv_id: str, fid: str, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    from groundtruth import investigate as I
    inv = _owned(inv_id, x_gt_owner)
    return I.feature_evidence(inv, fid)


@router.post("/investigations/{inv_id}/feedback")
def feedback(inv_id: str, b: FeedbackIn, x_gt_owner: str | None = Header(None)) -> dict[str, Any]:
    """A human verdict on one result: recorded as human evidence on the investigation and appended to a versioned
    feedback journal (data/app/v1/feedback/, separate from the immutable labels in data/labels/)."""
    from groundtruth import config
    from groundtruth import investigate as I
    inv = _owned(inv_id, x_gt_owner)
    concept = (inv.plan.targets[0] if inv.plan and inv.plan.targets else "")
    rec = {"t": time.time(), "investigation": inv_id, "feature_id": b.feature_id, "verdict": b.verdict, "note": b.note,
           "concept": concept, "scope": (inv.plan.scope or {}).get("label") if inv.plan else None, "versions": inv.versions}
    d = config.DATA / "app" / "v1" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "feedback.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    inv.evidence.append(I.Evidence(id=f"e{len(inv.evidence) + 1}", step_id="human", kind="human",
                                   statement=f"A reviewer {'confirmed' if b.verdict == 'confirm' else 'rejected'} result {b.feature_id}."
                                   + (f" Note: {b.note}" if b.note else ""), feature_ids=[b.feature_id], strength="strong",
                                   confidence_note="human review"))
    I.save(inv)
    return {"recorded": True, "evidence_id": inv.evidence[-1].id}


@router.get("/investigations/{inv_id}/report.md")
def report(inv_id: str, x_gt_owner: str | None = Header(None)) -> PlainTextResponse:
    from groundtruth import investigate as I
    inv = _owned(inv_id, x_gt_owner)
    return PlainTextResponse(I.report_markdown(inv), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="groundtruth-{inv_id}.md"'})
