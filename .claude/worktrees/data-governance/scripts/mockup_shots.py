"""Screenshots of the approved mockup (docs/design/target/mockup.html) at the same states demo_check_v2 captures from
the build: landing, transition, step1–4, sheet, pick → docs/media/v2/compare/<state>_mockup_<w>x<h>.png.

  ~/.venvs/gt-browser/bin/python scripts/mockup_shots.py [--size 1440x900]
"""
import argparse
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--size", default="1440x900")
a = ap.parse_args()
w, h = (int(v) for v in a.size.split("x"))
out = ROOT / "docs/media/v2/compare"
out.mkdir(parents=True, exist_ok=True)


def shot(page, state):
    page.screenshot(path=str(out / f"{state}_mockup_{w}x{h}.png"))


def pill(page, label):
    page.locator("button", has_text=re.compile(rf"^\s*{re.escape(label)}\s*$")).last.click()


with sync_playwright() as p:
    br = p.chromium.launch()
    page = br.new_page(viewport={"width": w, "height": h})
    page.goto((ROOT / "docs/design/target/mockup.html").as_uri(), wait_until="load")
    page.wait_for_selector("text=What do you want to find?", timeout=30000)
    page.wait_for_timeout(1200)
    shot(page, "landing")
    pill(page, "Houses with pools")
    page.wait_for_timeout(300)
    shot(page, "transition")
    page.wait_for_timeout(2600)
    shot(page, "step1")
    for i, lab in enumerate(["in Lake Norman", "with lake and dock access", "which got docks since 2020"], start=2):
        pill(page, lab)
        page.wait_for_timeout(3000)
        shot(page, f"step{i}")
    page.get_by_role("button", name="Open this result").nth(4).click()  # the step-3 card's first thumbnail
    page.wait_for_timeout(1800)
    page.locator("input[type=range]").fill("20")
    page.wait_for_timeout(300)
    shot(page, "sheet")
    page.keyboard.press("Escape")
    page.get_by_role("button", name="New search").click()
    page.wait_for_timeout(900)
    pill(page, "Swimming pools")
    page.wait_for_timeout(2400)
    page.locator("button[aria-pressed]").first.click()
    page.wait_for_timeout(300)
    shot(page, "pick")
    br.close()
print("ok", out)
