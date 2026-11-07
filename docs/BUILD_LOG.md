# Build log

Times are local (America/New_York).

| time | milestone |
|---|---|
| 2026-11-07 15:20 | Donovan: Recovered the mock MCP server and map agent from the mistakenly used repository into `donovan/mcp-agent`; aligned MCP input names with the published contract. Nine mock MCP tools list and call over stdio; 22 offline MCP and agent tests pass. Live HTTP routing awaits contract details. |
| 2026-11-07 15:23 | Donovan: Confirmed every tracked MCP and agent file from the earlier repository is present here. The MCP stdio client calls all nine tools and lists the about resource; all four mock agent CLI examples run; 22 offline tests pass. |

## Donovan:

- 2026-11-07 14:46 ET — In the earlier repository, added the mock MCP server, nine tools, versioned fixtures, the `groundtruth://about` resource, API retry and fallback adapter, and 15 offline tests. This work was recovered into this repository at 15:20 ET.
- 2026-11-07 14:57 ET — In the earlier repository, added the mock map agent with rules planning, optional OpenAI-compatible planning, people-data declines, bounded tool calls, configured places, CLI output, and JSON Schema map actions. This work was recovered into this repository at 15:20 ET.
| 2026-11-07 14:09 | Start. Repo foundation: MIT licence, CLAUDE.md rules, envelope + DataProvider interface, docs skeleton. |
| 2026-11-07 14:12 | Mecklenburg boundary fetched (Census TIGERweb, 1,413.18 km²). NAIP coverage check on Planetary Computer: full-county years 2022, 2020, 2018, 2016, 2014, 2012 → primary 2022, secondary 2020 (both 0.6 m). |
| 2026-11-07 14:34 | Working slice: UNC Charlotte AOI, 342 chips (NAIP 2022, 2 scenes), DINOv2 + OpenCLIP + FAISS. CPU throughput 4.4 chips/s (8 threads). Remote windowed COG reads were 265 s per AOI, so we switched to a whole-scene download (~10 MB/s with 16 streams). |
| 2026-11-07 14:40 | **Import pre-existing ORYN assets** (see docs/PRIOR_WORK.md): NC OSM extract (431 MB, data only), the shared DINOv2 weight cache, and design tokens → `web/shared/tokens.css`. Inventory in docs/ORYN_INVENTORY.md. No ORYN product code copied. |
| 2026-11-07 14:38 | Overture 2026-10-21.0 clipped to the county via DuckDB: 79,385 places, 393,968 buildings (98% OSM-sourced). The OSM/FEMA pass was stopped by host memory pressure; still to do. Evaluation hook runs (0 labels so far). Full-county precompute estimated at ~7 h/year on CPU, so it was **not started here**; see docs/PRECOMPUTE.md. |
| 2026-11-07 14:30 | Labelling page live at http://localhost:8765/label: NAIP 2022 tiles (cached PC mosaic proxy), 5 concepts, T/L/U/1–5 keys, OSM place search, journal + atomic snapshot + backup on every click, CSV/GeoJSON export. LabelStore tests pass (5). |
