# Performance report (2026-11-08, PC: RTX 4060 8 GB, 32 GB RAM, WSL2; API warm)

All numbers are measured. "Before" is the start of this sprint (commit `1ce0989`); "after" is the end (`4845780`+).

| Measure | Before | After | Source |
|---|---|---|---|
| Flagship investigation, total (UI, warm) | 0.9–2.0 s | 1.7–2.5 s | demo_check_v3 at 3 sizes |
| Flagship, first result (first find step done) | 0.4–0.8 s | 0.4–1.6 s | demo_check_v3 |
| Follow-up ("which of those …") | 0.34–0.43 s | 0.34–0.39 s | demo_check_v3 |
| Plan compilation (incl. geography resolution) | < 5 ms | 178 ms (flagship) | perf probe |
| Geography resolution per query | — | 3–10 ms (a 5-mile lake radius takes 229 ms; first call 642 ms loads the gazetteer) | perf probe |
| Find like this: warm, calibrated | 0.2–0.5 s | 0.13–0.20 s | eval_point_find |
| Find like this: cold first scan, 100 km² area | 21 s | not re-measured (unchanged code path) | — |
| Investigation benchmark (12 objectives × 2) | 12/12 status | 12/12 | bench_investigations |
| API resident memory | 6.25 GB | 6.9 GB (anon 3.7 GB; file-backed index pages 3.1 GB) | GET /api/v1/runtime |
| GPU memory (process) | 2.05 GB allocated / 3.3 GB reserved | same | /api/v1/runtime |
| Windows free RAM, API warm | 2.4 GB | 4.4 GB (meets the ≥ 4 GB rule; WSL page cache varies) | Win32_OperatingSystem |
| API warm-up after restart | ~3–4 min | ~3–4 min (unchanged) | health poll |

## Where the added flagship latency comes from

- The geography resolver runs on every objective. It costs 5–200 ms, plus 0.6 s the first time it loads the gazetteer.
- The coverage check now also measures exact lidar extents.
- Both are bounded and deterministic. A cached gazetteer, already in place, makes later calls cheap.

## Known bottlenecks

| Bottleneck | Detail | Next step |
|---|---|---|
| Cold scans | The first `find_all` in a large area takes 3–21 s while index pages fault in. | Pre-warm per place (already done for pools in the 8 demo places). |
| Warm-up | About 3–4 min after a restart. | Unchanged. |
| Measuring all 946 homes | Flood + elevation + shore take 10.4 s cold, 4.4 s warm (lidar windowed reads). | Batch reads. |
| Python step timeouts | They stop the investigation waiting but can't kill the running tool. | — |

## Memory monitoring

`GET /api/v1/runtime` reports process RSS (anon/file), system MemAvailable and the process's GPU memory. It never initialises CUDA itself.

## Cold start (Red Elimination Sprint 2, 2026-11-08)
Measured on the PC, same data and code apart from the warm-up order, with `scripts/cold_start_profile.sh blocking`
(the old order) and `scripts/cold_start_profile.sh` (the new one). One run each, so expect a few seconds of noise.
The per-phase profile is in `/api/v1/health` → `warmup.phases`.

| Phase (ms) | Old order | New order |
|---|---|---|
| engine init | 263 | 216 |
| models + pick candidates (DINOv2/CLIP load, first embed) | 10,786 | 19,951 |
| find_all in the warm-up place (index paging) | 26,987 | 22,825 |
| refine | 526 | 679 |
| flagship investigation (new: derived set, FEMA, lidar, geography) | 2,199 | 1,874 |
| LLM ping (laptop) | 1,352 | 1,305 |
| chat chain, 11 conversational turns | **206,594 before healthy** | after healthy, in the background, yielding to requests |
| **restart → healthy** | **274.8 s** | **51.8 s** |
| first flagship investigation after healthy | 2,301 | 1,103 |
| first plain chat search ("swimming pools") after healthy | 57 | 1,294 |

What changed: the conversational chat chain used to warm before `/health` turned healthy, and it took 207 s of
the 249 s warm-up. It now runs after healthy, in the background, and yields to real requests. The flagship
investigation is warmed before healthy instead. Trade-off: a plain chat search in the first ~3½ minutes can take
~1.3 s instead of ~0.06 s. `GROUNDTRUTH_WARMUP_CHAIN_BLOCKING=1` restores the old order. The three restarts after
this change reached healthy in 44.0, 46.6 and 41.9 s.

Remaining floor: of the 51.8 s, 46.9 s is the in-process warm-up, mostly model load + first embed (~10–20 s) and index paging for the first scan (~23–27 s); process start is the other ~5 s.
