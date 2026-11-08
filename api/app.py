"""Groundtruth engine API (contract: docs/CONTRACT.md). Separate process from the labelling server.

  uvicorn api.app:app --host 0.0.0.0 --port 8000
Every tool: POST /api/v1/tools/{tool} → Envelope (HTTP 200 even when declined).
"""

from __future__ import annotations

import json
import os
import time
import threading
from functools import lru_cache
from typing import Any

from fastapi import Request, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from groundtruth import __version__, config
from groundtruth.envelope import Envelope, declined
from groundtruth.index import ChipIndex
from groundtruth.providers.public import PublicDataProvider
from groundtruth.tools import NOT_IMPLEMENTED, VERSION, Engine

CONTRACT = "0.4"
app = FastAPI(title="Groundtruth API", version=__version__)
from api import auth as _auth  # noqa: E402

app.middleware("http")(_auth.middleware)  # authentication + server-side owner binding (docs/AUTH.md)
app.include_router(_auth.router)
_BOOT = _auth.ensure_bootstrap()
if _BOOT:
    print(f"[groundtruth] auth: created user 'owner'; its access token is in {_BOOT} (mode 0600). Not printed here.")
# CORS: the app is served same-origin (the API serves the UI); cross-origin access only from local dev servers and the
# tailnet host. Override with GROUNDTRUTH_CORS_ORIGINS (comma-separated). Was "*" (docs/SECURITY_AUDIT.md).
_CORS = [o.strip() for o in os.environ.get("GROUNDTRUTH_CORS_ORIGINS", "").split(",") if o.strip()] or [
    "http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5180", "http://localhost:5180",
    "http://127.0.0.1:5173", "http://localhost:5173"]
