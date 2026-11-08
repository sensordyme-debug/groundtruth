"""Tablet + phone screenshots of the workspace (landing, then the flagship investigation) → docs/media/v3/ui/."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
with sync_playwright() as p:
    br = p.chromium.launch()
    for name, (w, h) in {"tablet": (820, 1180), "phone": (390, 844)}.items():
        pg = br.new_page(viewport={"width": w, "height": h})
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
        pg.close()
    br.close()
