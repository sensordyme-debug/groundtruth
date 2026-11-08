# Security audit

Date: 2026-11-08. Scope: the worktree `gt-redesign` on branch `intelligence/evidence-grounded-investigation`.
- Code: `api/`, `engine/groundtruth/places/`, `scripts/run_*.sh`, `scripts/tailnet_serve.sh`, `.gitignore`.
- Git history: every branch and remote of the shared repository.

No code was edited. The suggested patches are for the lead to apply. Line numbers are from the files as they stood
on 2026-11-08; `api/app.py` was being edited concurrently, so its numbers may drift by a line or two.

## Deployment assumption

The deployment is **tailnet-only**:
- **API bind.** The API binds `127.0.0.1:8000` inside WSL (`scripts/run_api.sh:6`, `HOST` default `127.0.0.1`).
- **Exposure.** Windows forwards localhost into WSL. `scripts/tailnet_serve.sh:16` runs
  `tailscale serve --bg --tcp 8000 tcp://localhost:8000`, gated behind `--i-am-monish-and-approve`.
- **No Funnel.** `docs/DEMO_DAY.md:104` says "Never run `tailscale funnel`".
- **Label server.** It binds `127.0.0.1` only (`scripts/run_label_server.sh:14`).
- **Local LLM.** It binds `127.0.0.1` on the laptop and is reached through an SSH local-forward
  (`scripts/heavy/llm/llm.conf`).

Every finding below is rated **under this assumption**. Anyone on the tailnet, or any web page opened by a tailnet
user (because CORS is `*`), can reach the whole API with no credentials. If the API is ever exposed beyond the
tailnet, items S1–S4 become critical.

Notes on the exposure setup:
- `tailscale serve --tcp` is a raw TCP proxy. WireGuard encrypts it between nodes, but it injects **no**
  Tailscale identity headers.
- `tailscale serve --https=443 http://localhost:8000` (HTTPS mode) would add `Tailscale-User-Login` headers, which
  could serve as cheap authentication. See fix S1.
- The `app.py:3` docstring still shows `--host 0.0.0.0`. Under WSL mirrored networking, 0.0.0.0 could expose the API
  to the LAN. Correct the docstring to `127.0.0.1`.

## Secrets

| check | result |
|---|---|
| `.env` git-ignored | **Yes.** `.gitignore:21` `.env`. `git check-ignore -v .env` → `.gitignore:21`. |
| `.env.example` present | **No.** It also **cannot** be committed: `.gitignore:22` `.env.*` matches it (`git check-ignore -v .env.example` → `.gitignore:22`). Fix S9. |
| `.env` exists on disk | No, in either the worktree or the main checkout. Keys are read from the process environment or `.env` (`engine/groundtruth/places/credentials.py:14-15,42-45`); that module never prints them. |
| Env vars that carry secrets | `FOURSQUARE_API_KEY`, `YELP_API_KEY`, `YELP_CLIENT_ID` (optional), `GOOGLE_MAPS_API_KEY` (`engine/groundtruth/conf/providers.json`). Nothing else in `api/`, `engine/` or `scripts/` reads a `*KEY*`, `*TOKEN*`, `*SECRET*` or `*PASS*` variable. |
| Git history scan | All 75 commits on every branch and remote (`git log --all -p`, 74 MB of diff), scanned for 14 patterns: AWS `AKIA/ASIA…`, GitHub `gh?_…`, `sk-…`, `sk-ant-…`, Google `AIza…`, Slack `xox?-…`, HF `hf_…`, PEM private keys, `tskey-…`, JWTs, `fsq3…`, Azure `sig=`, `Bearer …`, and `api_key/secret/token/password = <16+ chars>`. **0 matches.** No `.env`, `*.pem`, `*.key`, `*secret*` or `*credential*` file has ever been committed. |
| Key leakage paths | `places/base.py:4-6,337` strips `key`/`api_key` from cache keys and never logs URLs or exception strings. **One residual risk** remains. Google Geocoding sends the key as a **query parameter** (`places/google.py:124`). httpx logs every request URL at INFO on logger `httpx`. No handler is configured today (no `basicConfig` in `api/`), so nothing is printed. If anyone adds INFO logging, the Google key would land in `data/logs/api.log`. Google is disabled by policy (`providers.json`). Fix S10. |

## Findings