app.add_middleware(CORSMiddleware, allow_origins=_CORS, allow_origin_regex=r"https://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net",
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-GT-Owner"])
_provider = PublicDataProvider(download_scenes=False)
_lock = threading.Lock()

TOOLS = {"search_text": True, "pick_candidates": True, "find_similar": True, "find_all": True, "refine": True,
         "measure": True, "add_layer": True, "filter": True, "export": True, "outline": True, "compare_years": True,
         "find_place": True, "isochrone": True, "find_buildings": True, "find_anything": True}


_BBOX_CACHE: dict[str, tuple[float, list[float]]] = {}


def _index_bbox(d) -> list[float] | None:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    p = d / "chips.parquet"
    key, mt = str(p), p.stat().st_mtime
    if key not in _BBOX_CACHE or _BBOX_CACHE[key][0] != mt:
        t = pq.read_table(p, columns=["west", "south", "east", "north"])
        _BBOX_CACHE[key] = (mt, [round(pc.min(t["west"]).as_py(), 5), round(pc.min(t["south"]).as_py(), 5),
                                 round(pc.max(t["east"]).as_py(), 5), round(pc.max(t["north"]).as_py(), 5)])
    return _BBOX_CACHE[key][1]


def available_indexes() -> list[dict[str, Any]]:
    root = config.DATA / "precompute" / config.INDEX_VERSION
    out = []
    for info in sorted(root.glob("*/*/index.json")):
        d = json.loads(info.read_text())
        out.append({"aoi": info.parent.parent.name, "year": int(info.parent.name), "chips": d["chips"],
                    "index_version": d["index_version"], "bbox": _index_bbox(info.parent)})
    return out


def default_aoi(year: int) -> str:
    idx = [i for i in available_indexes() if i["year"] == year]
    if any(i["aoi"] == "county" for i in idx):
        return "county"
    if not idx:
        raise HTTPException(503, f"no index built for {year}")
    return max(idx, key=lambda i: i["chips"])["aoi"]


@lru_cache(maxsize=8)
def engine(aoi: str, year: int) -> Engine:
    return Engine(_provider, aoi=aoi, year=year, index=ChipIndex(config.precompute_dir(year, aoi)))


def eng(aoi: str | None, year: int | None) -> Engine:
    y = year or _provider.primary_year
    a = aoi or default_aoi(y)
    with _lock:
        return engine(a, y)


# ---------------- request bodies ----------------
class Common(BaseModel):
    year: int | None = None
    aoi: str | None = None


class SearchTextIn(Common):
    query: str
    k: int = Field(50, ge=1, le=500)


class PickIn(Common):
    query: str
    n: int = Field(6, ge=2, le=12)


class FindSimilarIn(Common):
    examples: list[str]
    exclude: list[str] = []
    k: int = Field(50, ge=1, le=500)
    bbox: list[float] | None = None
    rerank: bool = False
    concept: str | None = None


class FindAllIn(Common):
    examples: list[str]
    exclude: list[str] = []
    k: int = Field(200, ge=1, le=1000)
    bbox: list[float] | None = None
    place: str | None = None
    concept: str | None = None
    rerank: bool = True
    outline_top: int = Field(300, ge=0, le=500)
    within_layer: str | None = None
    dense: bool | None = None
    soft_exclude: list[str] = []


class RefineIn(Common):
    session_id: str | None = None
    accept: list[str] = []
    reject: list[str] = []
    k: int = Field(50, ge=1, le=500)
    bbox: list[float] | None = None
    place: str | None = None
    concept: str | None = None
    candidates: list[str] | None = None


class CompareYearsIn(Common):
    examples: list[str]
    from_year: int = 2020
    to_year: int | None = None
    concept: str | None = None


class OutlineIn(Common):
    examples: list[str]
    max: int = Field(24, ge=1, le=50)
    cached_only: bool = False
    concept: str | None = None


class GeoIn(Common):
    geojson: dict[str, Any]


class MeasureIn(GeoIn):
    to_layer: str | None = None
    to_layers: list[str] = []


class FilterIn(GeoIn):
    where: dict[str, Any] = {}


class ExportIn(GeoIn):
    format: str = "geojson"
    title: str | None = None
    summary: dict[str, Any] | None = None


class AddLayerIn(Common):
    name: str
    bbox: list[float] | None = None


_REPLAY = os.environ.get("GROUNDTRUTH_REPLAY") == "1"
_REPLAY_DIR = config.DATA / "replay" / "hero"


def _replay(tool: str) -> dict[str, Any] | None:
    """Replay fallback (GROUNDTRUTH_REPLAY=1): serve the recorded hero-flow envelope for this tool, clearly marked."""
    if not _REPLAY:
        return None
    files = sorted(_REPLAY_DIR.glob(f"*_{tool}.json"))
    if not files:
        return None
    env = json.loads(files[-1].read_text())["response"]
    env["method"] = "[REPLAY] " + env.get("method", "")
    env["steps"] = [{"phase": "replay", "detail": f"recorded response {files[-1].name} (not a live computation)", "ms": 0}] + env.get("steps", [])
    env["limits"] = ["REPLAY: this is a recorded response from an earlier live run, not computed now"] + env.get("limits", [])
    return env


WARM: dict[str, Any] = {"done": os.environ.get("GROUNDTRUTH_WARMUP", "1") != "1", "error": None, "ms": None,
                        "place": os.environ.get("GROUNDTRUTH_WARMUP_PLACE", "Ballantyne")}


@app.on_event("startup")
def _warmup() -> None:
    """Before reporting healthy: load models, touch the index, and run pick + find_all (in the warm-up place) + refine
    on a dummy query, so the first real call is warm. /health reports ok=false, warming=true until done.
    GROUNDTRUTH_WARMUP=0 skips it; GROUNDTRUTH_WARMUP_PLACE picks the place (default Ballantyne)."""
    if WARM["done"]:
        return

    def run() -> None:
        import time as _t

        t0 = _t.perf_counter()
        ph = WARM.setdefault("phases", {})  # per-phase startup profile, reported by /health
        last = [t0]

        def mark(name: str) -> None:
            now = _t.perf_counter()
            ph[name] = round((now - last[0]) * 1000)
            last[0] = now

        def idle() -> None:  # background warm-up yields to real requests (GPU + CPU are shared)
            while _t.time() - LAST_REQ["t"] < 3.0 or LAST_REQ["active"] > 0:
                _t.sleep(0.5)

        try:
            e = eng(None, None)
            mark("engine_init")
            pc = e.pick_candidates("swimming pool")
            mark("models_pick_candidates")
            feats = [f for f in pc.result.geojson["features"] if not f["properties"].get("no_clear_example")]
            ex = next((f["properties"]["example"] for f in feats if f["properties"]["sense_id"] == "backyard_pool"),
                      feats[0]["properties"]["example"])
            neg = [f["properties"]["example"] for f in feats if f["properties"]["example"] != ex]
            fa = e.find_all([ex], [], place=WARM["place"] if e.aoi == "county" else None, concept="swimming_pool",
                            soft_exclude=neg)
            mark("find_all_warmup_place")
            hits = fa.result.geojson["features"]
            if hits:
                e.refine(None, [ex, hits[0]["properties"]["example"]], [hits[-1]["properties"]["example"]], k=50,
                         concept="swimming_pool", candidates=[h["properties"]["example"] for h in hits])
            mark("refine")
            WARM["core_ms"] = round((_t.perf_counter() - t0) * 1000)
            # the flagship investigation (what the app runs first): derived set, FEMA, lidar, geography, evidence
            try:
                from groundtruth import investigate as _I
                _inv = _I.start("Find residential properties near Lake Norman with swimming pools that intersect mapped "
                                "100-year flood zones. Rank the strongest candidates.", {"_owner": "warmup"},
                                lambda year: eng(None, year), lambda t: None, background=False)
                WARM["flagship"] = _inv.status
            except Exception as err:  # noqa: BLE001
                WARM["flagship"] = f"error: {type(err).__name__}"
            mark("flagship_investigation")
            if os.environ.get("LOCAL_LLM", "1") == "1":  # local LLM: one tiny plan so the first chat isn't cold
                try:
                    from api.planner.llm import local

                    if local.health():
                        _, info = local.llm_plan("houses with pools", None)
                        WARM["llm"] = {"ok": not info.get("error"), "ms": info.get("ms")}
                    else:
                        WARM["llm"] = {"ok": False, "error": "unreachable (rules planner will answer)"}
                except Exception as err:  # never block the API on the laptop
                    WARM["llm"] = {"ok": False, "error": f"{type(err).__name__}: {err}"[:160]}
            mark("llm_ping")

            def legacy_chain() -> None:
                """The conversational chat path (/agent/run) with exactly the contexts web/app-v2 sends, so plain
                searches are warm too (planner + LLM over the laptop tunnel + the county overview)."""
                if not (e.aoi == "county" and os.environ.get("GROUNDTRUTH_WARMUP_CHAIN", "1") == "1"):
                    return
                from api.planner.brain import chat

                def ui_chain(texts, first=None):
                    c = dict(first or {"place": None, "last_text": None, "picked": [], "not": []})
                    for tx in texts:
                        idle()
                        o = chat(lambda year: eng(None, year), tx, dict(c))
                        p = o.get("plan") or {}
                        c = {**c, "place": p.get("place") or c.get("place"), "last_text": p.get("effective_text") or tx}
                    return c

                tc = _t.perf_counter()
                ui_chain(["Houses with pools", "in Lake Norman", "with lake and dock access", "which got docks since 2020"])
                ui_chain(["Swimming pools"])
                c = ui_chain(["Swimming pools"], {"place": None, "last_text": None, "picked": ["backyard_pool"], "not": []})
                ui_chain(["in Lake Norman"], c)
                ui_chain(["pools in flood zones in Lake Norman", "who owns these houses"])
                WARM["chain_ms"] = round((_t.perf_counter() - tc) * 1000)

            # GROUNDTRUTH_WARMUP_CHAIN_BLOCKING=1 restores the old order (healthy only after the chat chain), for A/B timing
            blocking = os.environ.get("GROUNDTRUTH_WARMUP_CHAIN_BLOCKING", "0") == "1"
            if blocking:
                legacy_chain()
                mark("chat_chain_blocking")
            # results are cached now: hand the index file pages back (page cache to Windows), incl. the 2020 index
            e.index.release_pages()
            for other in e.__dict__.get("_other_index", {}).values():
                other.release_pages()
            WARM["healthy_ms"] = round((_t.perf_counter() - t0) * 1000)
            WARM["done"] = True  # healthy: the core and the flagship investigation are warm
            if not blocking:
                legacy_chain()  # background, yielding to requests
                mark("chat_chain_background")
            if e.aoi == "county" and os.environ.get("GROUNDTRUTH_WARMUP_PLACES", "1") == "1":
                for pl in json.loads((config.SENSES_PATH.parent / "places.json").read_text())["places"]:
                    idle()
                    e.find_all([ex], [], place=pl, concept="swimming_pool", soft_exclude=neg)
                WARM["places_ms"] = round((_t.perf_counter() - t0) * 1000)
                mark("places_background")
                e.index.release_pages()
        except Exception as err:  # never block health forever; report it
            WARM["error"] = f"{type(err).__name__}: {err}"[:300]
        finally:
            WARM["ms"] = round((_t.perf_counter() - t0) * 1000)
            WARM["done"] = True

    threading.Thread(target=run, daemon=True, name="warmup").start()


LAST_REQ: dict[str, Any] = {"t": 0.0, "active": 0}


@app.middleware("http")
async def replay_middleware(request, call_next):
    import time as _t

    if _REPLAY and request.method == "POST" and request.url.path.startswith("/api/v1/tools/"):
        r = _replay(request.url.path.rsplit("/", 1)[-1])
        if r is not None:
            return JSONResponse(r, headers={"X-Groundtruth-Replay": "1"})
    busy = request.method == "POST"
    if busy:
        LAST_REQ["active"] += 1
    try:
        return await call_next(request)
    finally:
        if busy:
            LAST_REQ["active"] -= 1
            LAST_REQ["t"] = _t.time()


def _env(e: Envelope) -> JSONResponse:
    return JSONResponse(e.model_dump(mode="json"))


# ---------------- meta ----------------
@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"ok": bool(WARM["done"]), "warming": not WARM["done"], "auth": "on" if _auth.enabled() else "off (development mode)",
            "warmup": {k: WARM.get(k) for k in ("core_ms", "healthy_ms", "chain_ms", "places_ms", "ms", "error", "place", "llm",
                                                "flagship", "phases")},
            "contract": CONTRACT, "engine_version": VERSION, "indexes": available_indexes(),
            "primary_year": _provider.primary_year}


