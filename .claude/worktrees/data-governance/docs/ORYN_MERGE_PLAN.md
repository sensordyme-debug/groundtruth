# Plan: bringing Groundtruth into ORYN after the hackathon

Plan only. This is written from Groundtruth's own interfaces and the read-only inventory in `docs/ORYN_INVENTORY.md`; no ORYN
code was read or changed. Everything below is a proposal for Monish to accept or edit.

## Principles
1. **Groundtruth stays a library.** `engine/groundtruth` is imported by ORYN as a versioned package (`groundtruth==0.x`),
   not copied in. The HTTP API (`api/app.py`) stays optional: ORYN can mount the router or call the engine in-process.
2. **The seams are the two interfaces we built for this:** `DataProvider` (data in) and `Envelope` (answers out).
3. **No ORYN product code moves into Groundtruth,** and no Groundtruth code depends on ORYN.

## 1. DataProvider → `OrynDataProvider`
Groundtruth reads everything through `engine/groundtruth/providers/base.py`. ORYN implements one class:

| method | ORYN source (from the inventory) | notes |
|---|---|---|
| `get_boundary()` | ORYN's admin boundaries (any county, not only Mecklenburg) | turns Groundtruth multi-county |
| `imagery_years()`, `list_scenes()` | ORYN's NAIP catalogue / `naip-pc` gate | keeps ORYN's licence gating (e.g. the PC "preview" wording) |
| `read_grid()` / `get_imagery()` | ORYN's COG readers or the existing Planetary Computer path | same EPSG:32617 grid contract |
| `get_tiles()` | ORYN's tile server (TiTiler) | the UI already takes a tile URL template |
| `get_layer()` | ORYN reference layers (OSM, FEMA NFHL, flood cache) | layer names map 1:1; licence-blocked layers (Meck GIS) stay blocked by ORYN's flags |
| `sources()` | ORYN's DATA_SOURCES registry | envelopes then cite ORYN's canonical source entries |

`PublicDataProvider` stays as the open-data fallback and test double.

## 2. Indexes
- Keep Groundtruth's precompute format (v3: DINOv2 8×8 cells + PCA-192, whole-chip OpenCLIP, fp16) as a **separate
  index family** next to ORYN's `imagery_tiles` (DINOv2 CLS on a 26917 no-overlap grid). They answer different questions
  (object-level vs. scene-level) and should not be merged blindly.
- Storage: move from local `.npy`/FAISS files to ORYN's storage. Two options to decide:
  - (a) object storage + FAISS loaded per county (simplest, matches today);
  - (b) pgvector `halfvec(192)` rows per cell (~6.9 M rows per county-year). This needs a benchmark first: cell search is ~150 ms
    with FAISS today.
- Precompute runs as an ORYN batch job per county-year, with the scripts already resumable at scene granularity.

## 3. Tools → ORYN routes
- Each tool returns an `Envelope`; ORYN wraps it in its own auth and tenancy at the route layer only. The engine never sees
  tenant or user data. That also keeps the "no data about people" rule enforceable.
- Contract: publish `docs/CONTRACT.md` (v0.3.1+) as ORYN's internal API spec; fixtures become contract tests in ORYN CI.
- Sessions (`refine`) move from in-process memory to ORYN's session store (keyed by session id; holds accepted/rejected
  example ids only).

## 4. MCP + agent
- The MCP server and agent (Donovan's branch, not merged here) target the HTTP contract, so they work against ORYN's
  routes unchanged once the base URL points at ORYN.
- The map-action schema (`fly_to`, `add_results`, `set_filter`, …, citing `step_N` envelopes) becomes ORYN's agent→map
  protocol; the Groundtruth UI's action adapter (`web/src/agent.ts`) is the reference implementation.

## 5. UI
- Groundtruth's demo UI already uses ORYN's design tokens (`web/shared/tokens.css`). Port its three screens (ask → pick
  what you meant → results with live refine) into ORYN's web app as a feature module, reusing ORYN's map component.

## 6. Licences and provenance
- Every Groundtruth dependency is MIT/BSD/Apache (`docs/SOURCES.md`); models are Apache-2.0 / MIT. ORYN's
  THIRD_PARTY_NOTICES gets the model entries (DINOv2, OpenCLIP laion2b, SAM 2.1), which the inventory found missing.
- Envelope `sources` should resolve to ORYN's registry IDs so citations stay consistent.

## 7. Order of work (proposed)
1. Package `groundtruth` (pyproject already exists) and pin it in ORYN.
2. `OrynDataProvider` + contract tests from `docs/fixtures/`.
3. Precompute job for Mecklenburg 2022/2020 under ORYN storage; compare with today's results using `scripts/evaluate.py`.
4. Mount the tool routes behind ORYN auth; move refine sessions to ORYN's store.
5. Port the UI flow; wire the agent to the map-action schema.
6. Extend to more counties via `get_boundary` + per-county indexes.

## Open questions for Monish
- Storage choice for cell vectors (FAISS files vs pgvector halfvec): benchmark first?
- Should ORYN's existing change-detection candidates and Groundtruth's `compare_years` be unified or kept separate?
  (They use different grids and signals.)
- Multi-county: one index per county-year (simple) or a state-level index?
