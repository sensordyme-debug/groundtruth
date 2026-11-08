"""Redesign phase 1 screenshots: landing, the landing → chat transition, the county overview, a result, steps, details.

Drives the real UI (types into the composer, presses Enter) against a running dev server that proxies the API, and saves
docs/media/redesign_*.png. One headless Chromium at a time, closed afterwards.

Usage (WSL):
  ~/.venvs/gt-browser/bin/python scripts/redesign_shots.py --base http://127.0.0.1:5175   (dev; Inter may 403 through the node_modules symlink)
  ~/.venvs/gt-browser/bin/python scripts/redesign_shots.py --base http://127.0.0.1:5176   (vite preview of a build: Inter bundled)
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

TIMEOUT_S = 240.0


def wait_map(page: Page, quiet_s: float = 1.5, timeout_s: float = TIMEOUT_S) -> bool:
    """Until the map has loaded its tiles, no assistant turn is pending, and that held for quiet_s."""
    t0 = time.monotonic(); calm = None
    while time.monotonic() - t0 < timeout_s:
        page.wait_for_timeout(250)
        st = page.evaluate("""() => { const m = window.__gtMap; return {
            ok: !!(m && m.loaded() && m.areTilesLoaded() && !m.isMoving()), busy: !!document.querySelector('.working') }; }""")
        if st["ok"] and not st["busy"]:
            calm = calm or time.monotonic()
            if time.monotonic() - calm > quiet_s:
                return True
        else:
            calm = None
    return False


def ask(page: Page, text: str) -> None:
    box = page.locator(".composer textarea")
    box.click()
    box.fill(text)
    box.press("Enter")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default="docs/media")
    ap.add_argument("--pick", action="store_true", help="also shoot the inline pick grid (pools in Ballantyne)")
    a = ap.parse_args()
    out = Path(a.out)
    shot = lambda page, name: (page.screenshot(path=str(out / name)), print("saved", out / name))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        try:
            def new_page(w: int, h: int) -> Page:
                ctx = browser.new_context(viewport={"width": w, "height": h})
                page = ctx.new_page()
                page.on("pageerror", lambda e: print("  pageerror:", str(e)[:300]))
                page.on("response", lambda r: r.status >= 400 and print("  http", r.status, r.url[:160]))
                page.goto(f"{a.base}/?shot=1", wait_until="load")
                wait_map(page, quiet_s=2.0)
                page.wait_for_timeout(800)
                print("  inter loaded:", page.evaluate("() => document.fonts.check('500 15px Inter')"))
                return page

            # 1. landing (1920 × 1080)
            page = new_page(1920, 1080)
            shot(page, "redesign_01_landing_1080.png")
            page.context.close()

            # 2. landing, transition, overview (1440 × 900)
            page = new_page(1440, 900)
            shot(page, "redesign_01_landing.png")
            ask(page, "houses with pools")
            page.wait_for_timeout(60)  # panel morph is 250 ms, the map un-blur 1.2 s: this is the middle of both
            shot(page, "redesign_02_transition_mid.png")
            print("  overview settled:", wait_map(page, quiet_s=2.5))
            shot(page, "redesign_03_chat_overview.png")
            page.context.close()

            # 3. a result: card, steps open, details open, ⋯ menu
            page = new_page(1440, 900)
            ask(page, "boat docks on Lake Norman")
            print("  result settled:", wait_map(page, quiet_s=2.5))
            shot(page, "redesign_04_chat_result.png")
            page.locator(".steps-toggle").first.click()
            page.wait_for_timeout(400)
            shot(page, "redesign_05_steps_open.png")
            page.locator(".steps-toggle").first.click()
            page.locator(".card-foot .link.quiet").first.click()
            page.wait_for_timeout(400)
            shot(page, "redesign_06_result_card_details.png")
            page.locator(".card-foot .link.quiet").first.click()
            page.locator(".card-head .icon-btn").first.click()
            page.wait_for_timeout(300)
            shot(page, "redesign_07_card_menu.png")
            page.context.close()

            # 4. (optional) the inline "Which do you mean?" grid
            if a.pick:
                page = new_page(1440, 900)
                ask(page, "pools in Ballantyne")
                print("  pick settled:", wait_map(page, quiet_s=2.5))
                shot(page, "redesign_08_pick_inline.png")
                page.context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