@app.get("/api/v1/tools")
def tools() -> list[dict[str, Any]]:
    return [{"tool": t, "implemented": ok, "version": VERSION} for t, ok in TOOLS.items()]


@app.get("/api/v1/layers")
def layers() -> list[str]:
    return _provider.layer_names()


@app.get("/api/v1/chips/{chip_id}.jpg")
def chip(chip_id: str, year: int | None = None, aoi: str | None = None) -> Response:
    y = year or int(chip_id.split("_", 1)[0])
    e = eng(aoi, y)
    if chip_id not in e.index.pos:
        raise HTTPException(404, "unknown chip")
    return Response(e.index.thumb_jpeg(e.index.pos[chip_id]), media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"})


@app.get("/api/v1/events")
def events() -> Response:
    return Response(status_code=501, content="reserved for the agent SSE stream (contract v0.1)")


class AgentIn(BaseModel):
    text: str
    aoi: str | None = None
    context: dict[str, Any] | None = None  # {place, last_text} from the UI


@app.post("/api/v1/agent/run")
def agent_run(b: AgentIn) -> JSONResponse:
    """Rules planner (deterministic, no LLM): parse → real tool calls → map actions citing step envelopes."""
    from groundtruth.planner import run as plan_run

    from api.planner.brain import chat

    return JSONResponse(chat(lambda year: eng(b.aoi, year), b.text, b.context))


# ---------------- tools ----------------
@app.post("/api/v1/tools/search_text")
def t_search_text(b: SearchTextIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).search_text(b.query, k=b.k))


