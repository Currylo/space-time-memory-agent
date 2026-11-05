#!/usr/bin/env python3
"""Record a short single-case demo: self candidate -> platform event -> video -> review."""
from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import shutil
import sys

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--candidate-track", type=int, required=True)
    parser.add_argument("--candidate-start", type=float, required=True)
    parser.add_argument("--candidate-duration", type=float, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.preview, args.output / "candidate.jpg")
    key = f"a2-long-stay:track={args.candidate_track}:src={args.candidate_start:.3f}:zone=a1_demo_zone"
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>A2 外接候选单案例</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:30px auto;color:#222}}
table{{border-collapse:collapse;margin:16px 0}}td,th{{border:1px solid #bbb;padding:8px 12px;text-align:left}}
img{{max-width:640px;border:1px solid #555}}code{{background:#f2f2f2;padding:2px 5px}}</style></head><body>
<h1>区域停留候选 → 平台事件 → 视频证据 → 人工复核</h1>
<p>自研候选（公开样例流程检查，不是现场 GT）</p>
<table><tr><th>候选键</th><td><code>{escape(key)}</code></td></tr>
<tr><th>轨迹</th><td>{args.candidate_track}</td></tr>
<tr><th>源视频时间</th><td>{args.candidate_start:.3f} s</td></tr>
<tr><th>停留时长</th><td>{args.candidate_duration:.3f} s</td></tr>
<tr><th>平台事件</th><td><code>{escape(args.event_id)}</code></td></tr></table>
<img src="candidate.jpg" alt="candidate preview">
<p>下一步：打开平台事件详情，播放追踪视频并查看人工复核记录。</p>
</body></html>"""
    (args.output / "candidate.html").write_text(html, encoding="utf-8")
    result = {"event_id": args.event_id, "candidate_key": key, "recorded_at": datetime.now().astimezone().isoformat()}
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
        page.goto((args.output / "candidate.html").resolve().as_uri(), wait_until="load", timeout=30000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(args.output / "01-self-candidate.png"))
        page.goto(
            f"http://127.0.0.1:15000/explore?event_id={args.event_id}",
            wait_until="domcontentloaded", timeout=30000,
        )
        page.wait_for_timeout(3500)
        page.screenshot(path=str(args.output / "02-platform-event-card.png"))
        page.mouse.click(230, 220)
        page.wait_for_timeout(1500)
        result["detail_text"] = page.locator("body").inner_text()[-1800:]
        page.screenshot(path=str(args.output / "03-platform-event-detail.png"))
        page.get_by_text("追踪详情", exact=True).click()
        page.wait_for_timeout(2000)
        result["video_first"] = page.evaluate(
            """() => {const v=document.querySelector('video'); return v ? {
              currentTime:v.currentTime, duration:v.duration, paused:v.paused,
              readyState:v.readyState, videoWidth:v.videoWidth, videoHeight:v.videoHeight,
              error:v.error?v.error.message:null} : null}"""
        )
        page.screenshot(path=str(args.output / "04-platform-video-playing.png"))
        page.wait_for_timeout(5000)
        result["video_after_5s"] = page.evaluate(
            """() => {const v=document.querySelector('video'); return v ? {
              currentTime:v.currentTime, duration:v.duration, paused:v.paused,
              readyState:v.readyState} : null}"""
        )
        page.screenshot(path=str(args.output / "05-platform-video-advanced.png"))
        context.close()
        browser.close()
    videos = sorted(args.output.glob("*.webm"))
    if videos:
        videos[0].replace(args.output / "case-closure.webm")
        result["recording"] = "case-closure.webm"
    (args.output / "case-closure.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("event_id", "candidate_key", "video_first", "video_after_5s", "recording")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