| # | severity (tailnet-only) | finding | where |
|---|---|---|---|
| S1 | **High** | **No authentication on any route**, including the routes that write to disk, spend money or burn CPU/GPU. | all of `api/app.py`, `api/sessions.py`, `api/investigations_api.py`, `api/point_api.py`, `api/places_api.py` |
| S2 | **High** | **CORS `allow_origins=["*"]`, methods `*`, headers `*`.** Any website open in a tailnet user's browser can call the API cross-origin and read responses: drive exports, run scans, spend Places API quota up to the daily caps, poll investigations by id. No cookies are used, so this is not credential theft. It is cross-site request abuse into a private network (the browser bridges into the tailnet). | `api/app.py:28` |
| S3 | **Medium** | **Paid third-party calls are unauthenticated.** `POST /api/v1/places/search` triggers Foursquare/Yelp calls. Spend is bounded by the per-provider daily caps (300 requests / $5 and 200 requests / $3, `providers.json`), so the realistic impact is **quota exhaustion** (denial of service for the demo) rather than a large bill. Combined with S2, any web page can trigger it. | `api/places_api.py:29-33` |
| S4 | **Medium** | **Export writes files with no rate limit, size cap or cleanup.** Each call writes `data/out/exports/export_<ts>_<n>.<fmt>`, and the GeoJSON body is unbounded. Two calls in the same second with the same count **overwrite** each other, because the name is only a second-resolution timestamp plus the count. The response also echoes up to 2 MB of content. Disk-fill DoS. | `api/app.py:448-450`; `engine/groundtruth/tools.py:2210-2242` (name at `:2233`) |
| S5 | **Medium** | **Anonymous `X-GT-Owner` sessions.** The owner id is a client-chosen string (`^[A-Za-z0-9-]{8,64}$`, `sessions.py:27,65-68`) kept in localStorage, and anyone who knows or guesses it gets full read/write. It is a capability, not an identity. Acceptable for a demo, as documented in `sessions.py:3-6`. Related defects:<br>• `create_pin` does not check that `session_id` belongs to the owner, and `session_id` and `msg_id` have no length limit (`sessions.py:187-207`).<br>• `AreaIn.geometry` has no size limit (`sessions.py:153-173`).<br>• `investigations_api.create` stores `X-GT-Owner` unvalidated, truncated to 64 characters (`investigations_api.py:58`). | `api/sessions.py`, `api/investigations_api.py` |
| S6 | **Medium** | **Investigations have no owner check.** `GET /investigations/{id}`, `/cancel`, `/features/{fid}/evidence`, `/feedback` and `/report.md` work for anyone who has the id. Ids are `uuid4().hex[:12]`, 48 random bits (`investigate.py:457`), so they are hard to guess but leak through shared links and logs. `feedback` appends to `data/app/v1/feedback/feedback.jsonl` without limit. `list_mine` parses up to 400 JSON files per call. | `api/investigations_api.py:65-144` |
| S7 | **Medium** | **No request size limits.** Uvicorn and Starlette impose none. Unbounded fields include:<br>• `AgentIn.text` (`app.py:334`), which feeds the LLM prompt;<br>• `examples` and `exclude` lists (`app.py:100-121`);<br>• `GeoIn.geojson` (`app.py:148-149`);<br>• `point/find` `area.geojson` (`point_api.py:23`);<br>• `FindBuildingsIn.limit` up to 100,000 (`app.py:416`).<br>Investigation `context` is capped at 200 KB (`investigations_api.py:56`) and sessions at 2 MB (`sessions.py:28`). One worker (`--workers 1`) with synchronous heavy endpoints means a single large or slow request stalls the demo. | `api/app.py`, `api/point_api.py` |
| S8 | **Low** | **Path traversal checks.**<br>• `/api/v1/exports/{name}`: **safe**. `resolve()` plus `p.parent != d` (`app.py:453-459`).<br>• `/api/v1/vt/{layer}`: **safe in practice**. A path param cannot contain `/`, and `..` is rejected, but only *after* `p.exists()` is evaluated on the attacker-built path (`app.py:540`, an existence probe only). Prefer an allowlist from `conf/vector_tiles.json`.<br>• `/assets/{path}` and `/v2assets/{path}`: **prefix check flaw**. `str(p).startswith(str(root))` (`app.py:631, 646`) also accepts *sibling* directories whose names start with the same prefix, for example `GET /assets/..%2Fassets-old/x` → `web/dist/assets-old/x`. Exposure is limited to such siblings, and none exist today. Fix with `Path.is_relative_to`.<br>• Investigation store: `store_dir()/f"{inv_id}.json"` with an unvalidated id (`investigate.py:446`). A `/` cannot arrive through routing, so the id is limited to `*.json` names inside the store. Add a regex. | `api/app.py`, `engine/groundtruth/investigate.py` |
| S9 | Low | `.env.example` is ignored by `.env.*`, so the documented key names cannot be committed. | `.gitignore:22` |
| S10 | Low | The httpx INFO logger would expose the Google Geocoding `key=` query parameter if logging were ever enabled (see Secrets). | `engine/groundtruth/places/google.py:124` |
| S11 | Low | **Disk growth from caches with no eviction.**<br>• `/api/v1/crop` caches every distinct bbox (`app.py:566-593`); `year` is not validated against `imagery_years()`.<br>• `/api/v1/tiles/naip` caches every Planetary Computer tile at z16–18 when online (`app.py:506-526`). | `api/app.py` |
| S12 | Low | **Model revisions are not pinned** for SAM 2.1 (`outline.py:39-40`), Grounding DINO (`openvocab.py:51-52`) and OpenCLIP (`embed.py:125`), which is a supply-chain and reproducibility gap. Mitigated by `HF_HUB_OFFLINE=1` (`run_api.sh:18`), so nothing new is fetched at runtime. DINOv2 is pinned. | engine |
| S13 | Low | **Outdated native libraries in the API process.** The pyvalhalla wheel bundles OpenSSL **1.1.1k** (end of life) and libcurl **7.61.1** (`pyvalhalla.libs/`), loaded in-process for `isochrone`. Valhalla makes no network calls here, so this is exploitable only through crafted local tiles. Track it. | `docs/compliance/LICENSE_INVENTORY.md` F1/F10 |
| S14 | Info | **Label server.** It binds 127.0.0.1 and has no CORS middleware, so cross-origin JSON `POST`/`DELETE` fail preflight. It has no Host-header check, so DNS rebinding could reach it from a malicious page. The labels are "sacred". A `TrustedHostMiddleware(allowed_hosts=["localhost","127.0.0.1"])` would close this. **Do not restart the label server to apply it without telling the human** (CLAUDE.md). | `api/label_server.py` |

