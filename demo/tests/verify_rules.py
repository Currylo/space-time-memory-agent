#!/usr/bin/env python3
"""Browser acceptance and screenshots for the E04 zone/rule demo.

Setup once (outside the project dependency lock):
    python3 -m pip install --target /tmp/e04-tools playwright==1.55.0

Run with the demo server already running:
    PYTHONPATH=/tmp/e04-tools/lib python3 demo/tests/verify_rules.py \
      --screenshots reports/e04

Use --start-server to let the script start/stop its own local server, and
--check-missing to temporarily rename demo/data/rules.json and verify the
real error prompt. Files are restored in finally blocks.
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


def canvas_alpha_count(page, canvas_id: str) -> int:
    return page.evaluate(
        """(id) => {
            const canvas = document.getElementById(id);
            const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
            let count = 0;
            for (let index = 3; index < data.length; index += 4) {
                if (data[index] > 0) {
                    count += 1;
                    if (count > 500000) return count;
                }
            }
            return count;
        }""",
        canvas_id,
    )


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


def zone_card_counts(page) -> list[int]:
    values = page.locator("#zoneStatCards .zone-stat-value strong").all_inner_texts()
    return [int(value.replace(",", "")) for value in values]


def clean_for_screenshot(page) -> None:
    page.evaluate("() => { window.scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); }")
    page.wait_for_timeout(150)


def click_timeline_ratio(page, ratio: float) -> None:
    box = page.locator("#timeline").bounding_box()
    if not box:
        raise AssertionError("timeline has no bounding box")
    page.mouse.click(box["x"] + box["width"] * ratio, box["y"] + box["height"] / 2)
    page.wait_for_timeout(500)


def open_rules(page, url: str) -> None:
    page.goto(url.rstrip("/") + "/rules.html", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_selector(".candidate-card", timeout=12000)
    page.wait_for_function(
        "document.getElementById('originalVideo').readyState >= 1 && "
        "document.getElementById('replayVideo').readyState >= 1",
        timeout=12000,
    )
    page.wait_for_timeout(500)


def main() -> int:
    parser = argparse.ArgumentParser(description="E04 区域与规则演示浏览器验收")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--screenshots", type=Path, default=None, help="截图输出目录")
    parser.add_argument("--start-server", action="store_true", help="若服务未启动则自动启动")
    parser.add_argument("--check-missing", action="store_true", help="临时改名规则数据验证缺失提示")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "需要 Playwright。请先安装到独立工具目录，例如：\n"
            "  python3 -m pip install --target /tmp/e04-tools playwright==1.55.0\n"
            "然后以 PYTHONPATH=/tmp/e04-tools/lib 运行本脚本。"
        ) from exc

    screenshot_dir = args.screenshots
    if screenshot_dir:
        screenshot_dir = screenshot_dir if screenshot_dir.is_absolute() else PROJECT_DIR / screenshot_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    rules_data = json.loads((DEMO_DIR / "data" / "rules.json").read_text(encoding="utf-8"))
    zone_times = rules_data["zone_stats"]["times"]
    zones = rules_data["zones"]

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
    rules_path = DEMO_DIR / "data" / "rules.json"
    rules_backup = rules_path.with_suffix(".json.verify-backup")

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

            # Home entry keeps the original E00-D track demo and adds the E04 mode.
            page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#startDemo:not([disabled])", timeout=10000)
            results.append(check(page.locator("#startRuleDemo").count() == 1, "首页保留轨迹演示并新增区域规则入口"))
            results.append(
                check(
                    page.locator("#startRuleDemo").get_attribute("href") == "rules.html",
                    "区域规则入口指向独立页面",
                )
            )
            results.append(
                check(
                    page.locator("#homeDisabled").inner_text().count("未") >= 3,
                    "首页能力状态仍明确标出未完成项",
                )
            )
            if screenshot_dir:
                clean_for_screenshot(page)
                page.screenshot(path=str(screenshot_dir / "01-首页双入口.png"), full_page=True)

            open_rules(page, args.url)
            results.append(check("区域统计与规则证据" in page.title(), "规则页标题正确", page.title()))
            results.append(check(page.locator("#fatalError").is_hidden(), "规则页无全屏错误"))
            results.append(check(page.locator("#missingBanner").is_hidden(), "规则页无文件缺失提示"))
            results.append(check(page.locator(".zone-stat").count() == 3, "显示 3 个演示区域"))
            results.append(check(page.locator(".rule-item").count() == 3, "显示 3 条规则"))
            results.append(
                check(
                    page.locator(".candidate-card").count() == len(rules_data["candidates"]),
                    f"显示 {len(rules_data['candidates'])} 条停留候选",
                )
            )
            results.append(
                check(
                    all(
                        "规则条件触发，待人工复核" in card
                        for card in page.locator(".candidate-status").all_inner_texts()
                    ),
                    "所有候选统一标注待人工复核",
                )
            )
            results.append(
                check(
                    page.locator("[data-rule-id='reversal']").inner_text().count("0 条候选") >= 1
                    and "077" in page.locator("[data-rule-id='reversal']").inner_text(),
                    "折返规则如实显示 0 条并给出最接近目标",
                )
            )
            results.append(
                check(
                    "观察时长不足" in page.locator("[data-rule-id='occupancy_change_60s']").inner_text()
                    and "60" in page.locator("[data-rule-id='occupancy_change_60s']").inner_text(),
                    "60 秒规则显示观察时长不足",
                )
            )
            results.append(check(canvas_alpha_count(page, "originalOverlay") > 20000, "原视频绘制区域轮廓"))
            results.append(check(canvas_alpha_count(page, "zoneChart") > 50000, "区域统计曲线已绘制"))
            if screenshot_dir:
                clean_for_screenshot(page)
                page.screenshot(path=str(screenshot_dir / "02-区域统计与规则.png"), full_page=True)

            # Stats at t=0 match the generated data.
            initial_counts = zone_card_counts(page)
            expected_initial = [zone["counts"][0] for zone in zones]
            results.append(
                check(
                    initial_counts == expected_initial,
                    "初始区域观测数与数据一致",
                    f"{initial_counts}",
                )
            )

            # Play 2 seconds: both videos advance and statistics update.
            page.click("#playPause")
            page.wait_for_timeout(2200)
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
            page.click("#playPause")
            page.wait_for_timeout(300)

            # Jump to 6 s and compare all three counts.
            click_timeline_ratio(page, 0.50)
            seeked = media_state(page)
            index = min(range(len(zone_times)), key=lambda i: abs(zone_times[i] - seeked["originalTime"]))
            expected = [zone["counts"][index] for zone in zones]
            actual = zone_card_counts(page)
            results.append(
                check(
                    5.5 <= seeked["originalTime"] <= 6.5,
                    "点击进度条跳到约 6 秒",
                    f"{seeked['originalTime']:.2f}",
                )
            )
            results.append(
                check(actual == expected, "6 秒处三个区域观测数正确", f"{actual} / {expected}")
            )

            # Select the first candidate and verify evidence alignment.
            first_candidate = rules_data["candidates"][0]
            page.locator(".candidate-card").first.click()
            page.wait_for_timeout(600)
            selected = media_state(page)
            results.append(
                check(
                    abs(selected["originalTime"] - first_candidate["evidence_frame_video_time"]) < 0.08,
                    "点击候选定位到证据帧时刻",
                    f"{selected['originalTime']:.2f}",
                )
            )
            detail_text = page.locator("#candidateDetailBody").inner_text()
            results.append(
                check(
                    first_candidate["candidate_id"] in detail_text
                    and first_candidate["rule_version"] in detail_text
                    and "证据帧时刻" in detail_text
                    and "模拟触发视频时刻" in detail_text
                    and "实际写出墙钟时间" in detail_text
                    and "规则条件触发，待人工复核" in detail_text,
                    "候选详情显示规则版本、证据帧/触发/写出时间和复核状态",
                )
            )
            results.append(
                check(
                    page.locator(".candidate-card.is-active").count() == 1
                    and page.locator(".candidate-marker.is-active").count() == 1,
                    "候选卡和候选标记同步高亮",
                )
            )
            results.append(check(canvas_alpha_count(page, "originalOverlay") > 30000, "证据时刻绘制目标框和轨迹"))
            if screenshot_dir:
                clean_for_screenshot(page)
                page.screenshot(path=str(screenshot_dir / "03-候选证据.png"), full_page=True)

            page.click('[data-detail-action="play-evidence"]')
            page.wait_for_timeout(1300)
            evidence_play = media_state(page)
            results.append(
                check(
                    evidence_play["originalTime"] > first_candidate["evidence_clip"]["start_video_time"]
                    and not evidence_play["originalPaused"],
                    "可播放候选触发前后证据片段",
                    f"{evidence_play['originalTime']:.2f}",
                )
            )
            page.click("#playPause")
            page.wait_for_timeout(250)

            # Rule toggles hide/show their own outputs independently.
            page.locator('[data-rule-toggle="long_stay"]').uncheck()
            page.wait_for_timeout(350)
            results.append(
                check(
                    page.locator(".candidate-card").count() == 0
                    and page.locator("#candidateCount").inner_text() == "0",
                    "关闭停留规则后候选列表清空",
                )
            )
            results.append(
                check(
                    "已关闭" in page.locator("[data-rule-id='long_stay'] .rule-status").inner_text(),
                    "关闭停留规则后状态明确",
                )
            )
            if screenshot_dir:
                clean_for_screenshot(page)
                page.screenshot(path=str(screenshot_dir / "04-规则关闭.png"), full_page=True)
            page.locator('[data-rule-toggle="long_stay"]').check()
            page.wait_for_timeout(350)
            results.append(
                check(
                    page.locator(".candidate-card").count() == len(rules_data["candidates"]),
                    "重新打开停留规则后候选恢复",
                )
            )
            page.locator('[data-rule-toggle="reversal"]').uncheck()
            page.locator('[data-rule-toggle="long_stay"]').uncheck()
            page.locator('[data-rule-toggle="occupancy_change_60s"]').uncheck()
            page.wait_for_timeout(350)
            results.append(
                check(
                    "所有规则均已关闭" in page.locator("#candidateList").inner_text(),
                    "规则全部关闭时显示明确空状态",
                )
            )
            page.locator('[data-rule-toggle="long_stay"]').check()
            page.locator('[data-rule-toggle="reversal"]').check()
            page.locator('[data-rule-toggle="occupancy_change_60s"]').check()
            page.wait_for_timeout(300)

            # Chart click and marker click both seek.
            chart = page.locator("#zoneChart").bounding_box()
            if not chart:
                raise AssertionError("zone chart has no bounding box")
            page.mouse.click(chart["x"] + chart["width"] * 0.75, chart["y"] + chart["height"] / 2)
            page.wait_for_timeout(450)
            chart_time = media_state(page)["originalTime"]
            results.append(
                check(8.4 <= chart_time <= 9.6, "点击区域曲线可定位到对应时刻", f"{chart_time:.2f}")
            )
            page.locator(".candidate-marker").first.click()
            page.wait_for_timeout(450)
            marker_time = media_state(page)["originalTime"]
            results.append(
                check(
                    abs(marker_time - first_candidate["evidence_frame_video_time"]) < 0.08,
                    "点击时间轴候选标记可回看证据帧",
                    f"{marker_time:.2f}",
                )
            )

            results.append(check(not console_errors, "浏览器控制台无错误", "; ".join(console_errors[:3])))
            context.close()

            # Regression: the original E00-D track demo still starts and selects target 007.
            regression_context = browser.new_context(viewport={"width": 1600, "height": 900})
            regression_page = regression_context.new_page()
            regression_page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            regression_page.wait_for_selector("#startDemo:not([disabled])", timeout=10000)
            regression_page.click("#startDemo")
            regression_page.wait_for_function(
                "document.getElementById('originalVideo').readyState >= 1 && "
                "document.getElementById('replayVideo').readyState >= 1",
                timeout=10000,
            )
            regression_page.wait_for_timeout(500)
            results.append(
                check(
                    regression_page.locator("#trackSelect").input_value() == "7"
                    and regression_page.locator("#trackDetail").inner_text().count("007") >= 1,
                    "原 E00-D 轨迹演示回归：默认目标 007",
                )
            )
            regression_context.close()

            if args.check_missing:
                missing_context = browser.new_context(viewport={"width": 1600, "height": 900})
                missing_page = missing_context.new_page()
                try:
                    rules_path.rename(rules_backup)
                    missing_page.goto(
                        args.url.rstrip("/") + "/rules.html",
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    missing_page.wait_for_timeout(1200)
                    results.append(
                        check(
                            not missing_page.locator("#fatalError").is_hidden(),
                            "规则数据缺失时显示全屏错误",
                        )
                    )
                    results.append(
                        check(
                            "rules.json" in missing_page.locator("#fatalDetail").inner_text(),
                            "缺失提示包含 rules.json",
                        )
                    )
                    if screenshot_dir:
                        missing_page.screenshot(path=str(screenshot_dir / "05-规则数据缺失.png"))
                finally:
                    if rules_backup.exists():
                        rules_backup.rename(rules_path)
                    missing_context.close()

            browser.close()
    finally:
        if rules_backup.exists():
            rules_backup.rename(rules_path)
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
