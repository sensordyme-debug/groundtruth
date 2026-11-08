"""POST /api/v1/point/find: "Find like this" (T2). Clicks / boxes / existing results → examples → find_all.

Body: {"positives": [{lon, lat} | {bbox} | {id}], "negatives": [...], "area": {place | bbox | geojson} | null,
       "year": 2022, "k": 500}
With an area the scan is exhaustive inside it (a count); without one it is the top-k county-wide ("top matches").
Negatives are hard ("not this": an object closer to them than to the examples is dropped).
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1")


class PointFindIn(BaseModel):
    positives: list[dict[str, Any]] = Field(..., min_length=1, max_length=40)
    negatives: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    area: dict[str, Any] | None = None
    year: int | None = None
    k: int = Field(500, ge=10, le=2000)
    outline_top: int = Field(120, ge=0, le=600)


@router.post("/point/find")
def point_find(b: PointFindIn) -> JSONResponse:
    from api.app import _env, eng
    from groundtruth.envelope import declined
    from groundtruth.point import clip_to, resolve

    t0 = time.perf_counter()
    e = eng(None, b.year)
    pos, prec = resolve(e.index, b.positives, e.year)
    neg, nrec = resolve(e.index, b.negatives, e.year)
    neg = [n for n in neg if n not in pos]
    if not pos:
        return _env(declined("point_find", "0.1.0", "nothing indexed under that point (outside the county imagery?)"))
    area = b.area or {}
    place, bbox = area.get("place"), area.get("bbox")
    if area.get("geojson") and not bbox:
        from shapely.geometry import shape
        bbox = list(shape(area["geojson"]).bounds)
    if place or bbox:
        env = e.find_all(pos, neg, place=None if bbox else place, bbox=bbox, concept=None, outline_top=b.outline_top)
    else:
        env = e.find_all(pos, neg, k=b.k, concept=None, outline_top=min(b.outline_top, 60))
    if env.status != "declined" and area.get("geojson") and env.result:
        fs = clip_to(env.result.geojson["features"], area["geojson"])
        env.result.geojson["features"] = fs
        m = env.result.metrics
        m["count_before_clip"] = m.get("count_detected", m.get("count"))
        m["count_detected"] = m["count"] = len(fs)
        env.limits.append("clipped to the drawn area (features whose geometry intersects it)")
    out = env.model_dump(mode="json")
    if out.get("result"):
        out["result"]["metrics"].update(examples=pos, negatives=neg, resolved={"positives": prec, "negatives": nrec},
                                        exhaustive=bool(place or bbox), point_find_ms=round((time.perf_counter() - t0) * 1000))
    out["tool"] = "point_find"
    return JSONResponse(out)