## People-data policy: order of checks

The policy (`engine/groundtruth/tools.py:34-56`, `_people`) uses whole-word terms for owners, residents, demographics,
crime, school ratings and "who lives/owns".

| entry point | where the check runs | before any model? |
|---|---|---|
| `/api/v1/agent/run` → `brain.chat` | `api/planner/brain.py:19-20`. People text is forced to the rules path. | **Yes.** The current text never reaches the LLM. |
| └ rules planner | `engine/groundtruth/planner.py:298` (decline), `:227` (no follow-up merge for people text) | Yes |
| `/api/v1/investigations` | `investigations_api.py:36-41` → `investigate.py:1048`, before `compile_plan`. Investigations do not call the LLM. | Yes |
| Tool endpoints | `search_text` `tools.py:348`, `pick_candidates` `:378`, `find_place` `:1919`, `find_anything` `:1971` | Yes (before CLIP/DINO) |
| **Gap: client `context.last_text`** | `brain.py:26,33` passes `context.get("last_text")` to `local.llm_plan` and `hybrid_plan` **without** a `_people` check. A client can put people-data text in `last_text` and it reaches the local LLM. The model runs locally and its output only becomes a plan, so the impact is limited, but this breaks the rule "people data never reaches the LLM". `INTELLIGENCE_AUDIT.md` §9 records the same gap. | **No.** Fix S15. |
| `/api/v1/places/search` | No `_people` check on `query` (`places_api.py:29-33`). The text goes to Foursquare/Yelp. Place search returns businesses, but "who owns …" style queries would be forwarded to third parties. | **No.** Fix S16. |

## Prioritised fixes

