# Demo day: Groundtruth Intelligence (branch `intelligence/evidence-grounded-investigation`)

The section below the line is the earlier demo-final script. It still works at `/classic` and on the `demo-final` tag.

## Setup (PC; about 4 minutes)

1. **Check memory.** Windows needs ≥ 4 GB free with the API warm. It was 2.4 GB on 2026-11-08; close other apps first.
2. **Start the API** from the worktree: `bash scripts/api_branch.sh`. Wait until `/api/v1/health` shows `"warming": false` (about 3–4 minutes).
3. **Optional: the local LLM.** It runs on the laptop through the tunnel. **Investigations do not need it**: the plan compiler is rules-based and deterministic. Plain chat searches use it when it's reachable.
4. **Check:** `~/.venvs/gt-browser/bin/python scripts/demo_check_v3.py` should print 17/17.
5. **Open** http://localhost:8000/ (or the tailnet URL; `tailscale serve`, never Funnel).

**Data the demo needs** (all local):
- NAIP 2022 COGs.
- The v3c8p192 county index.
- `data/derived/v1/Lake_Norman`.
- `data/risk/v1` (FEMA NFHL + shoreline).
- `data/lidar/v2/Lake_Norman`.
- The local tile cache. Optionally pre-render it with `scripts/prerender_tiles.py`.

## The 3-minute script

**1. Landing.** Say: "Ask a question about the real world; Groundtruth investigates the map and shows its evidence." Click the card **Flood exposure of homes with pools**.

**2. The plan runs.** The plan appears with live steps: Resolve the area → Check what data covers it → Find homes with pools → Keep the ones in a FEMA 100-year flood zone → Measure each result → Order the results → Weigh the evidence. Each step shows its real time. Total is about 1–1.7 s.

**3. Read the findings.**
- **"20 of 946 touch a FEMA 100-year flood zone."** Open it. The chain is pools detected in NAIP imagery → assigned to buildings (Overture) → FEMA intersection.
  - It is graded **weak**, and that's deliberate: the homes-with-pools detector isn't re-measured for this data version. The FEMA intersection itself is strong.
- **"Height above the base flood elevation is known for 13 of 20."** This is lidar ground minus FEMA's BFE.
- **The gap: "7 of 20 have no published base flood elevation."** Say: "It tells you what it can't establish."
- **Cross-check:** lidar 2016–19 vs imagery 2022 vs FEMA dates.

**4. Evidence.** Click an evidence row to open the inspector: source, date, licence, method, measurements, confidence, limits. Then click **Show these 20 on the map**.

**5. A result.** Click a thumbnail. The sheet shows:
- the WHY line, e.g. "Pool 127 m² · Zone AE · 44 m from the shore · 0.1 m below the base flood elevation";
- the 2020/2022 swipe;
- the evidence for this result and what is missing;
- **Confirm**, which is stored as human evidence.

**6. Steer it.** Click the pill **which of those are less than 1 m above the flood elevation**. The result is 12 of 20 in about 0.4 s. It starts from the previous result.

**7. Report.** Click **Report** to download the Markdown: findings with evidence ids, steps, sources, limitations, cross-checks, reproducibility.

**8. Boundaries.**
- Type **who owns these homes**: it is declined before any model runs.
- Optional: **Discover → Click anything to find all like it**. Click a pool and the calibrated result is about 23/24 pools by eye.

**9. Persistence.** Reload. The sidebar shows the investigation as **Completed**, and `/s/<id>` restores it.

## What not to claim

- "Found every pool": the scan is exhaustive over the imagery, but recall is not guaranteed.
- "Flood risk score": there is none; the results are ordered by documented facts.
- County-wide flood heights: FEMA publishes a BFE for only 10 of 8,761 zones.
- Change monitoring: not built.
- The 2020 imagery outside the demo areas: 14 scenes are not cached.

## Recovery

| Problem | Fix |
|---|---|
| API cold or slow | Wait for `"warming": false`, then re-run `demo_check_v3`. |
| LLM unreachable | Nothing changes for investigations; chat searches fall back to rules. |
| Tiles slow | Run `scripts/prerender_tiles.py` (it writes `data/cache/tiles/local/v1`). |
| Stuck investigation | The ■ button cancels; the step record shows what was skipped. |

---

# Demo day (one page)

The demo runs on this PC (`oryn`): API + UI on `127.0.0.1:8000` inside WSL, the label server on 8765 (not needed).
Code + data are frozen at the `demo-final` tag (`data/DEMO_MANIFEST.json`).

## Before you leave home
1. `wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine && bash scripts/rollback_demo.sh"`
   → checks out demo-final, pins its data versions, restarts with the full warm-up (~6 min), runs demo_check.
   It must end with `RESULT: PASS`.
2. Leave the PC on, awake, and on the tailnet.

## Enable access from the presenting laptop (tailnet only, one port, no Funnel)
1. On this PC: `bash scripts/tailnet_serve.sh on --i-am-monish-and-approve`  (inside WSL, repo root)
2. `bash scripts/tailnet_serve.sh status` shows `tcp:8000 → localhost:8000`.
3. On the presenting laptop (logged into the same tailnet): open **http://oryn:8000/** in the browser.
   - Test: the landing page loads, the imagery shows, click "find swimming pools", then type the chain below.
   - If `oryn` doesn't resolve, use the PC's tailnet IP: **http://100.116.119.48:8000/**.

## The chain to type (each < 2 s warm)
"find swimming pools" → pick *Backyard pool* → choose *Ballantyne* → Find all (dense) → ✓/✗ three results →
"pools within 1 km of I-485" → "houses within 10 minutes of Ardrey Kell High School" → "buildings taller than 30 m in
Uptown" → "boat docks on Lake Norman" → "new pools since 2020 in Ballantyne" → "export them as CSV" →
"who owns these pools" (declined, on purpose).

## Disable afterwards
`bash scripts/tailnet_serve.sh off --i-am-monish-and-approve`, then `bash scripts/tailnet_serve.sh status` (empty).

## Fallbacks
- **Network trouble:** present on this PC itself: open http://localhost:8000/ in its browser (no tailnet needed).
- **Planetary Computer down / no internet:** the imagery is local (NAIP 2022 county + 2020 for the 8 places). Restart
  with `GROUNDTRUTH_OFFLINE=1 bash scripts/restart_and_warm.sh` to block every outbound imagery fetch.
- **Something broke:** `bash scripts/rollback_demo.sh` (above). Last resort: restart with `GROUNDTRUTH_REPLAY=1`
  (recorded responses, marked `[REPLAY]` in the UI work log).
- **Never** run `tailscale funnel`.