@app.post("/api/v1/tools/pick_candidates")
def t_pick(b: PickIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).pick_candidates(b.query, n=b.n))


@app.post("/api/v1/tools/find_similar")
def t_find_similar(b: FindSimilarIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).find_similar(b.examples, b.exclude, k=b.k, bbox=b.bbox, rerank=b.rerank,
                                                 concept=b.concept))


@app.post("/api/v1/tools/find_all")
def t_find_all(b: FindAllIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).find_all(b.examples, b.exclude, k=b.k, bbox=b.bbox, place=b.place,
                                            concept=b.concept, rerank=b.rerank, outline_top=b.outline_top,
                                            within_layer=b.within_layer, dense=b.dense, soft_exclude=b.soft_exclude))


@app.post("/api/v1/tools/compare_years")
def t_compare_years(b: CompareYearsIn) -> JSONResponse:
    return _env(eng(b.aoi, b.to_year or b.year).compare_years(b.examples, from_year=b.from_year, concept=b.concept))


@app.post("/api/v1/tools/refine")
def t_refine(b: RefineIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).refine(b.session_id, b.accept, b.reject, k=b.k, bbox=b.bbox, place=b.place,
                                          concept=b.concept, candidates=b.candidates))


@app.post("/api/v1/tools/outline")
def t_outline(b: OutlineIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).outline(b.examples, max=b.max, cached_only=b.cached_only, concept=b.concept))