| prio | fix | effort |
|---|---|---|
| 1 | **S15.** Check `last_text` in `brain.chat` (patch A). | 5 min |
| 2 | **S2.** Restrict CORS to the app's own origins (patch B). The UI is same-origin (served by the API), so `*` is not needed. | 10 min |
| 3 | **S16.** Apply `_people` to `places/search` `query` (patch C). | 5 min |
| 4 | **S4 + S7.** Body-size middleware (patch D); a per-IP token bucket on `export`, `places/search`, `point/find`, `find_all` and `agent/run` (patch E); unique export names (`uuid4().hex[:8]`); daily cleanup of `data/out/exports` older than 7 days. | 1–2 h |
| 5 | **S1.** Switch the tailnet exposure to `tailscale serve --https=443 http://localhost:8000` and require the `Tailscale-User-Login` header on state-changing routes (patch F), or add a shared bearer token from `.env`. Also add a tailnet ACL that limits port 8000 to named users. | 1–2 h |
| 6 | **S8.** Use `is_relative_to` for assets (patch G); a vt allowlist; an investigation id regex. | 20 min |
| 7 | **S5/S6.** Owner checks on investigation read/cancel/feedback/report; `create_pin` verifies session ownership; `max_length` on `session_id`, `msg_id` and `AgentIn.text`. | 45 min |
| 8 | **S9.** Add `!.env.example` to `.gitignore` and commit an `.env.example` with empty values. | 5 min |
| 9 | **S10.** `logging.getLogger("httpx").setLevel(logging.WARNING)` at API start. | 2 min |
| 10 | **S12.** Pin `revision=` for SAM, Grounding DINO and OpenCLIP. | 15 min |
| 11 | **S11.** LRU or size caps on the crop and tile caches; validate `year`. | 30 min |
| 12 | **S13/F1.** Rebuild or replace Valhalla (see the licence inventory). | 0.5–1 day |
| 13 | **S14.** Add TrustedHost to the label server at the next approved restart. | 5 min, plus coordination |

### Suggested patches (not applied)

**A. `api/planner/brain.py`**: check the client-supplied context before the LLM.
```python
-    if _people(text) or EXPORT_RE.search(text) or os.environ.get("LOCAL_LLM", "1") != "1":
+    if _people(text) or _people(context.get("last_text") or "") or EXPORT_RE.search(text) \
+            or os.environ.get("LOCAL_LLM", "1") != "1":
```

**B. `api/app.py:28`**: same-origin plus tailnet host.
```python
-app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
+_ORIGINS = [o for o in os.environ.get("GROUNDTRUTH_CORS_ORIGINS",
+            "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173").split(",") if o]
+app.add_middleware(CORSMiddleware, allow_origins=_ORIGINS, allow_methods=["GET", "POST", "PATCH", "DELETE"],
+                   allow_headers=["Content-Type", "X-GT-Owner"])
```

**C. `api/places_api.py`**
```python
 def places_search(req: PlacesSearchRequest) -> dict[str, Any]:
+    from groundtruth.tools import _people
+    if _people(req.query):
+        from groundtruth.envelope import declined
+        return declined("places_search", "0.1.0", "Groundtruth does not search for information about people").model_dump()
```

**D. `api/app.py`**: request body cap (default 2 MB).
```python
+_MAX_BODY = int(os.environ.get("GROUNDTRUTH_MAX_BODY", 2_000_000))
+
+@app.middleware("http")
+async def body_limit(request, call_next):
+    cl = request.headers.get("content-length")
+    if request.method in ("POST", "PATCH", "PUT") and (cl is None or int(cl) > _MAX_BODY):
+        return JSONResponse({"detail": "request body too large or missing Content-Length"}, status_code=413)
+    return await call_next(request)
```
Rejecting a missing Content-Length also blocks chunked uploads. The browser `fetch` sends it for JSON bodies.

**E.** Rate limit: a simple in-process token bucket, with no new dependency.
```python
+_BUCKETS: dict[tuple[str, str], list[float]] = {}
+_LIMITS = {"/api/v1/tools/export": (10, 60), "/api/v1/places/search": (20, 60), "/api/v1/agent/run": (30, 60)}
+def _rate_ok(ip: str, path: str) -> bool:
+    n, win = _LIMITS.get(path, (0, 0))
+    if not n: return True
+    import time as _t
+    q = [t for t in _BUCKETS.get((ip, path), []) if _t.time() - t < win]
+    ok = len(q) < n
+    if ok: q.append(_t.time())
+    _BUCKETS[(ip, path)] = q
+    return ok
```
Call it from the existing `replay_middleware` and return 429 when it fails. Behind `tailscale serve`, every client
appears as 127.0.0.1, so in practice this is a global limit. That is fine for a demo.

**F.** Tailnet identity, after switching to HTTPS serve.
```python
+def _require_tailnet_user(request) -> None:
+    if os.environ.get("GROUNDTRUTH_REQUIRE_TS_USER") == "1" and not request.headers.get("Tailscale-User-Login"):
+        raise HTTPException(401, "tailnet identity required")
```

**G. `api/app.py:631,646`**
```python
-    if not str(p).startswith(str((_DIST / "assets").resolve())) or not p.exists():
+    if not p.is_relative_to((_DIST / "assets").resolve()) or not p.is_file():
```
Make the same change for `v2assets`. For the investigation store (`investigate.py:get`), add
`if not re.fullmatch(r"[0-9a-f]{12}", inv_id or ""): return None`.
