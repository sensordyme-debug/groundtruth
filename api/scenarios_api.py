"""SYNTHETIC what-if scenarios (engine/groundtruth/scenario.py). Every response says it is simulated: the payloads
carry is_synthetic=true and simulation_status, and the UI draws them in a distinct style. Read-only."""
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1")


def _s():
    from groundtruth import scenario as S
    return S


@router.get("/scenarios")
def list_scenarios() -> dict[str, Any]:
    return {"scenarios": _s().scenarios(), "is_synthetic": True}


@router.get("/scenarios/{sid}")
def scenario_meta(sid: str) -> dict[str, Any]:
    S = _s()
    try:
        return S.meta(sid)
    except KeyError:
        raise HTTPException(404, "unknown scenario") from None
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None


def _offset(sid: str, offset_m: float) -> float:
    S = _s()
    try:
        offs = [float(x) for x in next(d for d in S.conf()["scenarios"] if d["id"] == sid)["offsets_m"]]
    except StopIteration:
        raise HTTPException(404, "unknown scenario") from None
    if not any(abs(offset_m - o) < 1e-6 for o in offs):
        raise HTTPException(422, f"offset_m must be one of {offs}")
    return offset_m


@router.get("/scenarios/{sid}/extent")
def scenario_extent(sid: str, offset_m: float) -> dict[str, Any]:
    try:
        return _s().extent(sid, _offset(sid, offset_m))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/scenarios/{sid}/impacts")
def scenario_impacts(sid: str, offset_m: float) -> dict[str, Any]:
    try:
        return _s().impacts(sid, _offset(sid, offset_m))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
