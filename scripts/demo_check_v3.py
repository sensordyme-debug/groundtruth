"""demo_check v3: the investigation workspace (web/app-v2) end to end in headless Chromium against the running API.

  landing → flagship starter card (flood exposure of homes with pools, Lake Norman) → the plan runs (real step states)
  → findings + evidence → open a finding's evidence → open a result (per-result evidence, Confirm) → follow-up
  "which of those are less than 1 m above the flood elevation" → report download → reload → the investigation is in
  the sidebar and restores → "who owns these homes" declined → a plain search ("swimming pools") still gets the pick grid
Asserts DOM, numbers consistent between card / findings / report, banned words, latency; screenshots with --shots.

  ~/.venvs/gt-browser/bin/python scripts/demo_check_v3.py [--base http://127.0.0.1:8000] [--shots docs/media/v3/ui] [--size 1440x900]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BANNED = ["MOCK", "rules planner", "envelope", "chips", "cells", "sense", "Indexed now", "raw score", "undefined", "NaN", "[object Object]"]
checks: list[dict] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    checks.append({"check": name, "ok": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    return bool(cond)


def banned(page: Page) -> list[str]:
    t = page.evaluate("() => document.body.innerText")
    return [w for w in BANNED if re.search(rf"\b{re.escape(w)}\b", t, re.I)]


def wait_inv_done(page: Page, n_before: int, timeout_s: float = 120) -> dict:
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=investigation]').length > {n_before}", timeout=timeout_s * 1000)
    page.wait_for_function("() => { const b = [...document.querySelectorAll('[data-testid=investigation]')].pop();"
                           " return b && !['running','planning'].includes(b.dataset.status) && window.__gtLast && window.__gtLast.kind === 'investigation'; }",
                           timeout=timeout_s * 1000)
    return page.evaluate("() => window.__gtLast")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shots", default=None)
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--budget-ms", type=float, default=3000)
    ap.add_argument("--report", default=str(ROOT / "data" / "out" / "demo_check_v3.json"))
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split("x"))
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}_{w}x{h}.png"))

    timings: dict[str, int] = {}
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
        page.goto(a.base + "/", wait_until="load")
        page.wait_for_selector("h1", timeout=30000)
        page.wait_for_timeout(700)
        ok("landing renders with rail, sidebar and starter cards",
           page.locator("[data-testid=rail-home]").is_visible() and page.locator("[data-testid=starter]").count() == 3)
        ok("landing: no banned words", not banned(page), ",".join(banned(page)))
        shot(page, "01_landing")

        # ---- flagship investigation from the starter card
        page.locator("[data-testid=starter]").first.click()
        page.wait_for_selector("[data-testid=plan-step]", timeout=60000)
        page.wait_for_timeout(250)
        shot(page, "02_running")
        last = wait_inv_done(page, 0)
        timings["flagship_total_ms"], timings["flagship_first_ms"] = last.get("ms"), last.get("first_ms")
        page.wait_for_timeout(1900)
        inv = json.load(urllib.request.urlopen(f"{a.base}/api/v1/investigations/{last['id']}", timeout=10))
        statuses = [s["status"] for s in inv["plan"]["steps"]]
        ok("flagship: every plan step done", statuses and all(s == "done" for s in statuses), ",".join(statuses))
        ok(f"flagship: finished in ≤ {a.budget_ms:.0f} ms", (last.get("ms") or 1e9) <= a.budget_ms, f"{last.get('ms')} ms, first result {last.get('first_ms')} ms")
        card = page.locator("[data-testid=result-card]").last
        title = card.locator("[data-testid=card-title]").inner_text()
        n = int(re.sub(r"[^\d]", "", title.split()[0]))
        head = page.locator("[data-testid=finding]").first.inner_text()
        ok("card count == the headline finding's count", f"{n} of" in head, f"card '{title}' · finding '{head[:70]}'")
        ok("findings include a data gap", any("Gap" in x or "no published" in x or "not this one" in x
                                                for x in page.locator("[data-testid=finding]").all_inner_texts()))
        ok("no banned words after the investigation", not banned(page), ",".join(banned(page)))
        shot(page, "03_done")

        # ---- evidence: a finding → its evidence → the inspector
        page.locator("[data-testid=finding]").first.locator("button").first.click()
        page.wait_for_selector("[data-testid=evidence-row]")
        page.locator("[data-testid=evidence-row]").first.click()
        page.wait_for_selector("[data-testid=evidence-panel]")
        page.wait_for_timeout(500)
        ok("evidence inspector shows source and method", "Source" in page.locator("[data-testid=evidence-panel]").inner_text())
        shot(page, "04_evidence")

        # ---- a result: per-result evidence + a human verdict
        card.locator(".gt-thumb").first.click()
        page.wait_for_selector("[data-testid=feature-evidence]", timeout=15000)
        page.wait_for_function("() => [...document.querySelectorAll('[data-testid=swipe] img')].every(i => i.complete && i.naturalWidth > 0)", timeout=20000)
        ok("result sheet has a WHY line and its evidence", page.locator("[data-testid=why]").count() == 1)
        page.locator("[data-testid=feature-evidence]").get_by_role("button", name="Confirm").click()
        page.wait_for_function("() => document.querySelector('[data-testid=feature-evidence]').innerText.includes('Recorded as confirmed')")
        ok("human verdict recorded", True)
        page.wait_for_timeout(1500)
        shot(page, "05_result")
        page.keyboard.press("Escape")

        # ---- follow-up
        t0 = time.perf_counter()
        before = page.locator("[data-testid=investigation]").count()
        page.locator("#gt-ask-2").fill("which of those are less than 1 m above the flood elevation")
        page.locator("#gt-ask-2").press("Enter")
        last = wait_inv_done(page, before)
        timings["followup_ms"] = last.get("ms")
        m = int(re.sub(r"[^\d]", "", page.locator("[data-testid=result-card]").last.locator("[data-testid=card-title]").inner_text().split()[0]))
        ok("follow-up narrows the previous set", 0 < m <= n, f"{n} → {m}, {last.get('ms')} ms")
        page.wait_for_timeout(1900)
        shot(page, "06_followup")

        # ---- report
        with page.expect_download(timeout=20000) as dl:
            page.locator("[data-testid=report-link]").last.click()
        md = open(dl.value.path(), encoding="utf-8").read()
        ok("report has findings, sources, gaps and reproducibility",
           all(k in md for k in ("## Findings", "## Sources", "## Limitations and gaps", "## Reproducibility")) and f"{m} of {n}" in md,
           f"{len(md)} chars")

        # ---- reload → restore from the sidebar
        page.wait_for_timeout(1200)  # autosave
        url = page.url
        page.reload(wait_until="load")
        page.wait_for_function("() => window.__gtRestored", timeout=60000)
        ok("deep link restores the investigation", "/s/" in url and page.locator("[data-testid=investigation]").count() >= 2, url)
        if not page.locator("[data-testid=sidebar]").is_visible():
            page.locator("[data-testid=rail-investigations]").click()
        page.wait_for_selector("[data-testid=session-row]")
        ok("sidebar lists it with its status", "Completed" in page.locator("[data-testid=session-row]").first.inner_text())
        shot(page, "07_restored_sidebar")

        # ---- decline + plain search fallback
        page.locator("#gt-ask-2").fill("who owns these homes")
        page.locator("#gt-ask-2").press("Enter")
        page.wait_for_function("() => document.body.innerText.includes(\"can't help with information about people\")", timeout=30000)
        ok("people question declined", True)
        page.locator("[data-testid=rail-home]").click()
        page.wait_for_timeout(600)
        page.locator("#gt-ask-1").fill("swimming pools")
        page.locator("#gt-ask-1").press("Enter")
        page.wait_for_selector("[data-testid=pick-grid]", timeout=60000)
        ok("plain search still asks which kind (pick grid)", True)
        shot(page, "08_search_fallback")
        ok("no page or console errors", not errors, "; ".join(errors)[:300])
        br.close()

    rep = {"size": a.size, "timings": timings, "checks": checks, "passed": all(c["ok"] for c in checks), "t": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(json.dumps(rep, indent=1))
    print(f"\n{'ALL PASS' if rep['passed'] else 'FAILED'}: {sum(c['ok'] for c in checks)}/{len(checks)} · {json.dumps(timings)}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
