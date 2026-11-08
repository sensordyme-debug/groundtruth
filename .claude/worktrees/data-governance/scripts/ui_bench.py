"""UI load benchmark for the demo map (Phase 1.2: context layers GeoJSON -> vector tiles).

Opens the demo with some context layers on and a fixed view, waits until the map has settled, and records:
time to the last map 'idle' event (ms since navigation start), bytes and count of context-layer requests
(add_layer tool calls + /api/v1/vt tiles), total requests, and JS heap (CDP Performance.getMetrics).

One headless Chromium at a time, a fresh browser context (empty cache) per run, closed afterwards.
Needs the small bench hook in web/src/mapview.ts (window.__gtMap, window.__gtIdleTimes).

Usage (WSL):
  ~/.venvs/gt-browser/bin/python scripts/ui_bench.py --label before --runs 3 --shot docs/media/phase1_layers_before.png
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time

from playwright.sync_api import sync_playwright

DEFAULT_URL = ("http://localhost:8000/?place=Ballantyne&layers_panel=1&layers=fema_flood_zones,osm_i485,osm_water"
               "&view=-80.870246,35.034659,-80.830246,35.074659")
QUIET_S = 3.0  # settled = map idle + no network activity for this long
TIMEOUT_S = 120.0


def is_layer_request(url: str) -> bool:
    return "/api/v1/tools/add_layer" in url or "/api/v1/vt/" in url


def renderer_rss_mb() -> float | None:
    """RSS of the Playwright Chromium renderer processes (page + its web workers). JSHeapUsedSize is the main
    thread only, and MapLibre parses GeoJSON / vector tiles in workers, so this is the fairer memory number.
    Linux only; assumes one Playwright browser at a time."""
    total = 0
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read().decode(errors="ignore")
                if "ms-playwright" not in cmd or "--type=renderer" not in cmd:
                    continue
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1])
            except OSError:
                continue
    except OSError:
        return None
    return round(total / 1024, 1)


def one_run(pw, url: str, shot: str | None) -> dict:
    browser = pw.chromium.launch(headless=True, args=["--enable-precise-memory-info", "--use-gl=swiftshader",
                                                      "--enable-unsafe-swiftshader"])
    try:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.send("Performance.enable")
        # Playwright request events also cover MapLibre's web-worker fetches (vector tiles), which a page-level CDP
        # Network session does not see. Sizes are read after the page settles.
        reqs: dict[object, dict] = {}
        last_net = [time.monotonic()]

        def on_req(r):
            if not r.url.startswith("http"):
                return
            reqs[r] = {"url": r.url, "done": False}
            last_net[0] = time.monotonic()

        def on_fin(r, failed=False):
            if r in reqs:
                reqs[r]["done"] = True; reqs[r]["failed"] = failed
            last_net[0] = time.monotonic()

        ctx.on("request", on_req)
        ctx.on("requestfinished", on_fin)
        ctx.on("requestfailed", lambda r: on_fin(r, True))

        page.goto(url, wait_until="load")
        t0 = time.monotonic()
        settled = False
        while time.monotonic() - t0 < TIMEOUT_S:
            page.wait_for_timeout(250)
            st = page.evaluate("""() => { const m = window.__gtMap; return {
                loaded: !!(m && m.loaded() && m.areTilesLoaded()), idles: (window.__gtIdleTimes || []).length,
                busy: !!document.querySelector('.spin') }; }""")
            pending = any(not r["done"] for r in reqs.values())
            if (st["loaded"] and st["idles"] and not pending and not st["busy"]
                    and time.monotonic() - last_net[0] > QUIET_S):
                settled = True
                break
        idle_ms = page.evaluate("() => { const t = window.__gtIdleTimes || []; return t.length ? t[t.length - 1] : null; }")
        ctx_layers = page.evaluate("""() => { const m = window.__gtMap; if (!m) return [];
            return m.getStyle().layers.filter(l => l.id.startsWith('ctx-'))
              .map(l => ({ id: l.id, src: m.getSource(l.source)?.type, vis: m.getLayoutProperty(l.id, 'visibility') ?? 'visible' })); }""")
        metrics = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        heap_used = metrics.get("JSHeapUsedSize", 0)
        cdp.send("HeapProfiler.collectGarbage")
        metrics2 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        rss_mb = renderer_rss_mb()
        for req, r in reqs.items():
            try:
                s = req.sizes()
                r["bytes"] = s["responseBodySize"] + s["responseHeadersSize"]
            except Exception:
                r["bytes"] = 0
        if shot:
            page.screenshot(path=shot)
        layer =[r for r in reqs.values() if is_layer_request(r["url"])]
        return {
            "settled": settled,
            "idle_ms": round(idle_ms) if idle_ms is not None else None,
            "layer_bytes": sum(r["bytes"] for r in layer),
            "layer_requests": len(layer),
            "requests": len(reqs),
            "total_bytes": sum(r["bytes"] for r in reqs.values()),
            "failed": sum(1 for r in reqs.values() if r.get("failed")),
            "heap_mb": round(heap_used / 2**20, 1),
            "heap_after_gc_mb": round(metrics2.get("JSHeapUsedSize", 0) / 2**20, 1),
            "renderer_rss_mb": rss_mb,
            "ctx_layers": ctx_layers,
        }
    finally:
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--label", default="run")
    ap.add_argument("--shot", default=None, help="screenshot path (taken on the first run)")
    ap.add_argument("--out", default=None, help="write results JSON here")
    a = ap.parse_args()
    runs = []
    with sync_playwright() as pw:
        for i in range(a.runs):
            r = one_run(pw, a.url, a.shot if i == 0 else None)
            print(json.dumps({k: v for k, v in r.items() if k != "ctx_layers"}))
            runs.append(r)
    keys = ["idle_ms", "layer_bytes", "layer_requests", "requests", "total_bytes", "heap_mb", "heap_after_gc_mb",
            "renderer_rss_mb"]
    med = {k: statistics.median([r[k] for r in runs if r[k] is not None]) for k in keys}
    summary = {"label": a.label, "url": a.url, "runs": a.runs, "median": med, "ctx_layers": runs[-1]["ctx_layers"]}
    print(json.dumps(summary, indent=1))
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"summary": summary, "runs": runs}, f, indent=1)


if __name__ == "__main__":
    main()
