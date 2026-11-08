"""Place-intelligence routes (third-party place providers). Mount with ``app.include_router(places_api.router)``.

- POST /api/v1/places/search     -> search_places envelope (HTTP 200 even when declined, per docs/CONTRACT.md)
- GET  /api/v1/places/providers  -> per provider: enabled, reason, key present yes/no (NEVER the value), caps, usage
- GET  /api/v1/places/usage      -> today's usage per provider from the ledger (UTC day)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from groundtruth.places import build_providers, load_conf, search_places
from groundtruth.places.base import UsageLedger, ledger_path, utc_now_iso

router = APIRouter(prefix="/api/v1")


class PlacesSearchRequest(BaseModel):
    query: str = Field(default="", max_length=300)
    area: dict[str, Any]
    categories: list[str] = Field(default_factory=list, max_length=10)
    providers: list[str] | None = Field(default=None, max_length=5)
    investigation_id: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/places/search")
def places_search(req: PlacesSearchRequest) -> dict[str, Any]:
    # people-data policy BEFORE any third-party call: such text is never forwarded to a provider
    from groundtruth.envelope import declined
    from groundtruth.tools import _people
    if _people(req.query) or any(_people(c) for c in req.categories):
        return declined("search_places", "0.1.0", "I can't help with information about people (owners, residents, "
                        "demographics, crime or school ratings).").model_dump()
    env = search_places(req.query, req.area, req.categories, providers=req.providers,
                        investigation_id=req.investigation_id, limit=req.limit)
    return env.model_dump()


@router.get("/places/providers")
def places_providers() -> dict[str, Any]:
    conf = load_conf()
    provs = build_providers(conf)
    usage = UsageLedger(ledger_path(conf)).usage_today()
    out = []
    for name, p in provs.items():
        st = p.status()  # key_present is a boolean; the key value is never read into this response
        st["usage_today"] = usage.get(name, {})
        out.append(st)
    return {"providers": out, "date_utc": utc_now_iso()[:10]}


@router.get("/places/usage")
def places_usage() -> dict[str, Any]:
    conf = load_conf()
    usage = UsageLedger(ledger_path(conf)).usage_today()
    caps = {n: {"daily_request_cap": c.get("daily_request_cap"), "daily_usd_cap": c.get("daily_usd_cap")}
            for n, c in (conf.get("providers") or {}).items()}
    return {"date_utc": utc_now_iso()[:10], "usage": usage, "caps": caps,
            "note": "estimated_cost_usd uses conf/providers.json list prices; it is an estimate, not an invoice."}
