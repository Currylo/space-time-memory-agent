#!/usr/bin/env python3
"""Independent browser acceptance for the current same-source page.

The real page (8765) is checked without submitting a review.  A separate test
server (8766) with its own ledger and platform JSON is used only for the
submit/refresh/history flow; the real ledger is checked afterwards.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
from playwright.sync_api import sync_playwright  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_url(url: str):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/showcase.html")
    parser.add_argument("--test-url", default="http://127.0.0.1:8766/showcase.html")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    console_real: list[str] = []
    console_test: list[str] = []
    page_errors_real: list[str] = []
    page_errors_test: list[str] = []

    def check(name: str, passed: bool, detail=None):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    def wait_ready(page, timeout=25000):
        page.wait_for_function(
            "() => document.getElementById('platformSummary') && !document.getElementById('platformSummary').textContent.includes('读取中')",
            timeout=timeout,
        )
        page.wait_for_function(
            "() => document.getElementById('play') && !document.getElementById('play').disabled",
            timeout=timeout,
        )

    def video_state(page):
        return page.evaluate(
            """() => {const v=document.getElementById('sceneVideo'); return {
              t:v.currentTime, paused:v.paused, duration:v.duration, readyState:v.readyState,
              error:v.error?v.error.message:null};}"""
        )

    def seek_to(page, value):
        page.locator("#seek").evaluate(
            "(el,value)=>{el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}",
            value,
        )
        page.wait_for_timeout(450)

    def screenshot_full(page, path):
        page.evaluate("() => window.scrollTo(0,0)")
        page.wait_for_timeout(200)
        page.screenshot(path=path, full_page=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=args.chrome,
            args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            record_video_dir=str(args.output),
            record_video_size={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.on("console", lambda message: console_real.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors_real.append(str(error)))
        page.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(page)
        page.wait_for_timeout(1200)
        screenshot_full(page, str(args.output / "01-initial.png"))

        check("real_page_loads_without_error_banner", page.locator("#error").is_hidden(), {"error": page.locator("#error").inner_text() if not page.locator("#error").is_hidden() else ""})
        summary = page.locator("#platformSummary").inner_text()
        clock_text = page.locator("#platformClockText").inner_text()
        check("same_source_platform_banner", "18 条平台候选事件" in summary and "18 条录像窗口已核验" in summary, {"summary": summary})
        check("clock_alignment_verified", "对齐已核验" in clock_text and "数值残差" in clock_text and "帧周期 0.04" in clock_text, {"clock": clock_text})
        check("candidate_count_18", page.locator("#candidateList button").count() == 18, {"count": page.locator("#candidateList button").count()})

        # play / pause
        before = video_state(page)["t"]
        page.locator("#play").click()
        page.wait_for_timeout(1600)
        playing = video_state(page)
        page.screenshot(path=str(args.output / "02-playing.png"))
        check("play_advances", (not playing["paused"]) and playing["t"] > before + 0.5, {"before": before, "after": playing})
        page.locator("#play").click()
        page.wait_for_timeout(350)
        paused = video_state(page)
        pause_t1 = paused["t"]
        page.wait_for_timeout(900)
        pause_t2 = video_state(page)["t"]
        page.screenshot(path=str(args.output / "03-paused.png"))
        check("pause_holds", paused["paused"] and abs(pause_t1 - pause_t2) < 0.05, {"t1": pause_t1, "t2": pause_t2})

        # seek and speed
        seek_to(page, 30)
        state_30 = video_state(page)
        check("seek_30s", abs(state_30["t"] - 30.0) < 0.3, state_30)
        page.locator("#speed").select_option("2")
        check("speed_2x", abs(page.evaluate("() => document.getElementById('sceneVideo').playbackRate") - 2.0) < 1e-6)
        page.locator("#speed").select_option("1")

        # fullscreen (click via DOM to avoid a headless pointer-actionability quirk)
        page.evaluate("() => document.getElementById('fullscreen').click()")
        page.wait_for_timeout(700)
        fullscreen_on = page.evaluate("() => !!document.fullscreenElement")
        if fullscreen_on:
            page.evaluate("() => document.getElementById('fullscreen').click()")
            page.wait_for_timeout(300)
        check("fullscreen_button", fullscreen_on, {"fullscreen": fullscreen_on, "note": "headless browser must support the Fullscreen API"})

        # original/analysis switch preserves position
        seek_to(page, 42.5)
        before_switch = video_state(page)["t"]
        page.locator("#originalMode").click()
        page.wait_for_function("() => document.getElementById('mediaMessage').hidden && !document.getElementById('play').disabled", timeout=25000)
        page.wait_for_timeout(500)
        original_state = video_state(page)
        page.screenshot(path=str(args.output / "04-original-mode.png"))
        check("original_mode_preserves_position", abs(original_state["t"] - before_switch) < 0.35, {"before": before_switch, "after": original_state})
        page.locator("#analysisMode").click()
        page.wait_for_function("() => document.getElementById('mediaMessage').hidden && !document.getElementById('play').disabled", timeout=25000)
        page.wait_for_timeout(500)
        analysis_state = video_state(page)
        check("analysis_mode_preserves_position", abs(analysis_state["t"] - before_switch) < 0.35, {"before": before_switch, "after": analysis_state})

        # 60 s observation state
        seek_to(page, 30)
        before_60 = page.locator("#crowdStatus").inner_text()
        seek_to(page, 65)
        after_60 = page.locator("#crowdStatus").inner_text()
        check("crowd_observation_not_ready_before_60s", "观察未满 60 秒" in before_60, {"text": before_60})
        check("crowd_full_window_after_60s", "完整 60 秒窗口" in after_60, {"text": after_60})

        # pose insufficiency
        data = load_json_url("http://127.0.0.1:8765/data/unified.json")
        no_pose_time = next((frame["time"] for frame in data["frames"] if frame["pose_valid"] == 0 and frame["tracked"] > 0), None)
        pose_time = next((frame["time"] for frame in data["frames"] if frame["pose_valid"] > 0 and frame["tracked"] > 0), None)
        if no_pose_time is not None:
            seek_to(page, no_pose_time)
            no_pose_text = page.locator("#fallStatus").inner_text()
            check("pose_insufficient_is_explicit", "姿态不足" in no_pose_text, {"time": no_pose_time, "text": no_pose_text})
        if pose_time is not None:
            seek_to(page, pose_time)
            pose_text = page.locator("#fallStatus").inner_text()
            check("pose_available_does_not_say_normal", ("当前未触发" in pose_text) and ("正常" not in pose_text), {"time": pose_time, "text": pose_text})

        # empty fall result
        page.locator("#filters button[data-kind='fall']").click()
        page.wait_for_timeout(400)
        empty_text = page.locator("#candidateList .empty").inner_text()
        check("fall_empty_result_is_explicit", "没有摔倒提示线索" in empty_text and "规则已实际执行" in empty_text, {"text": empty_text})
        page.locator("#filters button[data-kind='all']").click()
        page.wait_for_timeout(300)

        # stay candidate and platform evidence
        page.locator("#candidateList button").first.click()
        page.wait_for_timeout(900)
        check("stay_detail_visible", page.locator("#platformDetail").is_visible(), {})
        stay_title = page.locator("#platformCandidateTitle").inner_text()
        stay_badge = page.locator("#platformStatusBadge").inner_text()
        stay_rule = page.locator("#platformRule").inner_text()
        stay_window = page.locator("#platformWindow").inner_text()
        stay_clip = page.locator("#platformClipStatus").inner_text()
        event_href = page.locator("#eventLink").get_attribute("href") or ""
        evidence_src = page.locator("#evidenceVideo").get_attribute("src") or ""
        page.wait_for_timeout(800)
        chart_ok = page.evaluate("() => {const i=document.getElementById('windowChart');return i.complete && i.naturalWidth>0;}")
        image_ok = page.evaluate("() => {const i=document.getElementById('evidenceImage');return i.complete && i.naturalWidth>0;}")
        screenshot_full(page, str(args.output / "05-stay-candidate.png"))
        check("stay_rule_and_window", "10.00 秒" in stay_rule and "1.40—11.20" in stay_window and "有效观测 10.00" in stay_window and "最后样本覆盖至 11.40 秒" in stay_window and "覆盖要求终点 11.40 秒" in stay_window and "1.40—11.40" in stay_window, {"rule": stay_rule, "window": stay_window})
        check("stay_clip_coverage", "覆盖要求 10.00 秒" in stay_clip and "比较窗口 9.80 秒" in stay_clip and "有效观测 10.00 秒" in stay_clip and "完整" in stay_clip, {"clip": stay_clip})
        check("stay_event_link", "/explore?event_id=" in event_href and "179380" in event_href, {"href": event_href})
        check("stay_evidence_video", "/api/unified/clip/" in evidence_src, {"src": evidence_src})
        check("stay_evidence_figures", chart_ok and image_ok, {"chart": chart_ok, "image": image_ok})
        check("review_defaults_empty", page.locator("#reviewer").input_value() == "" and page.locator("#decision").input_value() == "" and page.locator("#note").input_value() == "" and page.locator("#submitReview").is_enabled(), {})
        check("review_history_empty_real", "尚无提交记录" in page.locator("#reviewHistory").inner_text(), {"text": page.locator("#reviewHistory").inner_text()})

        # A corrected stay case with missing frames (track 47 / R1).
        stay_buttons = page.locator("#candidateList button")
        corrected_index = next(i for i in range(stay_buttons.count()) if "区域停留 · 31.20 秒" in stay_buttons.nth(i).inner_text())
        stay_buttons.nth(corrected_index).click()
        page.wait_for_timeout(900)
        corrected_window = page.locator("#platformWindow").inner_text()
        page.evaluate("() => document.querySelector('#evidenceAppendix summary').click()")
        page.wait_for_timeout(250)
        corrected_caption = page.locator("#windowChartCaption").inner_text()
        page.screenshot(path=str(args.output / "05b-corrected-stay.png"), full_page=True)
        check("corrected_stay_window_visible", all(token in corrected_window for token in ("20.60—31.00", "有效观测 10.00", "跨度至条件 10.60", "缺帧 0.60", "最后样本覆盖至 31.20 秒", "21.20—31.20")), {"window": corrected_window})
        check("corrected_stay_chart_caption", all(token in corrected_caption for token in ("有效观测 10.00", "实际跨度 10.60", "缺帧 0.60", "最后样本覆盖至 31.20 秒")), {"caption": corrected_caption})

        # crowd candidate and 60 s window
        buttons = page.locator("#candidateList button")
        crowd_index = next(i for i in range(buttons.count()) if "人数变化" in buttons.nth(i).inner_text())
        buttons.nth(crowd_index).click()
        page.wait_for_timeout(900)
        crowd_window = page.locator("#platformWindow").inner_text()
        crowd_clip = page.locator("#platformClipStatus").inner_text()
        play_window_text = page.locator("#playWindow").inner_text()
        check("crowd_exact_60s_window", "60 秒比较窗口 33.40—93.40" in crowd_window and "观测数 2 → 7" in crowd_window and "最后观测样本覆盖至 93.60 秒" in crowd_window, {"window": crowd_window})
        check("crowd_clip_full_window", "覆盖要求 60.20 秒" in crowd_clip and "比较窗口 60.00 秒" in crowd_clip, {"clip": crowd_clip})
        check("crowd_window_button", page.locator("#playWindow").is_enabled() and "完整 60 秒窗口" in play_window_text, {"text": play_window_text})
        page.locator("#playWindow").click()
        page.wait_for_timeout(1800)
        window_state = video_state(page)
        screenshot_full(page, str(args.output / "06-crowd-window.png"))
        check("crowd_window_starts_at_33_4", 32.8 <= window_state["t"] <= 35.5 and not window_state["paused"], window_state)
        page.locator("#play").click()
        page.wait_for_timeout(300)

        # Platform evidence player: real metadata, cross-fragment seek and tail playback.
        player_meta = page.evaluate("""async () => {
          const v=document.getElementById('evidenceVideo');
          if(v.readyState<1) await new Promise(res=>{v.addEventListener('loadedmetadata',res,{once:true});setTimeout(res,10000)});
          return {duration:v.duration, readyState:v.readyState, error:v.error?v.error.message:null,
                  seekable:v.seekable.length?[v.seekable.start(0),v.seekable.end(0)]:null,
                  width:v.videoWidth, height:v.videoHeight};
        }""")
        check("platform_player_metadata", player_meta["duration"] >= 60 and abs(player_meta["duration"] - float(page.locator("#platformClipStatus").inner_text().split("已解码 ")[1].split(" 秒")[0])) <= 1.0 and player_meta["seekable"] and player_meta["seekable"][1] >= player_meta["duration"] - 0.5, player_meta)
        seek_results = page.evaluate("""async (targets) => {
          const v=document.getElementById('evidenceVideo'); const out=[];
          for(const target of targets){
            await new Promise(res=>{
              const done=()=>{v.removeEventListener('seeked',done);res();};
              v.addEventListener('seeked',done);
              v.currentTime=Math.min(target, Math.max(0,v.duration-0.05));
              setTimeout(done,4000);
            });
            out.push({target:target, currentTime:v.currentTime, readyState:v.readyState, error:v.error?v.error.message:null});
          }
          return out;
        }""", [5, 30, 60, 66])
        cross_ok = all(abs(item["currentTime"] - min(item["target"], player_meta["duration"] - 0.05)) <= 0.5 and item["readyState"] >= 2 and not item["error"] for item in seek_results)
        check("platform_player_cross_fragment_seek", cross_ok, seek_results)
        tail_state = page.evaluate("""async () => {
          const v=document.getElementById('evidenceVideo');
          const start=Math.max(0,v.duration-2);
          await new Promise(res=>{const done=()=>{v.removeEventListener('seeked',done);res();};v.addEventListener('seeked',done);v.currentTime=start;setTimeout(done,4000);});
          v.play().catch(()=>{});
          await new Promise(res=>setTimeout(res,1600));
          const state={start:start,currentTime:v.currentTime,paused:v.paused,ended:v.ended,duration:v.duration,error:v.error?v.error.message:null};
          v.pause();
          return state;
        }""")
        page.screenshot(path=str(args.output / "06b-platform-player.png"))
        check("platform_player_tail_playback", (not tail_state["ended"]) and tail_state["currentTime"] > tail_state["start"] + 0.3 and not tail_state["error"], tail_state)

        # desktop overflow
        overflow_desktop = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        check("desktop_no_horizontal_overflow", overflow_desktop <= 1, {"overflow_px": overflow_desktop})
        check("real_page_console_clean", not console_real and not page_errors_real, {"console": console_real, "page_errors": page_errors_real})

        context.close()
        videos = sorted(args.output.glob("*.webm"))
        if videos:
            videos[0].replace(args.output / "unified-acceptance.webm")
        browser.close()

        # Mobile 390x844
        browser = playwright.chromium.launch(headless=True, executable_path=args.chrome, args=["--no-sandbox"])
        mobile_context = browser.new_context(viewport={"width": 390, "height": 844})
        mobile = mobile_context.new_page()
        mobile_errors = []
        mobile.on("console", lambda message: mobile_errors.append(message.text) if message.type == "error" else None)
        mobile.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(mobile)
        mobile.wait_for_timeout(1200)
        mobile.locator("#candidateList button").first.click()
        mobile.wait_for_timeout(700)
        overflow_mobile = mobile.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        mobile.evaluate("() => window.scrollTo(0,0)")
        mobile.wait_for_timeout(200)
        mobile.screenshot(path=str(args.output / "07-mobile-390x844.png"), full_page=True)
        check("mobile_390_no_horizontal_overflow", overflow_mobile <= 1, {"overflow_px": overflow_mobile})
        check("mobile_no_console_errors", not mobile_errors, {"console": mobile_errors})
        mobile_context.close()

        # Missing media
        missing_context = browser.new_context(viewport={"width": 1280, "height": 900})
        missing = missing_context.new_page()
        missing.route("**/media/unified/analysis.mp4", lambda route: route.abort())
        missing.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        missing.wait_for_timeout(2500)
        missing_text = missing.locator("#mediaMessage").inner_text()
        missing_error = missing.locator("#error").inner_text() if missing.locator("#error").is_visible() else ""
        missing.screenshot(path=str(args.output / "08-missing-media.png"))
        check("missing_media_explicit_and_disabled", "视频缺失或无法解码" in missing_text and missing.locator("#play").is_disabled() and "无法加载" in missing_error, {"message": missing_text, "error": missing_error})
        missing_context.close()

        # Missing platform clip (advertised as ready, actual media load fails)
        clip_missing_context = browser.new_context(viewport={"width": 1280, "height": 900})
        clip_missing = clip_missing_context.new_page()
        clip_missing.route("**/api/unified/clip/*", lambda route: route.abort())
        clip_missing.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(clip_missing)
        clip_missing.wait_for_timeout(800)
        clip_missing.locator("#candidateList button").first.click()
        clip_missing.wait_for_timeout(1800)
        clip_missing_message = clip_missing.locator("#evidenceMessage").inner_text() if not clip_missing.locator("#evidenceMessage").is_hidden() else ""
        clip_missing_status = clip_missing.locator("#platformClipStatus").inner_text()
        clip_missing.screenshot(path=str(args.output / "08b-missing-platform-clip.png"))
        check("missing_platform_clip_explicit_and_disabled", "平台录像缺失或无法解码" in clip_missing_message and "实际加载失败" in clip_missing_status and clip_missing.locator("#evidenceVideo").is_hidden(), {"message": clip_missing_message, "status": clip_missing_status})
        clip_missing_context.close()

        # Platform offline
        offline_context = browser.new_context(viewport={"width": 1280, "height": 900})
        offline = offline_context.new_page()
        offline.route("**/api/unified/platform", lambda route: route.fulfill(status=503, content_type="application/json", body='{"available":false}'))
        offline.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(offline)
        offline.wait_for_timeout(800)
        offline_summary = offline.locator("#platformSummary").inner_text()
        offline.locator("#candidateList button").first.click()
        offline.wait_for_timeout(500)
        offline_message = offline.locator("#evidenceMessage").inner_text()
        offline.screenshot(path=str(args.output / "09-platform-offline.png"))
        check("platform_offline_explicit", "平台未接入/离线" in offline_summary and "平台未接入或离线" in offline_message and offline.locator("#submitReview").is_disabled(), {"summary": offline_summary, "message": offline_message})
        offline_context.close()

        # Alignment failure (route a modified real payload)
        real_payload = load_json_url("http://127.0.0.1:8765/api/unified/platform")
        real_payload["clock"]["status"] = "needs_review"
        real_payload["clock"]["rmse_seconds"] = 2.5
        real_payload["clock"]["max_abs_residual_seconds"] = 3.0
        align_context = browser.new_context(viewport={"width": 1280, "height": 900})
        align = align_context.new_page()
        align.route("**/api/unified/platform", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(real_payload, ensure_ascii=False)))
        align.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(align)
        align.wait_for_timeout(800)
        align_summary = align.locator("#platformSummary").inner_text()
        align.locator("#candidateList button").first.click()
        align.wait_for_timeout(500)
        align_message = align.locator("#evidenceMessage").inner_text()
        align.screenshot(path=str(args.output / "10-alignment-failure.png"))
        check("alignment_failure_explicit_and_disabled", "对齐待核验" in align_summary and "时间对齐待核验" in align_message and align.locator("#submitReview").is_disabled() and align.locator("#playWindow").is_disabled(), {"summary": align_summary, "message": align_message})
        align_context.close()

        # Test review flow (separate test ledger)
        test_context = browser.new_context(viewport={"width": 1280, "height": 900})
        test_page = test_context.new_page()
        test_page.on("console", lambda message: console_test.append(message.text) if message.type == "error" else None)
        test_page.on("pageerror", lambda error: page_errors_test.append(str(error)))
        test_page.goto(args.test_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(test_page)
        test_page.wait_for_timeout(1000)
        test_summary = test_page.locator("#platformSummary").inner_text()
        test_page.locator("#candidateList button").first.click()
        test_page.wait_for_timeout(600)
        test_page.fill("#reviewer", "playwright_acceptance_operator")
        test_page.select_option("#decision", "uncertain")
        test_page.fill("#note", "Playwright 验收流程测试，非真实复核；独立测试 ledger。")
        test_page.locator("#submitReview").click()
        test_page.wait_for_timeout(1200)
        submit_message = test_page.locator("#reviewMessage").inner_text()
        test_page.screenshot(path=str(args.output / "11-test-review-submit.png"))
        check("test_mode_banner", "测试模式" in test_summary, {"summary": test_summary})
        check("test_review_submit_success", "已保存" in submit_message, {"message": submit_message})
        test_page.reload(wait_until="domcontentloaded")
        wait_ready(test_page)
        test_page.wait_for_timeout(800)
        test_page.locator("#candidateList button").first.click()
        test_page.wait_for_timeout(600)
        after_reload_status = test_page.locator("#platformReviewStatus").inner_text()
        after_reload_history = test_page.locator("#reviewHistory").inner_text()
        test_page.screenshot(path=str(args.output / "12-test-review-after-reload.png"))
        check("test_review_persists_after_reload", "不能确认" in after_reload_status and "Playwright 验收流程测试" in after_reload_history, {"status": after_reload_status, "history": after_reload_history})

        # Save failure must not display success and must not write a review.
        fail_context = browser.new_context(viewport={"width": 1280, "height": 900})
        fail_page = fail_context.new_page()
        fail_page.route("**/api/review/submit", lambda route: route.fulfill(status=500, content_type="application/json", body='{"success":false,"message":"模拟保存失败"}'))
        fail_page.goto(args.test_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(fail_page)
        fail_page.wait_for_timeout(800)
        fail_page.locator("#candidateList button").first.click()
        fail_page.wait_for_timeout(500)
        fail_page.fill("#reviewer", "playwright_failure_test")
        fail_page.select_option("#decision", "rejected")
        fail_page.fill("#note", "保存失败测试，不应显示成功。")
        fail_page.locator("#submitReview").click()
        fail_page.wait_for_timeout(1000)
        fail_message = fail_page.locator("#reviewMessage").inner_text()
        fail_page.screenshot(path=str(args.output / "12b-test-review-save-failure.png"))
        check("test_save_failure_not_success", "保存失败" in fail_message and "已保存" not in fail_message, {"message": fail_message})
        fail_context.close()

        check("test_page_console_clean", not console_test and not page_errors_test, {"console": console_test, "page_errors": page_errors_test})
        test_context.close()

        # Old pages remain accessible
        old_status = {}
        request_context = playwright.request.new_context()
        try:
            for path in ("showcase_cases.html", "review.html", "index.html", "rules.html"):
                response = request_context.get("http://127.0.0.1:8765/" + path)
                old_status[path] = response.status
        finally:
            request_context.dispose()
        check("old_pages_accessible", all(code == 200 for code in old_status.values()), old_status)

        # Longer scripted tour for the 1-3 minute operation recording.  It uses
        # the real page and never submits a review.
        if (args.output / "unified-acceptance.webm").is_file():
            (args.output / "unified-acceptance.webm").replace(args.output / "unified-quick-checks.webm")
        tour_dir = args.output / "tour"
        tour_dir.mkdir(exist_ok=True)
        tour_context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            record_video_dir=str(tour_dir),
            record_video_size={"width": 1440, "height": 1000},
        )
        tour = tour_context.new_page()
        console_tour: list[str] = []
        tour_errors: list[str] = []
        tour.on("console", lambda message: console_tour.append(message.text) if message.type == "error" else None)
        tour.on("pageerror", lambda error: tour_errors.append(str(error)))
        tour_started = time.time()
        tour.goto(args.base_url, wait_until="domcontentloaded", timeout=30000)
        wait_ready(tour)
        tour.wait_for_timeout(3000)
        tour.locator("#play").click()
        tour.wait_for_timeout(20000)
        tour.locator("#play").click()
        tour.wait_for_timeout(1000)
        for value in (30, 60, 65):
            seek_to(tour, value)
            tour.wait_for_timeout(3000)
        tour.locator("#candidateList button").first.click()
        tour.wait_for_timeout(5000)
        tour.evaluate("() => {const v=document.getElementById('evidenceVideo'); v.muted=true; v.play().catch(()=>{});}")
        tour.wait_for_timeout(5000)
        tour.evaluate("() => {const v=document.getElementById('evidenceVideo'); v.pause();}")
        buttons = tour.locator("#candidateList button")
        crowd_index = next(i for i in range(buttons.count()) if "人数变化" in buttons.nth(i).inner_text())
        buttons.nth(crowd_index).click()
        tour.wait_for_timeout(5000)
        tour.locator("#playWindow").click()
        tour.wait_for_timeout(20000)
        tour.locator("#play").click()
        tour.evaluate("() => document.querySelector('#evidenceAppendix summary').click()")
        tour.wait_for_timeout(5000)
        tour.mouse.wheel(0, 900)
        tour.wait_for_timeout(3000)
        screenshot_full(tour, str(args.output / "13-tour.png"))
        tour_wall = time.time() - tour_started
        tour_context.close()
        tour_videos = sorted(tour_dir.glob("*.webm"))
        if tour_videos:
            tour_videos[0].replace(args.output / "unified-acceptance.webm")
        check("tour_recording_long_enough", tour_wall >= 60.0, {"wall_seconds": round(tour_wall, 2)})
        check("tour_console_clean", not console_tour and not tour_errors, {"console": console_tour, "page_errors": tour_errors})
        browser.close()

    # Ledger isolation check.
    real_reviews = []
    test_reviews = []
    real_db = ROOT / "data/a2/adapter/ledger.sqlite"
    test_db = ROOT / "data/a2/adapter/ledger.test.sqlite"
    if real_db.is_file():
        connection = sqlite3.connect(real_db)
        real_reviews = [
            {"reviewer": row[0], "decision": row[1], "note": row[2]}
            for row in connection.execute("select reviewer,decision,note from reviews")
        ]
        connection.close()
    if test_db.is_file():
        connection = sqlite3.connect(test_db)
        test_reviews = [
            {"reviewer": row[0], "decision": row[1], "note": row[2]}
            for row in connection.execute("select reviewer,decision,note from reviews")
        ]
        connection.close()
    check(
        "real_ledger_not_polluted_by_browser_test",
        not any(item["reviewer"] in {"playwright_acceptance_operator", "browser_test_operator"} for item in real_reviews),
        {"real_reviews": real_reviews, "test_reviews": test_reviews},
    )
    check(
        "test_review_written_to_test_ledger",
        any(item["reviewer"] == "playwright_acceptance_operator" for item in test_reviews) and not any(item["reviewer"] == "playwright_failure_test" for item in test_reviews),
        {"test_reviews": test_reviews},
    )

    passed = sum(1 for item in checks if item["passed"])
    result = {
        "schema": "unified-platform-browser-acceptance-v1",
        "started_at_utc": utc_now(),
        "base_url": args.base_url,
        "test_url": args.test_url,
        "checks": checks,
        "summary": {"total": len(checks), "passed": passed, "failed": len(checks) - passed},
        "console_real_errors": console_real,
        "page_errors_real": page_errors_real,
        "console_test_errors": console_test,
        "page_errors_test": page_errors_test,
        "real_reviews": real_reviews,
        "test_reviews": test_reviews,
        "artifacts": {
            "video": "unified-acceptance.webm" if (args.output / "unified-acceptance.webm").is_file() else None,
            "screenshots": sorted(path.name for path in args.output.glob("*.png")),
        },
    }
    (args.output / "browser_acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "console.log").write_text("\n".join(console_real + console_test) + "\n", encoding="utf-8")
    (args.output / "pageerrors.log").write_text("\n".join(page_errors_real + page_errors_test) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(json.dumps({"failed": [item for item in checks if not item["passed"]]}, ensure_ascii=False, indent=2))
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
