#!/usr/bin/env python3
"""Browser interaction acceptance for the historical showcase_cases page.

The current same-source page has its own independent acceptance script:
scripts/unified_browser_acceptance.py.  This file is kept for the old
three-case page, which remains at showcase_cases.html.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/showcase_cases.html")
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/showcase/browser")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=args.chrome,
            args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            record_video_dir=str(args.output),
            record_video_size={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        console_errors = []
        page_errors = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        page.screenshot(path=str(args.output / "01-initial.png"))

        error_hidden = page.locator("#error").is_hidden()
        candidates = page.locator(".candidate").count()
        check("page_loads_without_error", error_hidden and not console_errors and not page_errors, {
            "error_hidden": error_hidden, "console_errors": console_errors, "page_errors": page_errors,
        })
        check("candidate_list_has_6", candidates == 6, {"count": candidates})
        first_clock = page.locator("#clock").inner_text()
        page.locator(".candidate").nth(1).click()
        page.wait_for_timeout(500)
        second_clock = page.locator("#clock").inner_text()
        check("candidate_selection_moves_clock", first_clock != second_clock, {"first": first_clock, "second": second_clock})

        before = page.evaluate("() => document.getElementById('video').currentTime")
        page.locator("#play").click()
        page.wait_for_timeout(1200)
        playing = page.evaluate("() => ({t:document.getElementById('video').currentTime,p:document.getElementById('video').paused})")
        check("play_advances", (not playing["p"]) and playing["t"] > before, {"before": before, "after": playing})
        page.locator("#play").click()
        page.wait_for_timeout(400)
        paused = page.evaluate("() => document.getElementById('video').paused")
        pause_time = page.evaluate("() => document.getElementById('video').currentTime")
        page.wait_for_timeout(700)
        pause_time_2 = page.evaluate("() => document.getElementById('video').currentTime")
        check("pause_holds", paused and abs(pause_time - pause_time_2) < 0.05, {"paused": paused, "t1": pause_time, "t2": pause_time_2})

        page.locator("#seek").evaluate("el => {el.value=2; el.dispatchEvent(new Event('input',{bubbles:true}))}")
        page.wait_for_timeout(400)
        seek_state = page.evaluate("() => ({t:document.getElementById('video').currentTime,clock:document.getElementById('clock').textContent})")
        check("seek_works", abs(seek_state["t"] - 2.0) < 0.15, seek_state)

        page.locator(".candidate").nth(0).click()
        page.locator("#evidencePlay").click()
        page.wait_for_timeout(1200)
        evidence_state = page.evaluate("() => ({t:document.getElementById('video').currentTime,p:document.getElementById('video').paused})")
        check("evidence_play_starts", not evidence_state["p"], evidence_state)

        roi_text = page.locator("#roiFacts").inner_text()
        external_text = page.locator("#externalFacts").inner_text()
        check("roi_v2_values", all(value in roi_text for value in ("1.667", "6.000", "10.667")), {"text": roi_text})
        check("review_and_dedup_visible", "pending_review" in external_text and "duplicate" in external_text, {"text": external_text})

        capability_cards = page.locator("#capabilities .benchmark").count()
        check("capability_cards_3", capability_cards == 3, {"count": capability_cards})
        external_href = page.locator("#externalEvent").get_attribute("href")
        replay_href = page.locator("#platformReplay").get_attribute("href")
        check("platform_links_present", bool(external_href and replay_href), {"external": external_href, "replay": replay_href})
        media = page.evaluate("""async () => {
          const urls = [document.getElementById('closureVideo').src, document.getElementById('externalClip').src];
          const out = [];
          for (const url of urls) { const r = await fetch(url, {method:'HEAD'}); out.push({url,status:r.status}); }
          return out;
        }""")
        check("saved_media_reachable", all(item["status"] == 200 for item in media), media)

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(800)
        narrow = page.evaluate("() => ({scrollWidth:document.documentElement.scrollWidth,innerWidth:window.innerWidth})")
        page.screenshot(path=str(args.output / "02-narrow.png"))
        check("narrow_layout_no_horizontal_overflow", narrow["scrollWidth"] <= narrow["innerWidth"] + 2, narrow)

        context.close()
        browser.close()
    videos = sorted(args.output.glob("*.webm"))
    if videos:
        videos[0].replace(args.output / "showcase-session.webm")
    result = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "url": args.url,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "recording": "showcase-session.webm" if videos else None,
    }
    (args.output / "browser-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": {item["name"]: item["passed"] for item in checks}}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
