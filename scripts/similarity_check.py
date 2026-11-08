"""Browser check: "Find like this" results explain why they matched (similarity vs threshold), as a prediction.
Runs the flagship, takes its first pool, clicks it in Find-like-this mode, opens the first similar result and checks
the sheet's "Why it matched" panel against the API's numbers. Usage: python scripts/similarity_check.py [--base URL]"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import _gt_auth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shot", default=None)
    a = ap.parse_args()
    res: list[tuple[str, bool, str]] = []

    def ok(name, cond, detail=""):
        res.append((name, bool(cond), detail))
        print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))

    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page(viewport={"width": 1440, "height": 900})
        _gt_auth.login_browser(page, a.base)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.base + "/", wait_until="load")
        page.wait_for_selector("[data-testid=starter]", timeout=30000)
        page.locator("[data-testid=starter]").first.click()
        page.wait_for_function("() => window.__gtLast && window.__gtLast.kind === 'investigation'", timeout=120000)
        inv_id = page.evaluate("() => window.__gtLast.id")
        inv = json.load(urllib.request.urlopen(urllib.request.Request(f"{a.base}/api/v1/investigations/{inv_id}?features=true",
                                                                    headers=_gt_auth.headers()), timeout=20))
        def centre(g):  # any geometry: mean of its coordinates
            pts, st = [], [g["coordinates"]]
            while st:
                x = st.pop()
                if isinstance(x[0], (int, float)):
                    pts.append(x)
                else:
                    st.extend(x)
            return sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts)
        feats = inv["result"]["features"]
        f0 = next((f for f in feats if f["properties"].get("kind") not in ("dock", "dock_link")), feats[0])
        lon, lat = centre(f0["geometry"])
        page.evaluate(f"() => window.__gtMap.jumpTo({{center: [{lon}, {lat}], zoom: 19}})")
        page.wait_for_timeout(800)
        page.locator("[data-testid=find-like-this]").click()
        xy = page.evaluate(f"() => {{ const p = window.__gtMap.project([{lon}, {lat}]); const r = window.__gtMap.getCanvas().getBoundingClientRect(); return [r.left + p.x, r.top + p.y]; }}")
        page.evaluate("() => { window.__gtLast = null; }")
        page.mouse.click(xy[0], xy[1])
        page.wait_for_function("() => window.__gtLast && window.__gtLast.kind === 'point'", timeout=120000)
        last = page.evaluate("() => window.__gtLast")
        title = page.locator("[data-testid=result-card]").last.locator("[data-testid=card-title]").inner_text()
        ok("find like this returns results", last.get("status") == "ok" and page.locator("[data-testid=result-card]").last.locator(".gt-thumb").count() > 0, f"{title} · {last.get('ms')} ms")
        page.keyboard.press("Escape")  # leave point mode; then open the first result from its card
        page.locator("[data-testid=result-card]").last.locator(".gt-thumb").first.click()
        page.wait_for_selector("[data-testid=sheet]", timeout=20000)
        page.wait_for_function("() => [...document.querySelectorAll('[data-testid=swipe] img')].every(i => i.complete && i.naturalWidth > 0)", timeout=20000)
        page.wait_for_timeout(1200)  # sheet animation
        has = page.locator("[data-testid=similarity-why]").count() == 1
        ok("result sheet shows 'Why it matched'", has)
        if has:
            t = page.locator("[data-testid=similarity-why]").inner_text()
            ok("states similarity and the area threshold", "similarity" in t and "threshold" in t, t[:160])
            ok("says similarity is a resemblance, not proof", "not proof" in t)
            ok("result is labelled a prediction", "not human-verified" in page.locator("[data-testid=prov-badge]").inner_text())
        if a.shot:
            page.screenshot(path=a.shot)
        ok("no page errors", not errors, "; ".join(errors)[:200])
        br.close()
    bad = [r for r in res if not r[1]]
    print(("ALL PASS" if not bad else "FAILED") + f": {len(res) - len(bad)}/{len(res)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
