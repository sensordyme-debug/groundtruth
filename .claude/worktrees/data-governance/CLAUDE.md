# Groundtruth — rules for working in this repo

"Teach the map anything" for Mecklenburg County, NC. Built at CrownHacks (UNC Charlotte).

## Product rules
- **The engine is the product; the UI is a thin demo.** Engine code (`engine/groundtruth`) is typed, tested,
  deterministic (seeded, stable sort orders) and versioned (each tool has a `version`).
- **Every tool returns one envelope** (see `docs/CONTRACT.md`, `engine/groundtruth/envelope.py`):
  `{tool, version, status: ok|partial|declined, result: {geojson, metrics}, confidence, method, assumptions,
  limits, sources: [{name, url, date, licence}], steps: [{phase, detail, ms}], declined_reason}`.
- **All data access goes through a `DataProvider`** (`get_imagery`, `get_tiles`, `get_layer(name, bbox)`, ...).
  Never fetch data inside engine logic. First implementation: `PublicDataProvider`. ORYN adds its own later.
- **Real public data only.** Record every source and licence in `docs/SOURCES.md`.
  Dependencies: MIT, BSD, Apache-2.0 (and similarly permissive) only. No AGPL. No non-commercial models or datasets.
- **No data about people**: no owner names, demographics, school ratings or crime.
- **Units in field names** (`area_acres`, `dist_m`). GeoJSON in EPSG:4326; measure in EPSG:32617 (UTM 17N).
- **Device auto-select**: cuda, then mps, then cpu (`groundtruth.device.pick_device`).
  Do not use the production PC's GPU for Groundtruth compute (set `GROUNDTRUTH_DEVICE=cpu` there).
- **Never handle or print API keys.** If something needs a key or login, ask the human.
- Commit small and often, push after each working step, and log each milestone with a timestamp in
  `docs/BUILD_LOG.md`.

## The demo is frozen
- **Upgrades happen on branches.** The `demo-final` tag and the data it uses (listed with checksums in
  `data/DEMO_MANIFEST.json`) are never modified or deleted. Rebuilt data goes to a new `data/<kind>/vN/` folder and is
  switched in `engine/groundtruth/conf/data_versions.json` only on a branch, after A/B + `scripts/demo_check.sh`.
- `scripts/rollback_demo.sh` restores demo-final (code + data versions) and runs demo_check.
- **Only ONE Claude Code session on this PC at a time.**
- Heavy CPU builds run on the laptop (`scripts/heavy/`); the PC keeps the API, UI, label server and GPU work (one GPU
  job at a time).

## Labelling data is sacred
- `data/labels/` is the human-made test set. Never auto-label it, never rewrite it from code other than the
  label store, never delete it. The label store writes a journal + atomic snapshot + backup on every change.
- Do not restart or kill the running labelling server without telling the human first, and only between clicks.

## ORYN
- ORYN (`/home/orynt/oryn`, WSL) is read-only. Reused assets are listed in `docs/PRIOR_WORK.md`;
  copied files are headed "Adapted from ORYN (pre-existing work by Monish)". Never copy ORYN's core product
  code, secrets, or anything about people.

## Layout
- `engine/groundtruth/` the Python package (the product)
- `api/` FastAPI exposing engine tools + the labelling page
- `web/` Vite + React + TypeScript + MapLibre demo (thin)
- `data/` gitignored local data
- `scripts/` fetch / precompute / evaluate
- `tests/` pytest
- `docs/` CONTRACT, SOURCES, ARCHITECTURE, BUILD_LOG, EVALUATION, PRIOR_WORK

## Running (WSL)
- Python env: `~/.venvs/groundtruth` (uv). `source ~/.venvs/groundtruth/bin/activate`
- Labelling server: `scripts/run_label_server.sh` → http://localhost:8765/label
- Tests: `pytest -q`
