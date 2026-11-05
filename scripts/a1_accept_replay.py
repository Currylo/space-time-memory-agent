#!/usr/bin/env python3
"""A1 replay acceptance: bounded VOD chain, real browser playback, and evidence.

This is a read-only acceptance helper. It does not restart containers, modify the
recording database, or delete old recordings. It selects one post-restart range,
checks the VOD master/playlist/init/media chain, then opens the native Frigate
Review page in Chrome and records playback, pause, and seek evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "deploy/frigate/runtime"
CAMERA = "a1_public_demo"
ORIGIN = "http://127.0.0.1:15000"
EXPECTED_INPUT = "/input/public_demo_h264_long.mp4"


def find_ffmpeg(explicit=None):
    if explicit:
        return Path(explicit)
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise SystemExit("ffmpeg not found; pass --ffmpeg or set PATH")


def find_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as error:
        raise SystemExit(
            "playwright not found; install requirements.browser.txt in your environment"
        ) from error


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(opener, output, name, path, suffix):
    url = urllib.parse.urljoin(ORIGIN + "/", path)
    if urllib.parse.urlparse(url).netloc != "127.0.0.1:15000":
        raise ValueError("Refusing a non-local VOD resource")
    try:
        with opener.open(url, timeout=30) as response:
            status = response.status
            body = response.read(32 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read(16384)
    target = output / (name + suffix)
    target.write_bytes(body)
    return {
        "url": url,
        "status": status,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "path": str(target.relative_to(output)),
    }


def probe_recording(ffmpeg, path):
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(path)],
        text=True, capture_output=True, timeout=30,
    )
    lines = [
        line.strip() for line in result.stderr.splitlines()
        if "Duration:" in line or "Video:" in line
    ]
    return {"returncode": result.returncode, "stream_info": lines}


def parse_label(page):
    text = page.locator("body").inner_text()
    labels = re.findall(r"\b\d{2}:\d{2}:\d{2}\b", text)
    return labels[-1] if labels else None


def seconds_of_day(label):
    if not label:
        return None
    hour, minute, second = [int(part) for part in label.split(":")]
    return hour * 3600 + minute * 60 + second


def video_state(page):
    state = page.evaluate(
        """() => {
          const v = document.querySelector('video');
          if (!v) return null;
          return {
            currentTime: Math.round(v.currentTime * 1000) / 1000,
            duration: Math.round(v.duration * 1000) / 1000,
            paused: v.paused,
            readyState: v.readyState,
            networkState: v.networkState,
            videoWidth: v.videoWidth,
            videoHeight: v.videoHeight,
            error: v.error ? v.error.message : null,
          };
        }"""
    )
    if state is None:
        raise RuntimeError("video element not found")
    state["label"] = parse_label(page)
    return state


def hover_video(page):
    box = page.locator("video").bounding_box()
    if not box:
        raise RuntimeError("video bounding box not found")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(250)
    return box


def pause_button(page):
    # The control bar is the floating div at bottom-5. Its third child is pause/play.
    return page.locator("div.absolute.bottom-5 > :nth-child(3)")


def set_paused(page, paused):
    hover_video(page)
    state = video_state(page)
    if state["paused"] != paused:
        pause_button(page).click()
        page.wait_for_timeout(450)
    return video_state(page)


def timeline_box(page, label):
    locator = page.get_by_text(label, exact=True)
    box = locator.first.bounding_box()
    if not box:
        raise RuntimeError(f"timeline label {label} not visible")
    return box


def click_timeline(page, desired_label, attempts=3):
    """Click the right timeline until the playhead label is close to desired."""
    desired = seconds_of_day(desired_label)
    clicks = []
    for _ in range(attempts):
        top = timeline_box(page, "17:45")
        lower = timeline_box(page, "17:30")
        # Labels are 15 minutes apart; y increases toward earlier time.
        seconds_per_pixel = 900 / (lower["y"] - top["y"])
        current = seconds_of_day(parse_label(page))
        delta = current - desired
        y = top["y"] + delta / seconds_per_pixel
        page.mouse.click(1375, y)
        page.wait_for_timeout(900)
        state = video_state(page)
        clicks.append({"y": round(y, 2), "label": state["label"], "currentTime": state["currentTime"]})
        if state["label"] and abs(seconds_of_day(state["label"]) - desired) <= 6:
            break
    return clicks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, required=True, help="selected epoch start (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="selected epoch end (exclusive)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--seek-forward", default="17:44:10")
    parser.add_argument("--seek-back", default="17:43:42")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing evidence directory: {args.output}")
    args.output.mkdir(parents=True)
    vod_dir = args.output / "vod"
    browser_dir = args.output / "browser"
    vod_dir.mkdir()
    browser_dir.mkdir()

    ffmpeg = find_ffmpeg(args.ffmpeg)
    sync_playwright = find_playwright()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "camera": CAMERA,
        "selected_epoch": {"start": args.start, "end": args.end},
        "selected_local": {
            "start": datetime.fromtimestamp(args.start).isoformat(),
            "end": datetime.fromtimestamp(args.end).isoformat(),
        },
        "checks": {},
        "errors": [],
    }

    def check(name, passed, detail):
        result["checks"][name] = {"passed": bool(passed), "detail": detail}
        if not passed:
            result["errors"].append(f"{name}: {detail}")

    # 1. Active input must be the long H.264 file, with -re only.
    status, config = 0, {}
    try:
        with opener.open(ORIGIN + "/api/config", timeout=15) as response:
            status = response.status
            config = json.loads(response.read())
        inputs = config["cameras"][CAMERA]["ffmpeg"]["inputs"]
        active = [{"path": item["path"], "input_args": item.get("input_args")} for item in inputs]
        flat_args = [arg for item in active for arg in (item.get("input_args") or [])]
        ok = (
            status == 200
            and len(active) == 1
            and active[0]["path"] == EXPECTED_INPUT
            and active[0]["input_args"] == ["-re"]
            and "-stream_loop" not in flat_args
        )
        check("active_input", ok, {"http_status": status, "inputs": active})
    except Exception as error:  # noqa: BLE001 - evidence must retain the actual error
        check("active_input", False, f"{type(error).__name__}: {error}")

    # 2. Selected recordings must exist and be H.264 (not old mp4v).
    recordings = []
    try:
        connection = sqlite3.connect(f"file:{RUNTIME / 'config/frigate.db'}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "select path,start_time,end_time,duration from recordings "
            "where camera=? and start_time < ? and end_time > ? order by start_time",
            (CAMERA, args.end, args.start),
        ).fetchall()
        for row in rows:
            item = dict(row)
            local_path = RUNTIME / "media" / Path(item["path"]).relative_to("/media/frigate")
            item["local_path"] = str(local_path)
            item["exists"] = local_path.is_file()
            item["utc"] = datetime.fromtimestamp(item["start_time"], timezone.utc).isoformat()
            if item["exists"]:
                item["probe"] = probe_recording(ffmpeg, local_path)
                item["sha256"] = sha256(local_path)
            recordings.append(item)
        codecs = [line for item in recordings for line in item.get("probe", {}).get("stream_info", []) if "Video:" in line]
        ok = bool(recordings) and all("Video: h264" in line for line in codecs)
        check("selected_recording_is_h264", ok, {"recordings": recordings})
    except Exception as error:  # noqa: BLE001
        check("selected_recording_is_h264", False, f"{type(error).__name__}: {error}")

    # 3. Bounded VOD chain for the selected new range.
    try:
        master_path = f"/vod/{CAMERA}/start/{args.start}/end/{args.end}/master.m3u8"
        master = fetch(opener, vod_dir, "master", master_path, ".m3u8")
        playlist = None
        init = None
        segment = None
        decode = None
        if master["status"] == 200:
            variants = [
                line.strip() for line in (vod_dir / "master.m3u8").read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
            playlist_url = urllib.parse.urljoin(master["url"], variants[0])
            playlist_path = urllib.parse.urlparse(playlist_url).path
            playlist = fetch(opener, vod_dir, "playlist", playlist_path, ".m3u8")
            if playlist["status"] == 200:
                text = (vod_dir / "playlist.m3u8").read_text()
                init_match = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)
                if init_match:
                    init_url = urllib.parse.urljoin(playlist["url"], init_match.group(1))
                    init = fetch(opener, vod_dir, "init", urllib.parse.urlparse(init_url).path, ".mp4")
                segments = [
                    line.strip() for line in text.splitlines()
                    if line.strip() and not line.startswith("#")
                ]
                if segments:
                    segment_url = urllib.parse.urljoin(playlist["url"], segments[0])
                    segment = fetch(opener, vod_dir, "segment", urllib.parse.urlparse(segment_url).path, ".m4s")
                    if init and init["status"] == 200 and segment["status"] == 200:
                        combined = vod_dir / "init-plus-segment.mp4"
                        combined.write_bytes((vod_dir / "init.mp4").read_bytes() + (vod_dir / "segment.m4s").read_bytes())
                        process = subprocess.run(
                            [str(ffmpeg), "-v", "error", "-i", str(combined), "-t", "1", "-f", "null", "-"],
                            text=True, capture_output=True, timeout=30,
                        )
                        decode = {"returncode": process.returncode, "stderr": process.stderr}
        ok = bool(
            master and master["status"] == 200
            and playlist and playlist["status"] == 200
            and init and init["status"] == 200
            and segment and segment["status"] == 200
            and decode and decode["returncode"] == 0
        )
        check("bounded_vod_chain", ok, {
            "master": master, "playlist": playlist, "init": init,
            "segment": segment, "decode": decode,
        })
    except Exception as error:  # noqa: BLE001
        check("bounded_vod_chain", False, f"{type(error).__name__}: {error}")

    # 4. Real browser review playback, pause, seek, and media requests.
    media_responses = []
    browser = {}
    session_video = None
    try:
        with sync_playwright() as playwright:
            browser_instance = playwright.chromium.launch(
                headless=True,
                executable_path=args.chrome,
                args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
            )
            context = browser_instance.new_context(
                viewport={"width": 1440, "height": 1000},
                record_video_dir=str(browser_dir),
                record_video_size={"width": 1440, "height": 1000},
            )
            page = context.new_page()

            def on_response(response):
                if "/vod/" in response.url:
                    media_responses.append({
                        "status": response.status,
                        "url": response.url,
                        "at": datetime.now(timezone.utc).isoformat(),
                    })

            page.on("response", on_response)
            review_url = f"{ORIGIN}/review?timestamp={CAMERA}_{args.start}"
            browser["review_url"] = review_url
            page.goto(review_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            started = video_state(page)
            browser["started"] = started

            # Playback must advance.
            page.wait_for_timeout(3000)
            advanced = video_state(page)
            browser["advanced"] = advanced
            check(
                "browser_playback_advances",
                (not advanced["paused"])
                and advanced["currentTime"] > started["currentTime"] + 2.0
                and advanced["label"] != started["label"],
                {"started": started, "advanced": advanced},
            )
            page.screenshot(path=str(browser_dir / "01-playing.png"))

            # Pause must hold the playhead.
            paused = set_paused(page, True)
            page.wait_for_timeout(2500)
            paused_later = video_state(page)
            browser["paused"] = paused
            browser["paused_after_2_5s"] = paused_later
            check(
                "browser_pause_holds",
                paused["paused"] and paused_later["paused"]
                and abs(paused_later["currentTime"] - paused["currentTime"]) < 0.25,
                {"paused": paused, "after_2_5s": paused_later},
            )
            hover_video(page)
            page.screenshot(path=str(browser_dir / "02-paused.png"))

            # Seek forward through the visible timeline.
            forward_clicks = click_timeline(page, args.seek_forward)
            forward = video_state(page)
            browser["seek_forward_clicks"] = forward_clicks
            browser["seek_forward_state"] = forward
            check(
                "browser_seek_forward",
                seconds_of_day(forward["label"]) > seconds_of_day(paused["label"]) + 10,
                {"from": paused["label"], "to": forward["label"], "clicks": forward_clicks},
            )
            set_paused(page, True)
            hover_video(page)
            page.screenshot(path=str(browser_dir / "03-seek-forward-paused.png"))

            # Seek backward through the same timeline.
            back_clicks = click_timeline(page, args.seek_back)
            back = video_state(page)
            browser["seek_back_clicks"] = back_clicks
            browser["seek_back_state"] = back
            check(
                "browser_seek_back",
                seconds_of_day(back["label"]) < seconds_of_day(forward["label"]) - 10,
                {"from": forward["label"], "to": back["label"], "clicks": back_clicks},
            )
            hover_video(page)
            page.screenshot(path=str(browser_dir / "04-seek-back-playing.png"))

            # Resume and confirm it advances again.
            resumed = set_paused(page, False)
            page.wait_for_timeout(2500)
            resumed_later = video_state(page)
            browser["resumed"] = resumed
            browser["resumed_after_2_5s"] = resumed_later
            check(
                "browser_resume_advances",
                (not resumed_later["paused"])
                and resumed_later["currentTime"] > resumed["currentTime"] + 1.5,
                {"resumed": resumed, "after_2_5s": resumed_later},
            )
            page.screenshot(path=str(browser_dir / "05-resumed.png"))

            # Frame difference while playing proves the visible picture changes.
            frame_a = browser_dir / "frame-a.png"
            frame_b = browser_dir / "frame-b.png"
            page.locator("video").screenshot(path=str(frame_a))
            page.wait_for_timeout(1500)
            page.locator("video").screenshot(path=str(frame_b))
            try:
                from PIL import Image, ImageChops, ImageStat
                image_a = Image.open(frame_a).convert("RGB")
                image_b = Image.open(frame_b).convert("RGB")
                diff = ImageChops.difference(image_a, image_b)
                mean_abs = sum(ImageStat.Stat(diff).mean) / 3
                browser["frame_mean_abs_diff"] = round(mean_abs, 4)
                check("browser_frames_change", mean_abs > 0.5, {"mean_abs_diff": round(mean_abs, 4)})
            except ImportError:
                browser["frame_mean_abs_diff"] = None

            context.close()
            browser_instance.close()
        videos = sorted(browser_dir.glob("*.webm"))
        if videos:
            target = browser_dir / "review-session.webm"
            videos[0].replace(target)
            session_video = target

        # Keep the browser's actual media requests and index for range mapping.
        (browser_dir / "media-responses.json").write_text(
            json.dumps(media_responses, ensure_ascii=False, indent=2) + "\n"
        )
        master_urls = [item["url"] for item in media_responses if item["url"].endswith("master.m3u8")]
        segment_indices = []
        bad = []
        for item in media_responses:
            if item["status"] not in (200, 206):
                bad.append(item)
            match = re.search(r"seg-(\d+)-v1\.m4s", item["url"])
            if match:
                segment_indices.append(int(match.group(1)))
        if master_urls:
            index_url = urllib.parse.urljoin(master_urls[0], "index-v1.m3u8")
            try:
                with opener.open(index_url, timeout=30) as response:
                    index_body = response.read()
                (browser_dir / "browser-index.m3u8").write_bytes(index_body)
            except urllib.error.HTTPError as error:
                (browser_dir / "browser-index.error.txt").write_text(f"{error.code}\n{error.read().decode(errors='replace')}")
        browser["media_responses"] = media_responses
        browser["media_request_count"] = len(media_responses)
        browser["segment_indices"] = sorted(set(segment_indices))
        browser["bad_media_responses"] = bad
        browser["session_video"] = str(session_video.relative_to(args.output)) if session_video else None
        check(
            "browser_media_requests_healthy",
            bool(segment_indices) and not bad,
            {
                "count": len(media_responses),
                "segment_min": min(segment_indices) if segment_indices else None,
                "segment_max": max(segment_indices) if segment_indices else None,
                "bad": bad,
            },
        )
        # Post-restart starts around 17:41:20; old mp4v is before 17:16.
        check(
            "browser_uses_post_restart_segments",
            bool(segment_indices) and min(segment_indices) >= 245,
            {"segment_min": min(segment_indices) if segment_indices else None, "segment_max": max(segment_indices) if segment_indices else None},
        )
    except Exception as error:  # noqa: BLE001
        check("browser_review", False, f"{type(error).__name__}: {error}")
        browser["error"] = f"{type(error).__name__}: {error}"
        try:
            context.close()
            browser_instance.close()
        except Exception:
            pass

    result["browser"] = browser
    result["a1_replay_passed"] = all(item["passed"] for item in result["checks"].values()) and not result["errors"]
    (args.output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "a1_replay_passed": result["a1_replay_passed"],
        "checks": {name: item["passed"] for name, item in result["checks"].items()},
        "errors": result["errors"],
    }, ensure_ascii=False, indent=2))
    return 0 if result["a1_replay_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