@app.post("/api/v1/tools/measure")
def t_measure(b: MeasureIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).measure(b.geojson, to_layer=b.to_layer, to_layers=b.to_layers))


@app.post("/api/v1/tools/filter")
def t_filter(b: FilterIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).filter(b.geojson, b.where))


class FindPlaceIn(Common):
    query: str
    bbox: list[float] | None = None
    place: str | None = None
    limit: int = Field(5000, ge=1, le=20000)


class IsochroneIn(Common):
    origin: Any
    minutes: float = Field(10, gt=0, le=60)
    mode: str = "drive"


class FindBuildingsIn(Common):
    kind: str = "building"
    bbox: list[float] | None = None
    place: str | None = None
    limit: int = Field(20000, ge=1, le=100000)
    min_height_m: float | None = None


class FindAnythingIn(Common):
    text: str
    bbox: list[float] | None = None
    place: str | None = None


@app.post("/api/v1/tools/find_place")
def t_find_place(b: FindPlaceIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).find_place(b.query, bbox=b.bbox, place=b.place, limit=b.limit))


@app.post("/api/v1/tools/isochrone")
def t_isochrone(b: IsochroneIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).isochrone(b.origin, b.minutes, b.mode))


@app.post("/api/v1/tools/find_buildings")
def t_find_buildings(b: FindBuildingsIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).find_buildings(b.kind, bbox=b.bbox, place=b.place, limit=b.limit,
                                                  min_height_m=b.min_height_m))


@app.post("/api/v1/tools/find_anything")
def t_find_anything(b: FindAnythingIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).find_anything(b.text, bbox=b.bbox, place=b.place))


_EXPORT_OWNERS = config.DATA / "app" / "v1" / "export_owners.jsonl"


def _export_owner(name: str) -> str | None:
    if not _EXPORT_OWNERS.exists():
        return None
    for line in _EXPORT_OWNERS.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("name") == name:
            return r.get("owner")
    return None


@app.post("/api/v1/tools/export")
def t_export(b: ExportIn, request: Request) -> JSONResponse:
    env = eng(b.aoi, b.year).export(b.geojson, b.format, title=b.title, summary=b.summary)
    name = (env.result.metrics or {}).get("download", "").rsplit("/", 1)[-1] if env.status == "ok" else ""
    if name:  # who made this file: only they can download it
        _EXPORT_OWNERS.parent.mkdir(parents=True, exist_ok=True)
        with open(_EXPORT_OWNERS, "a") as fh:
            fh.write(json.dumps({"name": name, "owner": request.headers.get("x-gt-owner"), "t": time.time()}) + "\n")
    return _env(env)


@app.get("/api/v1/exports/{name}")
def export_file(name: str, request: Request) -> Response:
    d = (config.DATA / "out" / "exports").resolve()
    p = (d / name).resolve()
    if p.parent != d or not p.exists():
        raise HTTPException(404)
    if _auth.enabled() and _export_owner(name) != request.headers.get("x-gt-owner"):
        raise HTTPException(404)  # not yours (or made before ownership was recorded): indistinguishable from missing
    return FileResponse(p, filename=name)


@app.get("/api/v1/places")
def places() -> dict[str, Any]:
    d = json.loads((config.SENSES_PATH.parent / "places.json").read_text())
    return {"source": d["_source"], "places": {k: v["bbox"] for k, v in d["places"].items()}}


@app.post("/api/v1/tools/add_layer")
def t_add_layer(b: AddLayerIn) -> JSONResponse:
    return _env(eng(b.aoi, b.year).add_layer(b.name, b.bbox))


