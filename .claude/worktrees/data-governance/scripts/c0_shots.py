"""C0 screenshots: result readability at zoom 11 / 14 / 17 over trees, roofs and water (before vs after).

Drives the demo through the agent URL hook (?agent=run&prompt=...), waits until the run has settled, then re-centres the
map on the results (median of the result centroids; z17 centres on the result nearest that median) at each zoom and
saves docs/media/c0_<label>_<scene>_z<zoom>.png. One headless Chromium at a time, closed afterwards.

Usage (WSL):
  ~/.venvs/gt-browser/bin/python scripts/c0_shots.py --base http://127.0.0.1:8000 --label before
  ~/.venvs/gt-browser/bin/python scripts/c0_shots.py --base http://127.0.0.1:5174 --label after
"""
from __future__ import annotations

import argparse
import time
import urllib.parse

from playwright.sync_api import sync_playwright

SCENES = {
    "trees": ("pools within 1 km of I-485 in Ballantyne", "Ballantyne"),
    "roofs": ("buildings taller than 30 m in Uptown", "Uptown"),
    "water": ("boat docks on Lake Norman", "Lake Norman"),
    "new": ("new pools since 2020 in Ballantyne", "Ballantyne"),
    "states": ("pools within 1 km of I-485 in Ballantyne", "Ballantyne"),
}
ZOOMS = [11, 14, 17]
TIMEOUT_S = 240.0

CENTRE_JS = """() => {
  const m = window.__gtMap; if (!m || !m.style) return null; const fs = m.querySourceFeatures("results");
  const pts = [];
  let live = fs.filter(f => !f.properties.dim && f.properties.state !== "rejected");
  const nw = live.filter(f => f.properties.change === "new"); if (nw.length) live = nw;
  for (const f of (live.length ? live : fs)) { let c = f.geometry.coordinates; while (Array.isArray(c[0])) c = c[0]; pts.push(c); }
  if (!pts.length) return null;
  const med = (a) => { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
  const mx = med(pts.map(p => p[0])), my = med(pts.map(p => p[1]));
  let best = pts[0], bd = Infinity;
  for (const p of pts) { const d = (p[0] - mx) ** 2 + (p[1] - my) ** 2; if (d < bd) { bd = d; best = p; } }
  return { median: [mx, my], nearest: best, n: pts.length };
}"""


def wait_settled(page, quiet_s: float = 2.5, timeout_s: float = TIMEOUT_S) -> bool:
    t0 = time.monotonic(); calm_since = None
    while time.monotonic() - t0 < timeout_s:
        page.wait_for_timeout(300)
        st = page.evaluate("""() => { const m = window.__gtMap; return {
            loaded: !!(m && m.loaded() && m.areTilesLoaded()), busy: !!document.querySelector('.status-chip, .am .spin') }; }""")
        if st["loaded"] and not st["busy"]:
            calm_since = calm_since or time.monotonic()
            if time.monotonic() - calm_since > quiet_s:
                return True
        else:
            calm_since = None
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--scenes", default="trees,roofs,water")
    ap.add_argument("--out", default="docs/media")
    a = ap.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        try:
            for scene in a.scenes.split(","):
                prompt, place = SCENES[scene]
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.on("pageerror", lambda e: print("  pageerror:", str(e)[:300]))
                page.on("response", lambda r: r.status >= 400 and print("  http", r.status, r.url[:160]))
                q = urllib.parse.urlencode({"agent": "run", "prompt": prompt, "place": place, "tab": "log",
                                            "layers_panel": "1", "shot": "1"})
                page.goto(f"{a.base}/?{q}", wait_until="load")
                page.wait_for_timeout(3000)
                ok = wait_settled(page, quiet_s=6.0)
                if scene == "new":  # colour by change: switch on the "New since 2020" layer toggle (compare_years)
                    page.get_by_text("New since 2020", exact=True).first.click()
                    page.wait_for_timeout(1500)
                    wait_settled(page, quiet_s=3.0)
                c = page.evaluate(CENTRE_JS)
                print(scene, "settled" if ok else "TIMEOUT", c and c["n"], "features in view")
                if not c:
                    ctx.close(); continue
                if scene == "states":  # ✓ row 1, ✗ row 2, select row 3, hover another result; z14 + z17 around row 3
                    rows = page.locator(".list-sec .res")
                    rows.nth(0).locator("button.ok").click(); page.wait_for_timeout(1500); wait_settled(page, 3.0)
                    rows.nth(1).locator("button.no").click(); page.wait_for_timeout(1500); wait_settled(page, 3.0)
                    rows.nth(2).click(); page.wait_for_timeout(2500); wait_settled(page, 2.0)
                    sel = page.evaluate("() => window.__gtMap.getCenter().toArray()")
                    for z in (14, 17):
                        page.evaluate("([c, z]) => window.__gtMap.jumpTo({ center: c, zoom: z })", [sel, z])
                        page.wait_for_timeout(800); wait_settled(page, 2.0)
                        # hover the result nearest the centre that is not the selected one
                        xy = page.evaluate("""() => { const m = window.__gtMap; const c = m.project(m.getCenter());
                          const fs = m.queryRenderedFeatures(undefined, { layers: ['results-pts-dot', 'results-cl-dot', 'results-hit'] })
                            .filter(f => !f.properties.sel && f.properties.state === 'hit');
                          let best = null, bd = Infinity;
                          for (const f of fs) { let g = f.geometry.coordinates; while (Array.isArray(g[0])) g = g[0];
                            const p = m.project(g); const d = (p.x - c.x) ** 2 + (p.y - c.y) ** 2; if (d > 400 && d < bd) { bd = d; best = p; } }
                          if (!best) return null; const r = m.getCanvas().getBoundingClientRect();
                          return [best.x + r.left, best.y + r.top]; }""")
                        if xy:
                            page.mouse.move(xy[0], xy[1]); page.wait_for_timeout(700)
                        path = f"{a.out}/c0_{a.label}_states_z{z}.png"
                        page.screenshot(path=path); print("  saved", path)
                    ctx.close(); continue
                for z in ZOOMS:
                    centre = c["nearest"] if z >= 17 else c["median"]
                    page.evaluate("([c, z]) => window.__gtMap.jumpTo({ center: c, zoom: z })", [centre, z])
                    page.wait_for_timeout(800)
                    wait_settled(page, quiet_s=2.0, timeout_s=60)
                    page.wait_for_timeout(600)
                    path = f"{a.out}/c0_{a.label}_{scene}_z{z}.png"
                    page.screenshot(path=path)
                    print("  saved", path)
                ctx.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
