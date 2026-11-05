#!/usr/bin/env python3
"""Browser acceptance checks and evidence screenshots for E00-D.

This script is a local verification helper; it is not part of the runtime demo.
It uses Playwright with the system Google Chrome installation.

Setup once (outside the project dependency lock):
    python3 -m pip install --target /tmp/e00d-tools playwright==1.55.0

Run with the demo server already running:
    PYTHONPATH=/tmp/e00d-tools python3 demo/tests/verify_demo.py \
      --screenshots reports/e00-d

Use --start-server to let the script start/stop its own local server, and
--check-missing to temporarily rename the derived replay and demo data to
verify the real missing-file prompts. Files are restored in finally blocks.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = DEMO_DIR.parent
DEFAULT_URL = "http://127.0.0.1:8765/"
CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
]


def check(condition: bool, label: str, detail: str = "") -> dict:
    result = {"check": label, "ok": bool(condition), "detail": detail}
    print(("PASS  " if condition else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))
    if not condition:
        raise AssertionError(label + (f": {detail}" if detail else ""))
    return result


def server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_server(url: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_alive(url):
            return True
        time.sleep(0.2)
    return False


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("找不到 Chrome。请用 CHROME_PATH 指定可执行文件。")


def media_state(page) -> dict:
    return page.evaluate(
        """() => {
            const o = document.getElementById('originalVideo');
            const r = document.getElementById('replayVideo');
            return {
                originalTime: o.currentTime,
                replayTime: r.currentTime,
                originalPaused: o.paused,
                replayPaused: r.paused,
                originalDuration: o.duration,
                replayDuration: r.duration,
                uiTime: document.getElementById('currentTime').textContent,
                playLabel: document.getElementById('playLabel').textContent,
                syncText: document.getElementById('syncText').textContent,
            };
        }"""
    )


def canvas_alpha_count(page, canvas_id: str) -> int:
    return page.evaluate(
        """(id) => {
            const canvas = document.getElementById(id);
            const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
            let count = 0;
            for (let index = 3; index < data.length; index += 4) {
                if (data[index] > 0) {
                    count += 1;
                    if (count > 5000) return count;
                }
            }
            return count;
        }""",
        canvas_id,
    )


def click_timeline_ratio(page, ratio: float) -> None:
    box = page.locator("#timeline").bounding_box()
    if not box:
        raise AssertionError("timeline has no bounding box")
    page.mouse.click(box["x"] + box["width"] * ratio, box["y"] + box["height"] / 2)
    page.wait_for_timeout(450)


def open_demo(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_selector("#startDemo:not([disabled])", timeout=10000)
    page.click("#startDemo")
    page.wait_for_function(
        "document.getElementById('originalVideo').readyState >= 1 && "
        "document.getElementById('replayVideo').readyState >= 1",
        timeout=10000,
    )
    page.wait_for_timeout(500)


def main() -> int:
    parser = argparse.ArgumentParser(description="E00-D 浏览器验收与截图")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--screenshots", type=Path, default=None, help="截图输出目录")
    parser.add_argument("--start-server", action="store_true", help="若服务未启动则自动启动")
    parser.add_argument("--check-missing", action="store_true", help="临时改名文件，验证缺失提示")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "需要 Playwright。请先安装到独立工具目录，例如：\n"
            "  python3 -m pip install --target /tmp/e00d-tools playwright==1.55.0\n"
            "然后以 PYTHONPATH=/tmp/e00d-tools 运行本脚本。"
        ) from exc

    screenshot_dir = args.screenshots
    if screenshot_dir:
        screenshot_dir = screenshot_dir if screenshot_dir.is_absolute() else PROJECT_DIR / screenshot_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    server_process = None
    if not server_alive(args.url):
        if not args.start_server:
            raise SystemExit(f"服务未启动：{args.url}\n请先运行 python3 demo/serve.py")
        port = str(int(args.url.rstrip("/").rsplit(":", 1)[-1]))
        server_process = subprocess.Popen(
            [sys.executable, str(DEMO_DIR / "serve.py"), "--port", port],
            cwd=str(PROJECT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_for_server(args.url):
            server_process.terminate()
            raise SystemExit("自动启动演示服务失败。")

    results: list[dict] = []
    chrome = find_chrome()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    replay_path = DEMO_DIR / "media" / "analysis_replay.mp4"
    replay_backup = replay_path.with_suffix(".mp4.verify-backup")
    data_path = DEMO_DIR / "data" / "demo.json"
    data_backup = data_path.with_suffix(".json.verify-backup")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chrome,
                headless=not args.headed,
                args=["--autoplay-policy=no-user-gesture-required", "--disable-features=Translate"],
            )
            context = browser.new_context(viewport={"width": 1600, "height": 900})
            page = context.new_page()
            console_errors: list[str] = []
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: console_errors.append(str(error)))

            page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#startDemo:not([disabled])", timeout=10000)
            results.append(
                check(
                    "时空记忆智能体" in page.title(),
                    "首页标题和项目名称可见",
                    page.title(),
                )
            )
            results.append(check(page.locator("#homeView").is_visible(), "首页可见"))
            results.append(
                check(
                    page.locator("#homeTrackCount").inner_text() == "82",
                    "首页显示 82 个累计轨迹编号",
                )
            )
            results.append(
                check(
                    "不是独立人数" in page.locator(".sample-boundary").inner_text(),
                    "首页明确 82 不是独立人数",
                )
            )
            results.append(
                check(
                    page.locator("#homeDisabled").inner_text().count("未") >= 3,
                    "首页显示未启用能力",
                )
            )
            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "01-首页.png"), full_page=True)

            page.click("#startDemo")
            page.wait_for_function(
                "document.getElementById('originalVideo').readyState >= 1 && "
                "document.getElementById('replayVideo').readyState >= 1",
                timeout=10000,
            )
            page.wait_for_timeout(600)
            state = media_state(page)
            results.append(
                check(
                    abs(state["originalDuration"] - 13.64) < 0.05,
                    "原视频时长 13.64 秒",
                    f"{state['originalDuration']:.2f}",
                )
            )
            results.append(
                check(
                    abs(state["replayDuration"] - 12.0) < 0.05,
                    "分析回放时长 12.00 秒",
                    f"{state['replayDuration']:.2f}",
                )
            )
            results.append(check(page.locator("#missingBanner").is_hidden(), "正常文件无缺失提示"))
            results.append(
                check(
                    page.locator("#trackSelect").input_value() == "7",
                    "默认预选示例目标 007",
                )
            )

            page.click("#playPause")
            page.wait_for_timeout(2400)
            playing = media_state(page)
            results.append(
                check(
                    playing["originalTime"] > 1.8 and playing["replayTime"] > 1.8,
                    "点击播放后两路视频均前进",
                    f"{playing['originalTime']:.2f}/{playing['replayTime']:.2f}",
                )
            )
            results.append(
                check(
                    abs(playing["originalTime"] - playing["replayTime"]) < 0.15,
                    "播放中两路时间同步",
                    f"差 {abs(playing['originalTime'] - playing['replayTime']):.3f} 秒",
                )
            )
            results.append(check(playing["syncText"] == "两路同步", "同步状态显示“两路同步”"))
            page.wait_for_timeout(1200)
            page.click("#playPause")
            page.wait_for_timeout(350)
            paused = media_state(page)
            results.append(
                check(paused["originalPaused"] and paused["replayPaused"], "暂停后两路均停止")
            )
            if screenshot_dir:
                page.wait_for_timeout(500)
                page.screenshot(path=str(screenshot_dir / "02-对照播放.png"))

            click_timeline_ratio(page, 0.50)
            seeked = media_state(page)
            results.append(
                check(
                    5.4 <= seeked["originalTime"] <= 6.7 and abs(seeked["originalTime"] - seeked["replayTime"]) < 0.15,
                    "点击进度条可跳转并保持同步",
                    f"{seeked['originalTime']:.2f}/{seeked['replayTime']:.2f}",
                )
            )
            results.append(
                check(
                    abs(float(seeked["uiTime"]) - seeked["originalTime"]) < 0.15,
                    "时间读数与视频时间一致",
                    seeked["uiTime"],
                )
            )

            page.select_option("#trackSelect", "7")
            page.wait_for_timeout(600)
            selected = media_state(page)
            results.append(
                check(
                    abs(selected["originalTime"]) < 0.08 and abs(selected["replayTime"]) < 0.08,
                    "选择目标 007 后定位到首次出现 0.00 秒",
                )
            )
            results.append(
                check(
                    "007" in page.locator("#trackDetail").inner_text()
                    and "9.48 秒" in page.locator("#trackDetail").inner_text(),
                    "轨迹详情显示目标编号和出现时间",
                )
            )
            results.append(
                check(
                    canvas_alpha_count(page, "originalOverlay") > 500,
                    "原视频叠加了真实轨迹路径",
                )
            )
            click_timeline_ratio(page, 0.46)
            page.wait_for_timeout(500)
            results.append(
                check(
                    canvas_alpha_count(page, "originalOverlay") > 2000,
                    "移动到中段后路径可见",
                )
            )
            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "03-轨迹路径.png"))

            page.select_option("#trackSelect", "55")
            page.wait_for_timeout(500)
            results.append(
                check(
                    abs(media_state(page)["originalTime"] - 1.52) < 0.08,
                    "选择后段进入的目标 055 会定位到 1.52 秒",
                )
            )
            page.click('#trackDetail button[data-seek="last"]')
            page.wait_for_timeout(500)
            evidence = media_state(page)
            results.append(
                check(
                    abs(evidence["originalTime"] - 11.96) < 0.12
                    and abs(evidence["replayTime"] - 11.96) < 0.12,
                    "点击“最后观察到”可定位视频证据",
                    f"{evidence['originalTime']:.2f}/{evidence['replayTime']:.2f}",
                )
            )
            results.append(
                check(
                    "已定位到目标 055" in page.locator("#toast").inner_text(),
                    "证据定位有明确中文提示",
                )
            )
            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "04-证据定位.png"))

            page.select_option("#trackSelect", "7")
            page.wait_for_timeout(300)
            page.click('#trackDetail button[data-action="play-clip"]')
            page.wait_for_timeout(1300)
            clip = media_state(page)
            results.append(
                check(
                    clip["originalTime"] > 0.8 and not clip["originalPaused"],
                    "“播放该目标片段”可播放选中轨迹",
                    f"{clip['originalTime']:.2f}",
                )
            )
            page.click("#playPause")
            page.wait_for_timeout(250)

            page.click("#timelineTail")
            page.wait_for_timeout(350)
            results.append(
                check(
                    "未参与分析" in page.locator("#toast").inner_text(),
                    "点击未分析尾部会说明 1.64 秒边界",
                )
            )
            page.wait_for_function(
                "document.getElementById('toast').hidden === true", timeout=6000
            )

            page.select_option("#trackSelect", "7")
            click_timeline_ratio(page, 0.46)
            page.click("#fullscreenBtn")
            page.wait_for_timeout(600)
            results.append(
                check(
                    page.evaluate("() => document.fullscreenElement?.id || null") == "playerCard",
                    "全屏按钮进入播放器全屏",
                )
            )
            if screenshot_dir:
                page.screenshot(path=str(screenshot_dir / "05-全屏模式.png"))
            page.evaluate("() => document.exitFullscreen().catch(() => {})")
            page.wait_for_timeout(500)
            results.append(
                check(
                    page.evaluate("() => document.fullscreenElement?.id || null") is None,
                    "退出全屏后恢复页面",
                )
            )

            results.append(
                check(not console_errors, "浏览器控制台无错误", "; ".join(console_errors[:3]))
            )
            page.screenshot(path=str(screenshot_dir / "02b-主视图完整.png"), full_page=True) if screenshot_dir else None
            context.close()

            if args.check_missing:
                check_context = browser.new_context(viewport={"width": 1600, "height": 900})
                check_page = check_context.new_page()
                try:
                    replay_path.rename(replay_backup)
                    check_page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
                    check_page.wait_for_selector("#startDemo:not([disabled])", timeout=10000)
                    check_page.click("#startDemo")
                    check_page.wait_for_timeout(1200)
                    results.append(
                        check(
                            not check_page.locator("#missingBanner").is_hidden(),
                            "回放缺失时显示缺失提示条",
                        )
                    )
                    results.append(
                        check(
                            "analysis_replay.mp4" in check_page.locator("#missingBanner").inner_text(),
                            "缺失提示包含实际文件路径",
                        )
                    )
                    results.append(
                        check(
                            check_page.locator("#playPause").is_disabled(),
                            "回放缺失时禁用播放",
                        )
                    )
                    results.append(
                        check(
                            not check_page.locator("#replayError").is_hidden(),
                            "回放画面显示内嵌错误提示",
                        )
                    )
                    if screenshot_dir:
                        check_page.screenshot(path=str(screenshot_dir / "06-缺失文件提示.png"))
                finally:
                    if replay_backup.exists():
                        replay_backup.rename(replay_path)
                    check_context.close()

                data_context = browser.new_context(viewport={"width": 1600, "height": 900})
                data_page = data_context.new_page()
                try:
                    data_path.rename(data_backup)
                    data_page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
                    data_page.wait_for_timeout(1000)
                    results.append(
                        check(
                            not data_page.locator("#fatalError").is_hidden(),
                            "演示数据缺失时显示全屏错误提示",
                        )
                    )
                    results.append(
                        check(
                            data_page.locator("#startDemo").is_disabled(),
                            "演示数据缺失时禁用开始按钮",
                        )
                    )
                    if screenshot_dir:
                        data_page.screenshot(path=str(screenshot_dir / "07-数据缺失提示.png"))
                finally:
                    if data_backup.exists():
                        data_backup.rename(data_path)
                    data_context.close()

            browser.close()
    finally:
        if replay_backup.exists():
            replay_backup.rename(replay_path)
        if data_backup.exists():
            data_backup.rename(data_path)
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()

    summary = {
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": args.url,
        "chrome": chrome,
        "checks_passed": len(results),
        "checks_failed": 0,
        "checks": results,
    }
    if screenshot_dir:
        with (screenshot_dir / "verify-result.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(f"\n全部 {len(results)} 项检查通过。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\n验收失败：{exc}", file=sys.stderr)
        sys.exit(1)