@app.post("/api/v1/tools/{tool}")
def t_unknown(tool: str) -> JSONResponse:
    return _env(declined(tool, VERSION, NOT_IMPLEMENTED if tool in TOOLS else f"unknown tool {tool!r}"))


# ---------------- NAIP display tiles (cached; same source as the labelling page) ----------------
_TILE_SEARCH: dict[int, str] = {}


@app.get("/api/v1/tiles/naip/{year}/{z}/{x}/{y}.png")
def naip_tile(year: int, z: int, x: int, y: int) -> Response:
    import httpx

    hdr = {"Cache-Control": "max-age=86400"}
    if z < 10 or z > 18 or year not in _provider.imagery_years():
        return Response(status_code=204)
    # 1) local COGs (rio-tiler, TiTiler's engine) when every scene under the tile is cached locally
    if os.environ.get("GROUNDTRUTH_LOCAL_TILES", "1") == "1":
        # rendered tiles up to z15 are kept on disk (low zooms read many scenes: slow to render, and they would hold the
        # browser's few connections while a chat request waits)
        lc = config.DATA / "cache" / "tiles" / "local" / "v1" / str(year) / str(z) / str(x) / f"{y}.png"
        if z <= 15 and lc.exists():
            if lc.stat().st_size == 0:
                return Response(status_code=204)
            return FileResponse(lc, media_type="image/png", headers={**hdr, "X-Tile-Source": "local-cache"})
        png = _provider.render_tile(year, z, x, y)
        if z <= 15 and png is not None:
            lc.parent.mkdir(parents=True, exist_ok=True)
            tmp = lc.with_suffix(f".{os.getpid()}.tmp"); tmp.write_bytes(png); os.replace(tmp, lc)
        if png == b"":
            return Response(status_code=204)
        if png is not None:
            return Response(png, media_type="image/png", headers={**hdr, "X-Tile-Source": "local-cog"})
    # 2) disk tile cache from earlier Planetary Computer fetches
    cache = config.DATA / "cache" / "tiles" / "naip" / str(year) / str(z) / str(x) / f"{y}.png"
    if cache.exists():
        return FileResponse(cache, media_type="image/png", headers={**hdr, "X-Tile-Source": "tile-cache"})
    # 3) Planetary Computer (fallback only; blocked when GROUNDTRUTH_OFFLINE=1)
    if config.offline():
        return Response(status_code=204, headers={"X-Tile-Source": "offline-miss"})
    base = "https://planetarycomputer.microsoft.com/api/data/v1"
    for attempt in range(2):
        if year not in _TILE_SEARCH or attempt:
            body = {"collections": ["naip"], "filter-lang": "cql2-json", "filter": {"op": "and", "args": [
                {"op": "=", "args": [{"property": "naip:year"}, str(year)]},
                {"op": "=", "args": [{"property": "naip:state"}, "nc"]}]}}
            _TILE_SEARCH[year] = httpx.post(f"{base}/mosaic/register", json=body, timeout=30).json()["searchid"]
        r = httpx.get(f"{base}/mosaic/{_TILE_SEARCH[year]}/tiles/WebMercatorQuad/{z}/{x}/{y}@1x.png",
                      params={"collection": "naip", "assets": "image", "asset_bidx": "image|1,2,3", "nodata": "0"},
                      timeout=60)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp"); tmp.write_bytes(r.content); os.replace(tmp, cache)
            return Response(r.content, media_type="image/png", headers={**hdr, "X-Tile-Source": "planetary-computer"})
        if r.status_code == 404 and "Unknown search" not in r.text:
            return Response(status_code=204)
    return Response(status_code=502)


# ---------------- vector tiles (PMTiles built by scripts/build_pmtiles.py; MapLibre source-layer = layer name) -----
_PMT: dict[str, Any] = {}


