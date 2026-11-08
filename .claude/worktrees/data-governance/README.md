# Groundtruth

**Teach the map anything** — for Mecklenburg County, North Carolina.

Type what you're looking for in aerial imagery ("swimming pools"). Groundtruth shows 6 candidate image tiles that
cover the different things you might mean (a backyard pool, a community pool, a stormwater pond, a blue tarp).
You pick what you meant and mark "not this". Groundtruth then finds, outlines, counts and measures every match
across the county, and refines live as you accept and reject results. Later, an AI agent acts on the map
(add layers, filter, measure, export).

Built at **CrownHacks** (UNC Charlotte). Scope: Mecklenburg County only. Licence: MIT.

## Repo
| path | what |
|---|---|
| `engine/groundtruth` | the product: a typed, tested Python engine. Every tool returns one envelope (`docs/CONTRACT.md`) |
| `api/` | FastAPI adapters: `label_server.py` (labelling tool), `app.py` (engine tools) |
| `web/` | thin demo UI (Vite + React + TS + MapLibre); `web/label/` is the labelling page |
| `scripts/` | data fetch, precompute, evaluation |
| `tests/` | pytest |
| `docs/` | CONTRACT, SOURCES (every dataset/model + licence), ARCHITECTURE, BUILD_LOG, EVALUATION |

## Run it (Linux / WSL)
```bash
uv venv ~/.venvs/groundtruth --python 3.12 && source ~/.venvs/groundtruth/bin/activate
uv pip install -e ".[api,dev]"           # add ".[ml]" for embeddings
python scripts/fetch_boundary_naip.py     # county boundary + NAIP coverage
bash scripts/run_label_server.sh          # → http://localhost:8765/label
pytest -q
```

### The demo (PC, WSL)
```bash
bash scripts/restart_and_warm.sh   # API + UI on http://127.0.0.1:8000/ (healthy after ~2 min of warm-up)
bash scripts/demo_check.sh         # manifest + hero flow + typed chain + latency budgets → RESULT: PASS
bash scripts/rollback_demo.sh      # back to the frozen demo-final (code + data versions), then demo_check
```
Start with **`docs/HANDOFF.md`** (what it does, how to run the PC + laptop stack, numbers, weaknesses) and
`docs/DEMO_DAY.md` (tailnet access, fallbacks). Upgrades happen on branches; `demo-final` is never modified.

### Labelling page
Keys: `1`–`5` concept · `T` true match · `L` look-alike · `U` undo · `Shift`+click drops the other kind · `/` search.
Each click is saved straight to `data/labels/` (append-only journal, atomic GeoJSON snapshot and a backup).
There is no save button. Unsaved clicks are kept in the browser and retried if the server can't be reached.

## How we built it
- **Engine first.** Data access sits behind a `DataProvider` interface, and every tool returns one
  envelope (`status`, `confidence`, `method`, `assumptions`, `limits`, `sources`, timed `steps`), so the UI and the
  agent consume one shape and every answer carries its provenance.
- **Real public data.** USDA NAIP 0.6 m imagery through Microsoft Planetary Computer, a Census county boundary,
  OpenStreetMap, FEMA NFHL and Overture Maps. Licences are recorded in `docs/SOURCES.md`.
- **Embeddings rather than per-concept training.** Image chips get DINOv2 (image↔image) and OpenCLIP (text↔image)
  vectors in FAISS. Text search, the 6 diverse "pick what you meant" candidates and find-similar are all
  vector operations, so new concepts need no retraining.
- **Human-made test set.** We built the labelling tool first and labelled while the engine was being built.
  `scripts/evaluate.py` reports precision@k against those labels.
- The build history is in `docs/BUILD_LOG.md` and in the commit log.

## Disclosure
Monish also builds ORYN, a geospatial platform; Groundtruth was built new at CrownHacks and will be integrated into ORYN afterwards.
Groundtruth reuses pre-existing data caches and helper code from ORYN, Monish's geospatial platform (listed in docs/PRIOR_WORK.md). The engine, pick-what-you-meant flow, live learning, labelling tool and agent were built at CrownHacks.
