"""Validation Studio browser check: opens the studio, loads the queue, shows a sample on the map with its prediction,
changes the class filter, then exercises the review buttons and the polygon editor. READ-ONLY: review POSTs are
intercepted in the browser and answered locally, so nothing reaches the review store (reviews must come from humans);
the reviewed count is checked before and after to prove it."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import _gt_auth  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ok = True
with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    _gt_auth.login_browser(pg, base)
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
    ht = pg.locator("[data-testid=vs-holdout]")
    hold = ht.count() == 1 and "not measured" in ht.inner_text()  # 0 reviews: groups listed, nothing invented
    print("holdout-group table listed, unmeasured until reviewed:", hold); ok &= hold
    prog = pg.locator("[data-testid=vs-progress]").inner_text()
    print("progress:", prog); ok &= "reviewed" in prog
    pg.screenshot(path=str(ROOT / "docs/media/v3/ui/10_validation_buildings_1440x900.png"))
    # ---- review buttons + polygon editor, with the POST intercepted (never stored)
    import urllib.request
    def reviewed():
        r = urllib.request.Request(base + "/api/v1/validation/progress", headers=_gt_auth.headers())
        return json.load(urllib.request.urlopen(r, timeout=20)).get("reviewed")
    n0 = reviewed()
    posted = []

    def fake(route):
        body = json.loads(route.request.post_data or "{}")
        posted.append(body)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"review": {"decision": body.get("decision")}, "sample": {}}))
    pg.route("**/api/v1/validation/reviews", fake)
    sid3 = pg.locator("[data-testid=vs-card]").get_attribute("data-sample")
    pg.locator("[data-testid=vs-accept]").click()
    pg.wait_for_timeout(800)
    print("accept button posts {sample_id, decision}:", posted[-1:] == [{"sample_id": sid3, "decision": "accept"}])
    ok &= posted[-1:] == [{"sample_id": sid3, "decision": "accept"}]
    sid4 = pg.locator("[data-testid=vs-card]").get_attribute("data-sample")
    print("advances to the next sample:", sid4 != sid3); ok &= sid4 != sid3
    pg.locator("[data-testid=vs-draw]").click()
    pg.wait_for_timeout(400)
    box = pg.locator("canvas.maplibregl-canvas").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    for dx, dy in ((-40, -30), (40, -30), (40, 30), (-40, 30)):
        pg.mouse.click(cx + dx, cy + dy)
        pg.wait_for_timeout(250)
    pg.keyboard.press("Enter")
    pg.wait_for_selector("[data-testid=vs-save-polygon]", timeout=5000)
    pg.screenshot(path=str(ROOT / "docs/media/v3/ui/11_validation_polygon_1440x900.png"))
    pg.locator("[data-testid=vs-save-polygon]").click()
    pg.wait_for_timeout(800)
    last = posted[-1] if posted else {}
    ring = ((last.get("polygon") or {}).get("coordinates") or [[]])[0]
    good = last.get("decision") == "corrected" and last.get("sample_id") == sid4 and len(ring) == 5 and ring[0] == ring[-1]
    print("drawn polygon posts a closed 4-vertex ring as 'corrected':", good, f"({len(ring)} points)"); ok &= good
    n1 = reviewed()
    print("review store untouched:", n0 == n1, f"({n0} → {n1})"); ok &= n0 == n1
    print("page errors:", errs); ok &= not errs
    br.close()
print("STUDIO", "PASS" if ok else "FAIL")
