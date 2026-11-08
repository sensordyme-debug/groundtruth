"""Validation Studio browser check: opens the studio, loads the queue, shows a sample on the map with its prediction,
changes the class filter, opens the metrics. READ-ONLY: it never submits a review (reviews must come from humans)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ok = True
with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(base + "/", wait_until="load")
    pg.wait_for_selector("[data-testid=rail-validate]")
    pg.locator("[data-testid=rail-validate]").click()
    pg.wait_for_selector("[data-testid=vs-card]", timeout=30000)
    pg.wait_for_timeout(2500)
    sid = pg.locator("[data-testid=vs-card]").get_attribute("data-sample")
    n_src = pg.evaluate("() => (window.__gtMap.getSource('review')?._data?.features || []).length")
    print("studio card:", sid, "· prediction on map:", n_src >= 1); ok &= bool(sid) and n_src >= 1
    pg.screenshot(path=str(ROOT / "docs/media/v3/ui/09_validation_studio_1440x900.png"))
    pg.locator("[data-testid=vs-class]").select_option("building")
    pg.wait_for_timeout(2500)
    sid2 = pg.locator("[data-testid=vs-card]").get_attribute("data-sample")
    print("class filter changes the queue:", sid2 != sid); ok &= sid2 != sid
    prog = pg.locator("[data-testid=vs-progress]").inner_text()
    print("progress:", prog); ok &= "reviewed" in prog
    pg.screenshot(path=str(ROOT / "docs/media/v3/ui/10_validation_buildings_1440x900.png"))
    print("page errors:", errs); ok &= not errs
    br.close()
print("STUDIO", "PASS" if ok else "FAIL")
