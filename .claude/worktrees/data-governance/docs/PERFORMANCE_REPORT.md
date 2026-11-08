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
