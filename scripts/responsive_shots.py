"""Tablet + phone screenshots of the workspace (landing, then the flagship investigation) → docs/media/v3/ui/."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import _gt_auth  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
with sync_playwright() as p:
    br = p.chromium.launch()
    for name, (w, h) in {"tablet": (820, 1180), "phone": (390, 844)}.items():
        pg = br.new_page(viewport={"width": w, "height": h})
        _gt_auth.login_browser(pg, base)
        pg.goto(base + "/", wait_until="load")
        pg.wait_for_selector("h1")
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(ROOT / f"docs/media/v3/ui/r_{name}_landing.png"))
        pg.locator("[data-testid=starter]").first.click()
        pg.wait_for_function("() => window.__gtLast && window.__gtLast.kind === 'investigation'", timeout=60000)
        pg.wait_for_timeout(2000)
        pg.screenshot(path=str(ROOT / f"docs/media/v3/ui/r_{name}_done.png"))
        ow = pg.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
        print(name, "horizontal overflow:", ow)
        if name == "phone":  # the rail is off-canvas until the ☰ drawer opens; then every destination is one tap away
            vis = lambda: pg.evaluate("() => { const r = document.querySelector('.gt-rail').getBoundingClientRect(); return r.right > 0; }")
            hidden = not vis()
            pg.get_by_role("button", name="Open sidebar").first.click()
            pg.wait_for_timeout(500)
            shown = vis()
            pg.screenshot(path=str(ROOT / f"docs/media/v3/ui/r_{name}_drawer.png"))
            pg.locator("[data-testid=rail-validate]").click()
            studio = pg.locator("[data-testid=vs-card]").first
            studio.wait_for(timeout=30000)
            print("phone rail: hidden by default", hidden, "· shown with the drawer", shown, "· Validate reachable", studio.is_visible())
        pg.close()
    br.close()
