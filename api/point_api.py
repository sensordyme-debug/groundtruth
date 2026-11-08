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
    outline_top: int | None = Field(None, ge=0, le=600)
    calibrate: bool = True  # per-click background calibration (conf/investigate.json find_like); false = the raw scan


@router.post("/point/find")
def point_find(b: PointFindIn) -> JSONResponse:
    from api.app import _env, eng
    from groundtruth.envelope import declined
    from groundtruth.investigate import conf as inv_conf
    from groundtruth.point import calibrate, clip_to, resolve

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
    fl = inv_conf()["find_like"]
    ot = fl["outline_top"] if b.outline_top is None else b.outline_top
    # "not this" clicks go to the scan as SOFT negatives (a hard negative there drops every object whose patch is closer
    # to it than to the example, which wiped out true matches: 137 → 3 after one click); the calibration below then
    # rejects, patch by patch, results closer to a "not this" example than to the examples
    # the scan sees only the examples: "not this" is applied AFTER, result by result (a negative inside the scan, even a
    # soft one, removed nearly every parking lot: a rejected stretch of asphalt looks like all of them)
    if place or bbox:
        env = e.find_all(pos, [], place=None if bbox else place, bbox=bbox, concept=None, outline_top=ot)
    else:
        env = e.find_all(pos, [], k=b.k, concept=None, outline_top=min(ot, 60))
    cal = {"calibrated": False}
    if env.status != "declined" and env.result and (b.calibrate or neg):
        t1 = time.perf_counter()
        box = bbox or (e.place_bbox(place) if place else None)
        fs, cal = calibrate(e.index, env.result.geojson["features"], pos, neg, box, e.year, fl, use_threshold=b.calibrate)
        cal["ms"] = round((time.perf_counter() - t1) * 1000, 1)
        env.result.geojson["features"] = fs
        m = env.result.metrics
        m["count_before_calibration"] = m.get("count_detected", m.get("count"))
        m["count_detected"] = m["count"] = len(fs)
        if b.calibrate:
            env.limits.append(f"kept only results more similar to your examples than {float(fl['bg_quantile']) * 100:g} % of "
                              f"{fl['bg_samples']} background patches sampled from the same area (threshold "
                              f"{cal.get('threshold')}); similarities are uncalibrated, not probabilities")
        if neg:
            env.limits.append(f"dropped results closer to one of your {len(neg)} 'not this' examples than to your examples")
    if env.status != "declined" and area.get("geojson") and env.result:
        fs = clip_to(env.result.geojson["features"], area["geojson"])
        env.result.geojson["features"] = fs
        m = env.result.metrics
        m["count_before_clip"] = m.get("count_detected", m.get("count"))
        m["count_detected"] = m["count"] = len(fs)
        env.limits.append("clipped to the drawn area (features whose geometry intersects it)")
    out = env.model_dump(mode="json")
    if out.get("result"):
        out["result"]["metrics"].update(examples=pos, negatives=neg, resolved={"positives": prec, "negatives": nrec}, calibration=cal,
                                        exhaustive=bool(place or bbox), point_find_ms=round((time.perf_counter() - t0) * 1000))
    out["tool"] = "point_find"
    return JSONResponse(out)
