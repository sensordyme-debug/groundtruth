# Mockup vs build: visible differences (Checkpoint A)

Screenshots: `<state>_mockup_<size>.png` vs `<state>_build_<size>.png` at 1440×900 and 1920×1080.
States: landing, transition, step1–step4 (the Lake Norman chain), sheet (detail sheet, swipe at 20 %), pick (pick grid).
The mockup side is `scripts/mockup_shots.py`; the build side is `scripts/demo_check_v2.py --shots` (31/31 checks pass
at both sizes). Run both with `scripts/compare_shots.sh`.

## Fixed during the compare
| # | State | Difference | Fix |
|---|---|---|---|
| 1 | step 2–4 | Pool dots/rings missing (only docks drawn) | MapLibre needs zoom interpolation outermost; rewrote the dot/ring opacity expressions |
| 2 | step 2 | Build fitted all 946 houses (dots only); mockup flies close and shows rings | Camera flies to the densest cluster of the matches at z14.3 (z14.6 on a filter), so rings show |
| 3 | step 1 | "Worked 78.7 s" on an answer served from cache (the original cold time) | Cached answers show the time they took now |
| 4 | step 1 | First answer took 27 s in the browser (API: 35 ms): the hidden landing map's tiles held the browser's 6 connections | Imagery starts only after the first question is sent; local tiles up to z15 disk-cached; county z10–12 + Lake Norman z13–15 pre-rendered |
| 5 | landing | 2 px focus outline on the textarea | The composer card is the focus target; no outline on the bare textarea |
| 6 | landing | Background crop was mostly roofs; mockup shows houses + shoreline | Real NAIP crop of a Lake Norman shore (homes, docks, lake on the right) |

## Remaining differences (intended or data-driven)
| # | State | Difference | Why |
|---|---|---|---|
| 1 | all map states | Real NAIP imagery instead of the illustrated map; outside the county there is no imagery (flat green) | Intended: real data |
| 2 | step 1 | Mockup: glowing dots on houses near the camera. Build: county view, glowing dots on the 41 top residential-pool matches | Real answer: a county overview is top matches, not a count, so no number is shown |
| 3 | step 2–4 | Numbers: mockup 1,188 / 214 / 31. Build 946 / 63 / none | Real numbers from the envelopes. Step 4: none of the 63 houses' 69 docks is new since 2020 (eye-checked contact sheet, `data/out/dock_change_sheet.jpg`); the reply says so |
| 4 | step 4 | Mockup pulses the new docks; build keeps the step-3 picture | Nothing new to show (see 3) |
| 5 | steps | "N steps" lines differ (e.g. 2 vs 4) | The real tools that ran |
| 6 | step 3 | Docks are real dock outlines (irregular shapes), not straight bars | Real detector outlines |
| 7 | pick | Tile labels "Community / apartment pool", "Lake / water" vs "Community pool", "Lake or pond"; real NAIP thumbnails | Labels come from the engine's meanings config; images are real |
| 8 | sheet | Facts: no dock length; "next to the house" instead of "behind the house" | Dock length is not measured yet; "behind" can't be known from above |
| 9 | sheet | Map behind the sheet is at z18 with the pool outlined in white (mockup: white ring at mid zoom) | Zoom-aware styling per MEGA BUILD 1.4 (outlines above z16) |
| 10 | transition | Same timings/easings; the map behind shows real tiles loading | Real tiles |
