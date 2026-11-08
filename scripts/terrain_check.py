"""Browser check: sign in, run the Lake Norman flagship, turn on hillshade / elevation / contours / 3D terrain from
the layers popover, check the legend and that no page errors occur. Screenshots → docs/media/v3/ui/terrain_*.png.
Usage: python scripts/terrain_check.py [BASE_URL]"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import _gt_auth  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ok = True
with sync_playwright() as p:
    br = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"])
    pg = br.new_page(viewport={"width": 1440, "height": 900})
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(base + "/", wait_until="load")
    pg.wait_for_selector("[data-testid=login]", timeout=30000)
    print("sign-in screen shown (auth on):", True)
    pg.locator("[data-testid=login-token]").fill(_gt_auth.token() or "")
    pg.locator("[data-testid=login] button[type=submit]").click()
    pg.wait_for_selector("[data-testid=starter]", timeout=30000)
    pg.locator("[data-testid=starter]").first.click()
    pg.wait_for_function("() => window.__gtLast && window.__gtLast.kind === 'investigation'", timeout=120000)
    print("flagship status:", pg.evaluate("() => window.__gtLast.status"))
    pg.get_by_role("button", name="Layers and options").click()
    for k in ("tint", "hillshade", "contours"):
        pg.locator(f"[data-testid=terrain-{k}]").check()
        pg.wait_for_timeout(600)
    pg.wait_for_selector("[data-testid=terrain-legend]", timeout=15000)
    leg = pg.locator("[data-testid=terrain-legend]").inner_text()
    good = "m" in leg and "unknown" in leg
    print("legend:", good, "|", leg.replace("\n", " ")[:140]); ok &= good
    pg.evaluate("() => window.__gtMap.jumpTo({center: [-80.878, 35.497], zoom: 14.2})")
    pg.wait_for_timeout(4000)
    pg.screenshot(path=str(ROOT / "docs/media/v3/ui/terrain_2d.png"))
    pg.locator("[data-testid=terrain-terrain3d]").check()
    pg.wait_for_timeout(5000)
    has3d = pg.evaluate("() => !!window.__gtMap.getTerrain()")
    print("3D terrain on:", has3d); ok &= has3d
    pg.screenshot(path=str(ROOT / "docs/media/v3/ui/terrain_3d.png"))
    print("page errors:", errs[:3]); ok &= not errs
    br.close()
print("TERRAIN", "PASS" if ok else "FAIL")
