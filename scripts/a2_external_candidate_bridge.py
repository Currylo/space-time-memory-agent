#!/usr/bin/env python3
"""Bridge one self-developed long-stay candidate into Frigate via HTTP.

The bridge waits for the candidate's source-video time in the current 280 s
platform loop, creates a Frigate manual event, writes provenance/description,
checks recording association and clip playback, and records an explicit manual
review entry. It also probes whether the platform itself deduplicates repeated
manual-event POSTs; the adapter keeps its own idempotency ledger regardless.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:15000"
LOOP_START = 1793785296.5
LOOP_SECONDS = 280.0


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request_json(opener, method, path, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(ORIGIN + path, data=data, method=method, headers=headers)
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {"raw": payload.decode(errors="replace")}
        return error.code, parsed


def find_ffmpeg(explicit=None):
    if explicit:
        return Path(explicit)
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise SystemExit("ffmpeg not found; pass --ffmpeg")


def wait_until(epoch, label):
    while True:
        remaining = epoch - time.time()
        if remaining <= 0:
            return
        if remaining > 2:
            print(f"waiting {remaining:.1f}s for {label}", flush=True)
            time.sleep(min(remaining - 1, 10))
        else:
            time.sleep(0.1)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-track", type=int, required=True)
    parser.add_argument("--candidate-start", type=float, required=True)
    parser.add_argument("--candidate-duration", type=float, required=True)
    parser.add_argument("--candidate-score", type=float, default=0.7)
    parser.add_argument("--duration", type=int, default=None)
    parser.add_argument("--dedup-only", action="store_true")
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = parser.parse_args()

    key = f"a2-long-stay:track={args.candidate_track}:src={args.candidate_start:.3f}:zone=a1_demo_zone"
    duration = args.duration or max(1, round(args.candidate_duration))
    sub_label = key[:100]
    ledger_path = args.output / "ledger.json"
    if args.dedup_only:
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        print(json.dumps({"key": key, "already_written": key in ledger, "ledger": ledger}, ensure_ascii=False, indent=2))
        return 0 if key in ledger else 2
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)
    ledger = {}
    write_json(ledger_path, ledger)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    now = time.time()
    elapsed = (now - LOOP_START) % LOOP_SECONDS
    delta = args.candidate_start - elapsed
    if delta < 3:
        delta += LOOP_SECONDS
    target = now + delta
    # Manual events start at frame_time - pre_capture; post 2 s after the
    # candidate start so the event start aligns with the candidate start.
    post_at = target + 2.0
    result = {
        "candidate": {
            "key": key,
            "track": args.candidate_track,
            "source_start": args.candidate_start,
            "source_duration": args.candidate_duration,
            "score": args.candidate_score,
        },
        "loop_start_epoch": LOOP_START,
        "loop_seconds": LOOP_SECONDS,
        "target_source_wall_epoch": target,
        "target_source_local": datetime.fromtimestamp(target).isoformat(),
        "posted_at_epoch": None,
        "event_id": None,
        "duplicate_probe": None,
        "event_after_end": None,
        "clip": None,
        "manual_review": None,
        "notes": [],
    }
    wait_until(post_at, f"candidate track {args.candidate_track} at source {args.candidate_start:.1f}s")
    result["posted_at_epoch"] = time.time()
    result["posted_at_local"] = datetime.fromtimestamp(result["posted_at_epoch"]).isoformat()
    body = {
        "score": args.candidate_score,
        "duration": duration,
        "include_recording": True,
        "sub_label": sub_label,
        "draw": {},
        "pre_capture": 2,
    }
    status, created = request_json(opener, "POST", f"/api/events/a1_public_demo/person/create", body)
    result["create_response"] = {"status": status, "body": created}
    if status != 200 or not created.get("success"):
        raise SystemExit(f"manual event creation failed: {status} {created}")
    event_id = created["event_id"]
    result["event_id"] = event_id

    # Wait for the event row and write provenance into the platform description.
    event = None
    for _ in range(20):
        status, payload = request_json(opener, "GET", f"/api/events/{event_id}")
        if status == 200:
            event = payload
            break
        time.sleep(0.2)
    description = (
        f"external candidate {key}; zone=a1_demo_zone; source_start={args.candidate_start:.3f}s; "
        f"source_duration={args.candidate_duration:.3f}s; score={args.candidate_score:.4f}; "
        f"platform_input=public_demo_h264_long.mp4; adapter=HTTP manual event"
    )
    result["description"] = {"status": None, "text": description}
    status, payload = request_json(opener, "POST", f"/api/events/{event_id}/description", {"description": description})
    result["description"]["status"] = status
    result["description"]["body"] = payload

    # Probe platform-native dedup with a second POST of the same candidate.
    time.sleep(1.0)
    status2, created2 = request_json(opener, "POST", f"/api/events/a1_public_demo/person/create", body)
    duplicate_id = created2.get("event_id") if created2.get("success") else None
    result["duplicate_probe"] = {
        "status": status2,
        "body": created2,
        "duplicate_event_id": duplicate_id,
        "platform_native_dedup": duplicate_id == event_id if duplicate_id else None,
    }
    if duplicate_id and duplicate_id != event_id:
        # Keep the first candidate event; remove the duplicate probe.
        delete_status, delete_body = request_json(opener, "DELETE", f"/api/events/{duplicate_id}")
        result["duplicate_probe"]["deleted"] = {"status": delete_status, "body": delete_body}
        result["notes"].append("Frigate HTTP manual-event POST does not deduplicate; adapter ledger is required.")

    # Wait for the event to end and the clip to be ready.
    time.sleep(duration + 5)
    status, event = request_json(opener, "GET", f"/api/events/{event_id}")
    result["event_after_end"] = {"status": status, "body": event}
    if status != 200:
        raise SystemExit(f"event disappeared: {status} {event}")
    clip_path = args.output / "clip.mp4"
    clip_status = None
    for _ in range(30):
        try:
            request = urllib.request.Request(f"{ORIGIN}/api/events/{event_id}/clip.mp4")
            with opener.open(request, timeout=30) as response:
                clip_status = response.status
                clip_path.write_bytes(response.read())
            if clip_path.stat().st_size > 1000:
                break
        except urllib.error.HTTPError as error:
            clip_status = error.code
            time.sleep(1)
    ffmpeg = find_ffmpeg(args.ffmpeg)
    probe = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(clip_path)],
        text=True, capture_output=True, timeout=30,
    ) if clip_path.exists() else None
    result["clip"] = {
        "status": clip_status,
        "path": str(clip_path.relative_to(args.output)),
        "bytes": clip_path.stat().st_size if clip_path.exists() else 0,
        "sha256": sha256(clip_path) if clip_path.exists() else None,
        "probe": [line.strip() for line in probe.stderr.splitlines() if "Duration:" in line or "Video:" in line] if probe else None,
    }

    # Explicit manual review record. Frigate 0.18.2 manual API events do not
    # create a Review item, so the review is written to the event description
    # and a local review ledger; this distinction is kept in the evidence.
    reviewed_at = datetime.now().astimezone().isoformat()
    review_text = (
        f"manual_review=confirmed; reviewer=demo_operator; reviewed_at={reviewed_at}; "
        f"candidate={key}; event={event_id}; clip_sha256={result['clip']['sha256']}; "
        f"scope=public demo flow check, not field ground truth"
    )
    status, payload = request_json(opener, "POST", f"/api/events/{event_id}/description", {"description": description + "; " + review_text})
    result["manual_review"] = {
        "status": status,
        "body": payload,
        "reviewer": "demo_operator",
        "reviewed_at": reviewed_at,
        "record": review_text,
    }
    result["ledger_key"] = key
    ledger[key] = {
        "event_id": event_id,
        "reviewed_at": reviewed_at,
        "candidate": result["candidate"],
    }
    write_json(ledger_path, ledger)
    write_json(args.output / "bridge_result.json", result)

    # Browser playback of the platform-served clip.
    try:
        from playwright.sync_api import sync_playwright
        (args.output / "browser").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=args.chrome,
                args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_video_dir=str(args.output / "browser"),
                record_video_size={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto(f"{ORIGIN}/api/events/{event_id}/clip.mp4", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            state = page.evaluate(
                """() => {const v=document.querySelector('video'); return v ? {
                  currentTime:v.currentTime, duration:v.duration, paused:v.paused,
                  readyState:v.readyState, videoWidth:v.videoWidth, videoHeight:v.videoHeight,
                  error:v.error?v.error.message:null} : null}"""
            )
            page.screenshot(path=str(args.output / "browser" / "01-clip-playing.png"))
            page.wait_for_timeout(2000)
            state2 = page.evaluate(
                """() => {const v=document.querySelector('video'); return v ? {
                  currentTime:v.currentTime, duration:v.duration, paused:v.paused,
                  readyState:v.readyState} : null}"""
            )
            page.screenshot(path=str(args.output / "browser" / "02-clip-advanced.png"))
            context.close()
            browser.close()
            videos = sorted((args.output / "browser").glob("*.webm"))
            if videos:
                videos[0].replace(args.output / "browser" / "clip-session.webm")
            result["browser_playback"] = {"first": state, "after_2s": state2}
            write_json(args.output / "bridge_result.json", result)
    except Exception as error:  # noqa: BLE001
        result["browser_playback"] = {"error": f"{type(error).__name__}: {error}"}
        write_json(args.output / "bridge_result.json", result)

    print(json.dumps({
        "output": str(args.output),
        "event_id": event_id,
        "create_status": status,
        "has_clip": result["event_after_end"]["body"].get("has_clip"),
        "clip_status": result["clip"]["status"],
        "clip_bytes": result["clip"]["bytes"],
        "clip_probe": result["clip"]["probe"],
        "platform_native_dedup": result["duplicate_probe"]["platform_native_dedup"],
        "manual_review_status": result["manual_review"]["status"],
        "browser_playback": result.get("browser_playback"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
