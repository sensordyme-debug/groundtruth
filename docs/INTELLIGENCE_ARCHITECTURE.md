# Intelligence architecture: investigations

**Goal.** An objective becomes a typed, validated, multi-step plan. Real tools execute it in dependency order, and the result is graded findings that are each linked to evidence, together with the gaps and cross-checks. The engine computes; no model invents a number.

Code:
- `engine/groundtruth/investigate.py` (engine)
- `engine/groundtruth/conf/investigate.json` (vocabulary, ranking rule, detector quality, strength rubric, limits)
- `api/investigations_api.py` (HTTP)
- `web/app-v2/src/Investigation.tsx` + `App.tsx` (UI)
- `tests/test_investigate.py`

```
objective ─► policy (people / owners / residents: whole words) ─► declined, before any model or tool
          └► compile_plan(objective, context, previous)
               uses planner.parse()          (the existing rules planner: target, place, layers)  ← no second planner
               + constraint words (conf)     (flood 100/500-yr, < / > N m above BFE, within N m of shore, canopy,
                                              taller than, more than N minutes from …, rank)
               → InvestigationPlan (pydantic)  validated BEFORE execution:
                   ops ∈ OPS registry · each op's args validated by its own model · dependencies point backwards ·
                   filter / explain / rank / compare need an input step · max 12 steps · filter keys allow-listed
               → or a question ("Where should I look?") · or "chat" (plain searches keep the existing chat turn)
          └► run(): steps in order; a failed / declined step BLOCKS its dependents; independent steps still run
               each step: queued → running → done | failed | blocked | skipped, with ms and a plain-words detail
               events appended as tools finish; the API is polled (no simulated streaming)
          └► evidence ledger (typed Evidence items, one or more per step)
          └► conclude(): findings graded by their WEAKEST supporting evidence; gaps; cross-checks
          └► report (Markdown), per-result evidence, human verdicts (stored as evidence + a versioned journal)
```

## Operations (the only things a plan can do)

| op | engine call | evidence it produces |
|---|---|---|
| `resolve_area` | `place_bbox` / drawn polygon | scope (area km², source) |
| `check_coverage` | data registry + provider scenes + lidar extents + derived folders + flood availability | coverage (what exists here; gaps become findings) |
| `find_derived` | `Engine.find_derived` (homes with pools → lakefront → own dock) | detection (pools in imagery) + association (pool → building) |
| `find_concept` | `pick_candidates` + `find_all` | detection |
| `find_like` | `find_all` with the user's examples | detection |
| `find_buildings` / `find_places` | Overture | detection (mapped, not seen) |
| `filter` | `Engine.filter` (risk predicates, layers, travel time) | spatial_intersection or measurement (kept N of M, unmeasured counted) |
| `explain` | `Engine.explain` (WHY line per result + insights) | measurement (BFE known for N of M; shore distance) |
| `compare_years` | `Engine.compare_years` | temporal (new / existing / unsure; "unsure" never counted as new) |
| `rank` | documented stable sort, **not a score** | ordering |
| `conclude` | — | findings |

The LLM never produces SQL, Python, tool names or results. It can only influence `planner.parse`-level fields through the existing brain. The investigation compiler is deterministic for identical inputs and data versions; the benchmark checks this.

## Evidence and confidence semantics

Each **Evidence** item carries: id, step_id, kind (scope | coverage | detection | association | spatial_intersection | measurement | temporal | ordering | human), statement, source {name, url, date, licence}, method, measurement (units in the keys), feature_ids, strength, confidence_note, limitations.

**Strength** (`conf/investigate.json` → `strength`):

| Strength | Applies to |
|---|---|
| strong | computed from a published layer (FEMA intersection, OSM shore distance) or a deterministic ordering; human verdicts |
| moderate | a measured source with stated limits (lidar heights), or a detector whose measured precision is ≥ 0.8 |
| weak | a detector whose precision is not measured for this data version |
| insufficient | the input data is missing |

How it is used:
- **A finding's strength is the minimum over its supporting chain.** Example: "20 of 946 touch a FEMA 100-year flood zone" is weak because the homes-with-pools set it depends on hasn't been re-measured for v1. The FEMA intersection itself is strong.
- **Raw model scores are never shown as probabilities.** Similarities are labelled uncalibrated.
- **Findings have a nature:** computed, model prediction, observed, inference or gap. Gaps are first-class: "7 of 20 have no published base flood elevation…", "lidar heights are built for 6 areas; not this one".
- **Cross-checks** are recorded, never silently resolved. Current checks:
  - objects outside FEMA's mapped area (zone unknown, not "none");
  - "new" objects that look present in both years;
  - date mismatch: lidar 2016–19, imagery 2022, FEMA effective maps.

## Follow-ups and memory

- A follow-up ("which of those …") compiles into a plan whose input is the previous investigation's final set (`context.previous`).
- Investigations persist as JSON in `data/app/v1/investigations/`, with plan, steps, events, evidence, findings, the final feature set, and data/engine versions.
- Chat sessions (`api/sessions.py`) store the investigation ids, so `/s/<id>` restores them from the server record without re-running.
- Human verdicts go to the investigation (as human evidence) and to `data/app/v1/feedback/feedback.jsonl`, keyed by investigation, concept, scope and versions. `data/labels/` is never touched.

## Execution choices

- **Sequential execution.** Steps run one at a time. The engine shares one GPU, a memory-mapped index and unlocked caches, so parallel model calls would only contend; the audit found shared state without locks. Independent cheap steps (resolve, coverage) cost under 100 ms anyway.
- **Cancellation** is cooperative: the running step finishes and later steps are marked skipped.
- **Timeouts:**
  - Engine tools are bounded by their own limits (scan area, `k`, outline queue).
  - `conf.limits.step_timeout_s` is recorded but **not enforced preemptively**, because Python threads can't be killed safely. This is a known gap.

## The workspace UI (web/app-v2)

- **Layout:** rail (Home, Investigations, Discover, Reports, Saved, Settings) | investigation sidebar | conversation | map.
  - The sidebar has search, date groups (Today / Yesterday / Previous 7 days / Older) and real status (Running, Completed, Needs review, Failed).
  - There is no "Monitor" item: no scheduler exists, and the product shows no dead buttons.
- **An investigation in the chat:**
  1. What it understood: area, target, conditions, and data available here.
  2. The plan, with live step states from the polled record.
  3. Summary.
  4. Findings, each with three strength bars. Expanding a finding shows its evidence rows; each row opens the evidence inspector with source, method, measurements, confidence and limits, and "Show on map".
  5. Cross-checks.
  6. Next steps grounded in the data (e.g. offered only when elevation data exists).
  7. The report download.
- **Map ↔ chat:** "Show these N on the map" highlights a finding's features and fades the rest. Clicking a map result opens its sheet with the WHY line, its evidence, what is missing, and Confirm / Reject (stored as evidence).
- **Responsive:** the rail stays; below 900 px the sidebar becomes a drawer; the chat and map stack on phones (`app.css` media rules).

## Inspiration note

The brief asked for an interface at the level of polish of Devin's agent workspace. I could only study devin.ai's public marketing page, which shows no product UI, and could not access app.devin.ai (it needs a login).

The design is therefore original. It follows agent-workspace principles: a persistent session list, a visible plan with honest step states, and evidence one click away. It uses Groundtruth's approved tokens and copies no branding, assets or text.
