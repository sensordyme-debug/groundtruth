"""demo_check v2: the conversational demo (web/app-v2) end to end in headless Chromium, against the running API.

  landing → "Houses with pools" → "in Lake Norman" → "with lake and dock access" → "which got docks since 2020"
  → open a result (detail sheet) → swipe → "Not this" (the count drops by one) → ⋯ Export CSV (rows == the count shown)
  → "who owns these houses" (declined in the chat) ; new search → "Swimming pools" → pick grid → Continue → "in Lake Norman"
Asserts DOM state, counts, banned words and per-message latency; saves screenshots (--shots DIR) and a JSON report.

Usage (WSL): ~/.venvs/gt-browser/bin/python scripts/demo_check_v2.py [--base http://127.0.0.1:8000] [--shots DIR]
             [--budget-ms 1500] [--size 1440x900] [--label warm]
Exit code 0 = every check passed.
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
# Words and widgets that must never be visible in the main view (MEGA BUILD 1.5). Checked on the visible DOM text.
BANNED = ["MOCK", "rules planner", "partial", "envelope", "chips", "cells", "sense", "Indexed now", "confidence",
          "raw score", "status"]
CHAIN = ["Houses with pools", "in Lake Norman", "with lake and dock access", "which got docks since 2020"]

checks: list[dict] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    checks.append({"check": name, "ok": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    return bool(cond)


def visible_text(page: Page) -> str:
    return page.evaluate("() => document.body.innerText")


def banned_hits(page: Page) -> list[str]:
    t = visible_text(page)
    return [w for w in BANNED if re.search(rf"\b{re.escape(w)}\b", t, re.I)]


def weights(page: Page) -> list[int]:
    return page.evaluate("""() => { const s = new Set(); for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el); if (cs.visibility !== 'hidden' && el.offsetParent) s.add(parseInt(cs.fontWeight)); }
        return [...s].sort(); }""")


def n_assistant(page: Page) -> int:
    return page.locator("[data-testid=assistant-msg]").count()


def send(page: Page, text: str, timeout_s: float = 120) -> dict:
    """Type into the active composer (or click the pill with this label) and wait for the new reply."""
    before = n_assistant(page)
    t0 = time.perf_counter()
    pill = page.locator("button.gt-pill", has_text=re.compile(rf"^{re.escape(text)}$"))
    if pill.count() and pill.first.is_visible():
        pill.first.click()
    else:
        box = page.locator("#gt-ask-1") if page.locator("[data-testid=landing]").is_visible() else page.locator("#gt-ask-2")
        box.fill(text)
        box.press("Enter")
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=assistant-msg]').length > {before} "
                           "&& !document.querySelector('[aria-label=Working].gt-dots')", timeout=timeout_s * 1000)
    ms = (time.perf_counter() - t0) * 1000
    last = page.evaluate("() => window.__gtLast || {}")
    reply = page.locator("[data-testid=reply]").last.inner_text()
    card = page.locator("[data-testid=assistant-msg]").last.locator("[data-testid=card-title]")
    title = card.inner_text() if card.count() else ""
    return {"text": text, "ms": round(ms), "api_ms": last.get("ms"), "reply": reply, "card": title,
            "count": last.get("count"), "status": last.get("status"), "brain": last.get("brain")}


def settle(page: Page, extra_ms: int = 1900) -> None:
    page.wait_for_timeout(extra_ms)  # flyTo 1.6 s + paint transitions
    try:
        page.wait_for_function("() => window.__gtMapIdle === true", timeout=15000)
    except Exception:  # noqa: BLE001 (tiles still streaming: screenshot anyway)
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shots", default=None, help="save screenshots here (e.g. docs/media/v2/compare)")
    ap.add_argument("--budget-ms", type=float, default=1500)
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--label", default="run")
    ap.add_argument("--report", default=str(ROOT / "data" / "out" / "demo_check_v2.json"))
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split("x"))
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, state: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{state}_build_{w}x{h}.png"))

    health = json.load(urllib.request.urlopen(f"{a.base}/api/v1/health", timeout=10))
    ok("api healthy", health.get("ok") is True, json.dumps(health.get("warmup", {}))[:160])
    turns: list[dict] = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page(viewport={"width": w, "height": h})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
        t0 = time.perf_counter()
        page.goto(a.base + "/", wait_until="load")
        page.wait_for_selector("h1", timeout=30000)
        ok("landing renders", page.locator("h1", has_text="What do you want to find?").is_visible(),
           f"{(time.perf_counter() - t0) * 1000:.0f} ms")
        ok("landing: no banned words", not banned_hits(page), ",".join(banned_hits(page)))
        page.wait_for_timeout(600)
        shot(page, "landing")

        # ---- the Lake Norman chain
        for i, tx in enumerate(CHAIN):
            if i == 0:
                page.locator("#gt-ask-1").fill(tx)
                page.locator("#gt-ask-1").press("Enter")
                page.wait_for_timeout(300)
                shot(page, "transition")
                before = 0
                page.wait_for_function(f"() => document.querySelectorAll('[data-testid=assistant-msg]').length > {before}",
                                       timeout=120000)
                last = page.evaluate("() => window.__gtLast || {}")
                r = {"text": tx, "ms": last.get("ms"), "api_ms": last.get("ms"), "reply": page.locator("[data-testid=reply]").last.inner_text(),
                     "card": "", "count": last.get("count"), "status": last.get("status"), "brain": last.get("brain")}
            else:
                r = send(page, tx)
            turns.append(r)
            settle(page)
            shot(page, f"step{i + 1}")
            ok(f"step {i + 1} '{tx}' answered", r["status"] == "ok" and bool(r["reply"]), f"{r['reply'][:90]!r} [{r['brain']}]")
            ok(f"step {i + 1} latency ≤ {a.budget_ms:.0f} ms", (r["api_ms"] or 1e9) <= a.budget_ms, f"{r['api_ms']} ms")
            ok(f"step {i + 1}: no banned words", not banned_hits(page), ",".join(banned_hits(page)))
            if i >= 1 and i <= 2:
                ok(f"step {i + 1} card count shown", r["count"] is not None and str(f"{r['count']:,}") in r["card"], r["card"])
            nums = re.findall(r"\d[\d,]*", r["reply"])
            if r["count"] is not None and nums:
                ok(f"step {i + 1} reply number matches the card", f"{r['count']:,}" in r["reply"], r["reply"][:80])
        ok("pills only under the latest reply",
           page.locator("[data-testid=assistant-msg]").nth(0).locator("button.gt-pill").count() == 0)

        # ---- detail sheet on the step-3 set (the last card that has thumbnails)
        cards = page.locator("[data-testid=result-card]")
        target = cards.last
        count_before = turns[2]["count"]
        ok("a result card with thumbnails", target.locator(".gt-thumb").count() > 0, f"{target.locator('.gt-thumb').count()} thumbs")
        t1 = time.perf_counter()
        target.locator(".gt-thumb").first.click()
        page.wait_for_function("() => document.querySelector('[data-testid=sheet]').getAttribute('aria-hidden') === 'false'")
        page.wait_for_function("() => [...document.querySelectorAll('[data-testid=swipe] img')].every(i => i.complete && i.naturalWidth > 0)",
                               timeout=20000)
        ok("detail sheet opens with 2020 + 2022 crops", True, f"{(time.perf_counter() - t1) * 1000:.0f} ms")
        settle(page, 1700)
        page.locator("input.gt-range").fill("20")
        page.wait_for_timeout(250)
        shot(page, "sheet")
        # "Not this": the set re-runs without this house; the latest card's count drops by one
        last_count = count_before  # the sheet came from the step-3 card
        page.get_by_role("button", name="Not this").click()
        page.wait_for_function(f"() => (window.__gtLast || {{}}).count === {json.dumps((last_count or 0) - 1)}", timeout=30000)
        new_count = page.evaluate("() => (window.__gtLast || {}).count")
        ok("'Not this' updates the count", last_count is not None and new_count == last_count - 1,
           f"{last_count} → {new_count} (step-3 count was {count_before})")
        page.keyboard.press("Escape")
        # ⋯ Export CSV on the latest card: rows == the count shown
        target.get_by_role("button", name="More options").click()
        with page.expect_download(timeout=30000) as dl:
            page.get_by_role("menuitem", name="Export CSV").click()
        path = dl.value.path()
        rows = sum(1 for _ in open(path, encoding="utf-8")) - 1
        exp = page.evaluate("() => window.__gtExport || {}")
        ok("Export CSV rows == count shown", rows == exp.get("count") == new_count, f"{rows} rows, card {exp.get('count')}")

        # ---- decline
        r = send(page, "who owns these houses")
        turns.append(r)
        ok("people question declined in the chat", r["status"] == "declined" and "people" in r["reply"].lower(), r["reply"][:90])

        # ---- pick grid
        page.get_by_role("button", name="New search").click()
        page.wait_for_timeout(700)
        page.locator("#gt-ask-1").fill("Swimming pools")
        page.locator("#gt-ask-1").press("Enter")
        page.wait_for_selector("[data-testid=pick-grid]", timeout=120000)
        last = page.evaluate("() => window.__gtLast || {}")
        ok("'Swimming pools' asks which kind (pick grid)", page.locator("[data-testid=pick-grid] .gt-tile").count() >= 4,
           f"{page.locator('[data-testid=pick-grid] .gt-tile').count()} tiles, {last.get('ms')} ms")
        page.wait_for_function("() => [...document.querySelectorAll('.gt-tileimg')].every(i => i.complete)", timeout=20000)
        page.locator("[data-sense=backyard_pool]").click()
        page.wait_for_timeout(200)
        shot(page, "pick")
        before = n_assistant(page)
        page.get_by_role("button", name="Continue").click()
        page.wait_for_function(f"() => document.querySelectorAll('[data-testid=assistant-msg]').length > {before} "
                               "&& !document.querySelector('[aria-label=Working].gt-dots')", timeout=120000)
        last = page.evaluate("() => window.__gtLast || {}")
        ok("Continue → where should I look", "where" in page.locator("[data-testid=reply]").last.inner_text().lower(),
           f"{last.get('ms')} ms")
        r = send(page, "in Lake Norman")
        turns.append(r)
        settle(page)
        ok("picked pools in Lake Norman", r["status"] == "ok" and bool(r["card"]), f"{r['card']} · {r['api_ms']} ms")
        ok("no banned words at the end", not banned_hits(page), ",".join(banned_hits(page)))
        ws = weights(page)
        ok("font weights ≤ 500", max(ws or [400]) <= 500, str(ws))
        ok("no page or console errors", not errors, "; ".join(errors)[:200])
        br.close()

    rep = {"label": a.label, "size": a.size, "budget_ms": a.budget_ms, "turns": turns, "checks": checks,
           "passed": all(c["ok"] for c in checks), "t": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(json.dumps(rep, indent=1))
    print(f"\n{'ALL PASS' if rep['passed'] else 'FAILED'}: {sum(c['ok'] for c in checks)}/{len(checks)} → {a.report}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
