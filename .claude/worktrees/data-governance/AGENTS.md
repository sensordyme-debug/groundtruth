# AGENTS.md: rules for coding agents (Codex, Claude, etc.)

Read `CLAUDE.md` for the project rules (envelope, DataProvider, licences, no people data, units in field names).

## Ownership
| owner | paths | notes |
|---|---|---|
| **Donovan** | `mcp/`, `agent/`, `tests/mcp/`, `tests/agent/`, `docs/REQUESTS.md`, `docs/DONOVAN_*.md` | MCP server + AI agent that act on the map through the API |
| Monish / engine | everything else (`engine/`, `api/`, `web/`, `scripts/`, `tests/` except the two above, `docs/` except the above) | engine, API, UI, data |

## Rules for Donovan's agent work
- **Never push to `main`.** Work on your branch (`donovan/mcp-agent`) and open a PR.
- **Never edit files outside your paths.** Need an engine/API change? Write it in `docs/REQUESTS.md`
  (what, why, example request/response) and it will be done on the engine side.
- Talk to the engine **only through the HTTP API** in `docs/CONTRACT.md`. Don't import `engine/` directly.
  Use `docs/fixtures/*.json` (one real request/response per tool, including `declined.json`) for offline tests.
- Treat every response as an envelope: check `status` (`ok | partial | declined`), show `limits`, and surface
  `steps` in logs. `declined_reason == "not_implemented"` means the route exists but the tool is not built yet.
- Never handle or commit API keys, tokens or `.env*` files. Never send data about people.
- Keep updates current with `git fetch origin && git merge origin/main`.

## Run the API locally (slice bundle, no county data needed)
The release asset `groundtruth-slices-chips224s192-g0.6-v3c8p192.tar.gz` (40.5 MB, on tag `contract-v0.2.1`) holds
two indexed slices (UNC Charlotte 342 chips, south Charlotte 272 chips, NAIP 2022) plus the county boundary and
FEMA/OSM layers clipped to them. It contains public/open data only; licences are in `BUNDLE_SOURCES.md` inside it.
SHA-256 `01c3841d0bcc16ea4b14a6d988cde34ecc655f485f0f31dc600b0fcd89093005`.

**macOS / Linux** (Python 3.11+, from the repo root):
```bash
make api-slice            # venv (CPU torch) + download + verify + unpack bundle + API on http://127.0.0.1:8000
curl -s http://127.0.0.1:8000/api/v1/health
```

**Windows (PowerShell)**, from the repo root:
```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\pip install -e ".[api,ml,dev]"
$b = "groundtruth-slices-chips224s192-g0.6-v3c8p192.tar.gz"
curl.exe -fL -o $b "https://github.com/sensordyme-debug/groundtruth/releases/download/contract-v0.2.1/$b"
(Get-FileHash $b -Algorithm SHA256).Hash   # must equal 01C3841D0BCC16EA4B14A6D988CDE34ECC655F485F0F31DC600B0FCD89093005
tar -xzf $b
$env:PYTHONPATH = "engine;."; .venv\Scripts\uvicorn api.app:app --host 127.0.0.1 --port 8000
```
First calls download public model weights from Hugging Face once: OpenCLIP (~600 MB) for `search_text`/`pick_candidates`
and SAM 2.1 tiny (~150 MB) for `outline`. Use `"aoi": "south_charlotte"` or `"unc_charlotte"` in tool bodies (both are
also the defaults). On CPU, `pick_candidates` takes ~10–15 s and `outline` ~1.2 s per new polygon.
