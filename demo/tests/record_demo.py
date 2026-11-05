#!/usr/bin/env python3
"""Record a complete, silent walkthrough of the E00-D demo.

The recording contains on-screen Chinese captions but no synthesized voice.
It is intended to accompany the 3-5 minute narration script, not replace it.

Setup:
    python3 -m pip install --target /tmp/e00d-tools playwright==1.55.0
    PYTHONPATH=/tmp/e00d-tools python3 -m playwright install ffmpeg

Run with the demo server already running:
    PYTHONPATH=/tmp/e00d-tools python3 demo/tests/record_demo.py

Output:
    reports/e00-d/E00-D演示录屏.webm
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = DEMO_DIR.parent
DEFAULT_URL = "http://127.0.0.1:8765/"
DEFAULT_OUT = PROJECT_DIR / "reports" / "e00-d" / "E00-D演示录屏.webm"
CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
]


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("找不到 Chrome。请用 CHROME_PATH 指定可执行文件。")


def server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def set_caption(page, text: str) -> None:
    page.evaluate(
        """(text) => {
            let caption = document.getElementById('recordingCaption');
            if (!caption) {
                caption = document.createElement('div');
                caption.id = 'recordingCaption';
                caption.style.cssText = [
                    'position:fixed',
                    'left:50%',
                    'bottom:26px',
                    'transform:translateX(-50%)',
                    'z-index:9999',
                    'max-width:min(88vw,1100px)',
                    'padding:14px 22px',
                    'border-radius:14px',
                    'background:rgba(7,21,37,.94)',
                    'border:1px solid rgba(94,234,212,.35)',
                    'box-shadow:0 18px 45px rgba(0,0,0,.35)',
                    'color:#fff',
                    'font:700 22px/1.5 "Noto Sans SC","Microsoft YaHei",sans-serif',
                    'text-align:center',
                    'pointer-events:none',
                ].join(';');
                document.body.appendChild(caption);
            }
            caption.textContent = text;
        }""",
        text,
    )


def wait(page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000))


def main() -> int:
    parser = argparse.ArgumentParser(description="录制 E00-D 完整操作演示")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    if not server_alive(args.url):
        raise SystemExit(f"服务未启动：{args.url}\n请先运行 python3 demo/serve.py")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "需要 Playwright。请先安装到独立工具目录，例如：\n"
            "  python3 -m pip install --target /tmp/e00d-tools playwright==1.55.0"
        ) from exc

    out = args.out if args.out.is_absolute() else PROJECT_DIR / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    video_dir = out.parent / "_video_tmp"
    if video_dir.exists():
        shutil.rmtree(video_dir)
    video_dir.mkdir(parents=True)

    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=find_chrome(),
            headless=not args.headed,
            args=["--autoplay-policy=no-user-gesture-required", "--disable-features=Translate"],
        )
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            record_video_dir=str(video_dir),
            record_video_size={"width": args.width, "height": args.height},
        )
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector("#startDemo:not([disabled])", timeout=10000)
        page.wait_for_function(
            "document.getElementById('originalVideo').readyState >= 1 && "
            "document.getElementById('replayVideo').readyState >= 1",
            timeout=10000,
        )
        wait(page, 1.0)

        set_caption(page, "E00-D 公开样例可视化演示｜公开样例 · 离线分析")
        wait(page, 6.0)
        set_caption(page, "1. 首页：说明项目用途；当前只有一个可用公开样例。")
        wait(page, 4.0)

        page.click("#startDemo")
        wait(page, 1.0)
        set_caption(page, "2. 进入对照视图：左侧原视频，右侧带检测框与轨迹编号的分析回放。")
        wait(page, 4.0)

        page.click("#playPause")
        set_caption(page, "3. 播放：两路画面按同一视频时刻同步前进。")
        wait(page, 6.0)
        page.click("#playPause")
        wait(page, 0.8)

        box = page.locator("#timeline").bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.68, box["y"] + box["height"] / 2)
        set_caption(page, "4. 点击进度条可跳转时刻；灰色部分是原视频未参与分析的尾部。")
        wait(page, 4.0)

        page.select_option("#trackSelect", "7")
        wait(page, 1.0)
        set_caption(page, "5. 选择示例目标 007：显示首次出现、最后观察时间、轨迹点数量。")
        wait(page, 5.0)

        box = page.locator("#timeline").bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.56, box["y"] + box["height"] / 2)
        wait(page, 1.0)
        set_caption(page, "6. 黄色线是目标在原始画面中的实际移动路径，绿色点是首次出现位置。")
        wait(page, 6.0)

        page.click('#trackDetail button[data-seek="last"]')
        wait(page, 1.0)
        set_caption(page, "7. 点击“最后观察到”的时间，两路视频同时回到对应证据时刻。")
        wait(page, 5.0)

        page.select_option("#trackSelect", "7")
        wait(page, 0.5)
        page.click('#trackDetail button[data-action="play-clip"]')
        set_caption(page, "8. “播放该目标片段”连续播放该轨迹的观察时段，便于核对。")
        wait(page, 8.0)
        page.click("#playPause")
        wait(page, 0.8)

        page.select_option("#trackSelect", "55")
        wait(page, 1.0)
        set_caption(page, "9. 也可以切换其他编号，例如后段进入的目标 055。")
        wait(page, 5.0)

        page.evaluate(
            "document.getElementById('summaryTitle').scrollIntoView({behavior:'smooth', block:'center'})"
        )
        wait(page, 1.5)
        set_caption(page, "10. 真实结果摘要：82 是累计轨迹编号，不是独立人数。")
        wait(page, 7.0)

        page.evaluate(
            "document.getElementById('capabilityTitle').scrollIntoView({behavior:'smooth', block:'center'})"
        )
        wait(page, 1.5)
        set_caption(page, "11. 本页不含规则；视频理解未接入。规则候选在独立区域规则页展示。")
        wait(page, 7.0)

        page.evaluate("window.scrollTo({top:0, behavior:'smooth'})")
        wait(page, 1.0)
        page.select_option("#trackSelect", "7")
        box = page.locator("#timeline").bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.56, box["y"] + box["height"] / 2)
        page.click("#fullscreenBtn")
        wait(page, 1.0)
        set_caption(page, "12. 全屏模式适合会议投屏；按 Esc 退出。")
        wait(page, 5.0)
        page.evaluate("() => document.exitFullscreen().catch(() => {})")
        wait(page, 0.8)

        page.click("#backHome")
        wait(page, 1.0)
        set_caption(page, "13. 返回首页。全部画面与统计来自已保存的离线运行结果，不现场运行模型。")
        wait(page, 5.0)

        video = page.video
        context.close()
        browser.close()
        recorded_path = Path(video.path()) if video else None

    elapsed = time.monotonic() - started
    if not recorded_path or not recorded_path.exists():
        raise SystemExit("录屏文件未生成。")
    if out.exists():
        out.unlink()
    shutil.move(str(recorded_path), str(out))
    shutil.rmtree(video_dir, ignore_errors=True)
    print(f"录屏已保存：{out}（{out.stat().st_size / 1024 / 1024:.1f} MB，用时 {elapsed:.0f} 秒）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n录屏已取消。", file=sys.stderr)
        sys.exit(130)
