#!/usr/bin/env python3
"""Capture the platform Explore UI for a bridged external event."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"event_id": args.event_id, "captured_at": datetime.now().astimezone().isoformat()}
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
        page.goto(
            f"http://127.0.0.1:15000/explore?event_id={args.event_id}",
            wait_until="domcontentloaded", timeout=30000,
        )
        page.wait_for_timeout(4000)
        result["card_text"] = page.locator("body").inner_text()[:500]
        page.screenshot(path=str(args.output / "01-event-card.png"))
        page.mouse.click(230, 220)
        page.wait_for_timeout(1500)
        result["detail_text"] = page.locator("body").inner_text()[-2500:]
        page.screenshot(path=str(args.output / "02-event-detail.png"))
        page.get_by_text("追踪详情", exact=True).click()
        page.wait_for_timeout(2500)
        state = page.evaluate(
            """() => {const v=document.querySelector('video'); return v ? {
              currentTime:v.currentTime, duration:v.duration, paused:v.paused,
              readyState:v.readyState, videoWidth:v.videoWidth, videoHeight:v.videoHeight,
              error:v.error?v.error.message:null} : null}"""
        )
        result["video_first"] = state
        result["tracking_text"] = page.locator("body").inner_text()[-1200:]
        page.screenshot(path=str(args.output / "03-tracking-playing.png"))
        page.wait_for_timeout(3000)
        result["video_after_3s"] = page.evaluate(
            """() => {const v=document.querySelector('video'); return v ? {
              currentTime:v.currentTime, duration:v.duration, paused:v.paused,
              readyState:v.readyState} : null}"""
        )
        page.screenshot(path=str(args.output / "04-tracking-advanced.png"))
        context.close()
        browser.close()
    videos = sorted(args.output.glob("*.webm"))
    if videos:
        videos[0].replace(args.output / "explore-session.webm")
        result["session_video"] = "explore-session.webm"
    (args.output / "explore_playback.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: result.get(k) for k in ("event_id", "video_first", "video_after_3s", "session_video")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