@app.get("/api/v1/vt/{layer}/{z}/{x}/{y}.pbf")
def vector_tile(layer: str, z: int, x: int, y: int) -> Response:
    from pmtiles.reader import MmapSource, Reader

    p = config.data_dir("tiles") / f"{layer}.pmtiles"
    if not p.exists() or "/" in layer or ".." in layer:
        raise HTTPException(404, f"no vector tiles for {layer!r}")
    if layer not in _PMT:
        f = open(p, "rb")
        _PMT[layer] = Reader(MmapSource(f))
    data = _PMT[layer].get(z, x, y)
    if not data:
        return Response(status_code=204)
    return Response(data, media_type="application/x-protobuf",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "max-age=86400"})


@app.get("/api/v1/vt")
def vector_tile_layers() -> dict[str, Any]:
    conf = json.loads((config.SENSES_PATH.parent / "vector_tiles.json").read_text())["layers"]
    return {k: {"minzoom": v["minzoom"], "maxzoom": v["maxzoom"], "url": f"/api/v1/vt/{k}/{{z}}/{{x}}/{{y}}.pbf"}
            for k, v in conf.items() if (config.data_dir("tiles") / f"{k}.pmtiles").exists()}


@app.get("/api/v1/boundary")
def boundary() -> dict[str, Any]:
    return _provider.get_boundary()


# ---------------- NAIP crops (thumbnails + the detail sheet's 2020↔2022 swipe), from the local COGs ----------------
@app.get("/api/v1/crop")
def naip_crop(bbox: str, year: int = 2022, px: int = 256) -> Response:
    """JPEG crop of NAIP for bbox=w,s,e,n (EPSG:4326) and year, px on the long side (≤ 640). Cached on disk."""
    import hashlib
    import io

    from PIL import Image

    try:
        b = [float(v) for v in bbox.split(",")]
        assert len(b) == 4 and b[0] < b[2] and b[1] < b[3] and (b[2] - b[0]) < 0.05 and (b[3] - b[1]) < 0.05
    except (ValueError, AssertionError):
        raise HTTPException(400, "bbox=w,s,e,n (EPSG:4326, at most ~5 km)")
    px = max(32, min(int(px), 640))
    key = hashlib.sha1(f"{[round(v, 6) for v in b]}|{year}|{px}".encode()).hexdigest()[:20]
    cache = config.DATA / "cache" / "crops" / "v1" / f"{key}.jpg"
    if cache.exists():
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    im = _provider.get_imagery(tuple(b), year)
    img = Image.fromarray(im.rgb)
    img.thumbnail((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(cache)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# ---------------- UIs: the conversational app (web/app-v2/dist) at "/", the classic demo UI (web/dist) at "/classic" --
_DIST = config.ROOT / "web" / "dist"
_V2 = config.ROOT / "web" / "app-v2" / "dist"


@app.get("/")
def ui_index() -> Response:
    if (_V2 / "index.html").exists():
        return FileResponse(_V2 / "index.html", headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": True, "ui": "not built (cd web/app-v2 && npm run build)", "classic": "/classic"})


@app.get("/s/{sid}")
def ui_session(sid: str) -> Response:  # deep link to a saved session: the app reads the id from the URL
    return ui_index()


from api.investigations_api import router as _inv_router  # noqa: E402
from api.meta_api import router as _meta_router  # noqa: E402
from api.point_api import router as _point_router  # noqa: E402
from api.sessions import router as _sessions_router  # noqa: E402

app.include_router(_sessions_router)
app.include_router(_point_router)
app.include_router(_inv_router)
app.include_router(_meta_router)
for _mod in ("api.places_api", "api.validation_api", "api.scenarios_api", "api.terrain_api"):  # optional routers (present once built); a missing one is skipped
    try:
        app.include_router(__import__(_mod, fromlist=["router"]).router)
    except ImportError as _e:
        print(f"[groundtruth] optional router {_mod} not loaded: {_e}")


@app.get("/v2assets/{path:path}")
def ui_v2_assets(path: str) -> Response:
    p = (_V2 / "v2assets" / path).resolve()
    if not p.is_relative_to((_V2 / "v2assets").resolve()) or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/classic")
def ui_classic() -> Response:
    if (_DIST / "index.html").exists():
        return FileResponse(_DIST / "index.html", headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": True, "ui": "classic not built (cd web && npm run build)"})


@app.get("/assets/{path:path}")
def ui_assets(path: str) -> Response:
    p = (_DIST / "assets" / path).resolve()
    if not p.is_relative_to((_DIST / "assets").resolve()) or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)
