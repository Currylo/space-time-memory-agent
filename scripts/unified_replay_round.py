#!/usr/bin/env python3
"""Controlled same-source replay: calibration, off-line candidate injection, clock proof.

Usage (vision venv because OpenCV is needed):

  .venv/bin/python scripts/unified_replay_round.py calibrate --round-dir ...
  .venv/bin/python scripts/unified_replay_round.py inject --round-dir ...

The script never writes a platform event during calibration.  During injection
it uses the persistent adapter ledger, submits each frozen candidate at its
mapped source time, and records the clock model actually used for each submit.
Clip downloads and description writes are deliberately deferred.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from st_memory.a2_adapter import Ledger, PlatformClient, candidate_key, sha256_file, submit_candidate  # noqa: E402
from st_memory.unified_platform import (  # noqa: E402
    CAMERA_DEFAULT,
    ClockModel,
    ClockSample,
    SourceIndex,
    annotate_causal_stay_events,
    clock_segment_anchors,
    event_request_plan,
    fit_clock,
    summarize_replay,
)
import unified_config as uc  # noqa: E402

ORIGIN = "http://127.0.0.1:15000"
RUNTIME_MEDIA = ROOT / "deploy/frigate/runtime/media"
FRIGATE_DB = ROOT / "deploy/frigate/runtime/config/frigate.db"
LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
RUN_DIR = ROOT / "runs/unified-mot20-05-detect-pose-v2-001"
SOURCE = ROOT / "data/a2/unified/MOT20-05-raw.mp4"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_bytes(url: str, timeout: float = 10.0) -> tuple[int, bytes, float, float]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.time()
    try:
        with opener.open(url, timeout=timeout) as response:
            body = response.read()
            finished = time.time()
            return response.status, body, started, finished
    except urllib.error.HTTPError as error:
        return error.code, error.read(), started, time.time()
    except Exception:  # noqa: BLE001
        return None, b"", started, time.time()


def request_json(method: str, path: str, body=None, timeout: float = 20.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ORIGIN + path, data=data, method=method, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as response:
            payload = response.read()
            try:
                return response.status, json.loads(payload)
            except json.JSONDecodeError:
                return response.status, {"text": payload.decode(errors="replace")}
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload)
        except Exception:
            return error.code, {"raw": payload.decode(errors="replace")}
    except Exception as error:  # noqa: BLE001
        return None, {"error": f"{type(error).__name__}: {error}"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def decode_segment(path: Path, index: SourceIndex) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"decoded": False, "error": "cannot_open", "path": str(path)}
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or index.fps)
    endpoints = {}
    for label, positions in (("first", [0, 1, 2]), ("last", [count - 1, count - 2, count - 3])):
        match = None
        for position in positions:
            if position < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, position)
            ok, frame = cap.read()
            if not ok:
                continue
            match = index.match(frame)
            match["decoded_position"] = position
            if match.get("watermark_source_time") is not None or match.get("source_time") is not None:
                break
        endpoints[label] = match
    cap.release()

    first = endpoints.get("first") or {}
    last = endpoints.get("last") or {}

    def source_time(item):
        if not item:
            return None
        return item.get("watermark_source_time", item.get("source_time"))

    return {
        "decoded": bool(first.get("frame_index") is not None and last.get("frame_index") is not None),
        "path": str(path),
        "frame_count": count,
        "fps": fps,
        "duration_seconds": count / fps if fps else None,
        "first": first,
        "last": last,
        "first_source_time": source_time(first),
        "last_source_time": source_time(last),
        "first_watermark": (first.get("watermark") or {}).get("frame_number"),
        "last_watermark": (last.get("watermark") or {}).get("frame_number"),
        "methods_agree": bool(first.get("methods_agree") is not False and last.get("methods_agree") is not False),
    }


def map_container_path(container_path: str) -> Path:
    prefix = "/media/frigate/"
    if container_path.startswith(prefix):
        return RUNTIME_MEDIA / container_path[len(prefix):]
    return Path(container_path)


def db_segments(camera: str, since_epoch: float, until_epoch: float | None = None) -> list[dict]:
    uri = f"file:{FRIGATE_DB}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        if until_epoch is None:
            rows = connection.execute(
                "SELECT id,camera,path,start_time,end_time,duration FROM recordings WHERE camera=? AND end_time>=? ORDER BY end_time",
                (camera, since_epoch),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id,camera,path,start_time,end_time,duration FROM recordings WHERE camera=? AND end_time>=? AND start_time<=? ORDER BY end_time",
                (camera, since_epoch, until_epoch),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


class CameraController:
    def __init__(self, round_dir: Path, camera: str = CAMERA_DEFAULT, origin: str = ORIGIN):
        self.round_dir = round_dir
        self.camera = camera
        self.origin = origin

    def set_enabled(self, enabled: bool, label: str) -> dict:
        raw = uc.get_raw_config(self.origin)
        text, value = uc.build_config(raw, enabled=enabled)
        candidate_path = self.round_dir / "platform" / f"config.{label}.yml"
        candidate_path.write_text(text, encoding="utf-8")
        save = uc.save_config(self.origin, text, restart=True)
        ready = uc.wait_ready(self.origin, timeout=180.0, expect_camera=self.camera)
        stats = None
        if ready.get("ok"):
            config = ready.get("config") or {}
            camera_config = (config.get("cameras") or {}).get(self.camera) or {}
            stats = uc.camera_stats(self.origin, self.camera)
            record = {
                "label": label,
                "enabled_requested": enabled,
                "save": save,
                "ready": {k: v for k, v in ready.items() if k != "config"},
                "config_camera_enabled": camera_config.get("enabled"),
                "config_input": ((camera_config.get("ffmpeg") or {}).get("inputs") or [{}])[0].get("path"),
                "config_input_args": ((camera_config.get("ffmpeg") or {}).get("inputs") or [{}])[0].get("input_args"),
                "stats": stats,
                "recorded_at_utc": utc_now(),
            }
        else:
            record = {
                "label": label,
                "enabled_requested": enabled,
                "save": save,
                "ready": {k: v for k, v in ready.items() if k != "config"},
                "config_camera_enabled": None,
                "error": "api_or_camera_did_not_recover",
                "recorded_at_utc": utc_now(),
            }
        write_json(self.round_dir / "platform" / f"camera_{label}.json", record)
        return record


class LiveAligner:
    """Collect independent clock samples while the replay is running."""

    def __init__(self, round_dir: Path, camera: str, index: SourceIndex, latency: float, bias: float):
        self.round_dir = round_dir
        self.camera = camera
        self.index = index
        self.latency = latency
        self.bias = bias
        self.snapshot_samples: list[dict] = []
        self.segment_samples: list[dict] = []
        self.segments: list[dict] = []
        self.processed: set[str] = set()
        self.loop_index = 0
        self.loop_boundaries: list[dict] = []
        self.previous_last_source: float | None = None
        self.snapshot_dir = round_dir / "alignment" / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def capture_snapshot(self) -> dict | None:
        import cv2
        import numpy as np

        url = f"{self.origin_url()}/api/{self.camera}/latest.jpg?ts={time.time():.6f}"
        status, body, started, finished = fetch_bytes(url)
        record = {
            "kind": "latest_snapshot",
            "request_started_epoch": started,
            "response_finished_epoch": finished,
            "http_status": status,
            "bytes": len(body),
            "loop_index": self.loop_index,
        }
        if status != 200 or len(body) < 1000:
            record["accepted"] = False
            record["reason"] = "http_or_empty"
            return record
        image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            record["accepted"] = False
            record["reason"] = "decode_failed"
            return record
        match = self.index.match(image)
        record.update({
            "accepted": match.get("watermark_source_time") is not None or match.get("source_time") is not None,
            "watermark": match.get("watermark"),
            "image_match": {
                "frame_index": match.get("frame_index"),
                "source_time": match.get("source_time"),
                "image_mae": match.get("image_mae"),
                "hash_hamming": match.get("hash_hamming"),
                "watermark_index": match.get("watermark_index"),
                "watermark_source_time": match.get("watermark_source_time"),
                "methods_agree": match.get("methods_agree"),
            },
        })
        if record["accepted"]:
            source_time = match.get("watermark_source_time")
            if source_time is None:
                source_time = match.get("source_time")
            record["source_time"] = source_time
            record["t0_snapshot_epoch"] = finished - source_time
            filename = f"{finished:.3f}.jpg"
            (self.snapshot_dir / filename).write_bytes(body)
            record["image_path"] = str((self.snapshot_dir / filename).relative_to(self.round_dir))
            self.snapshot_samples.append(record)
        return record

    def origin_url(self) -> str:
        return ORIGIN

    def read_segments(self, since_epoch: float) -> list[dict]:
        new_records = []
        for row in db_segments(self.camera, since_epoch):
            segment_id = row["id"]
            if segment_id in self.processed:
                continue
            path = map_container_path(row["path"])
            if not path.is_file():
                continue
            if time.time() < float(row["end_time"]) + 0.5:
                continue
            decoded = decode_segment(path, self.index)
            if not decoded.get("decoded"):
                continue
            self.processed.add(segment_id)
            first_source = decoded.get("first_source_time")
            last_source = decoded.get("last_source_time")
            if first_source is None or last_source is None:
                continue
            # A large backwards jump in the burned-in frame number is a new
            # file loop, not a normal overlapping segment.
            if self.previous_last_source is not None and first_source < self.previous_last_source - 30.0:
                self.loop_index += 1
                self.loop_boundaries.append({
                    "loop_index": self.loop_index,
                    "first_seen_epoch": time.time(),
                    "segment_start": row["start_time"],
                    "previous_last_source_time": self.previous_last_source,
                    "new_first_source_time": first_source,
                    "first_watermark": decoded.get("first_watermark"),
                    "note": "source time jumped backwards; treated as a new platform replay loop",
                })
            loop_index = self.loop_index
            anchors = clock_segment_anchors({
                "segment_id": segment_id,
                "segment_start_epoch": float(row["start_time"]),
                "segment_end_epoch": float(row["end_time"]),
                "first_source_time": first_source,
                "last_source_time": last_source,
                "frame_count": decoded.get("frame_count"),
                "db_duration": float(row["duration"]),
            }, fps=self.index.fps)
            t0_sample_v1 = float(row["end_time"]) - last_source
            record = {
                "kind": "recording_segment",
                "loop_index": loop_index,
                "segment_id": segment_id,
                "path": str(path.relative_to(ROOT)),
                "container_path": row["path"],
                "segment_start_epoch": float(row["start_time"]),
                "segment_end_epoch": float(row["end_time"]),
                "db_duration": float(row["duration"]),
                "decoded": decoded,
                "first_source_time": first_source,
                "last_source_time": last_source,
                "segment_anchors": anchors,
                "t0_sample_epoch": anchors["t0_from_start"],
                "t0_sample_v1_epoch": t0_sample_v1,
                "observed_at_epoch": time.time(),
            }
            self.segments.append(record)
            if loop_index == 0:
                self.segment_samples.append(record)
            new_records.append(record)
            self.previous_last_source = last_source
        return new_records

    def snapshot_model(self) -> ClockModel | None:
        samples = [
            ClockSample(
                source_time=float(item["source_time"]),
                platform_epoch=float(item["response_finished_epoch"]),
                kind="latest_snapshot",
                note=f"watermark={item.get('watermark', {}).get('frame_number')}",
            )
            for item in self.snapshot_samples
            if item.get("source_time") is not None
        ]
        if not samples:
            return None
        # The sparse snapshot stream is aliased by the platform's processed
        # frame rate, so its slope is not a usable drift estimate.  Fix the
        # slope at 1.0 and use the median intercept, which is the actual
        # latency-bearing quantity.
        model = fit_clock(samples, fixed_drift=1.0)
        return model.shifted(-self.latency)

    def recording_model(self) -> ClockModel | None:
        samples = []
        for item in self.segment_samples:
            anchors = item.get("segment_anchors") or clock_segment_anchors(item, fps=self.index.fps)
            samples.append(ClockSample(
                source_time=float(anchors["start_anchor"]["source_time"]),
                platform_epoch=float(anchors["start_anchor"]["platform_epoch"]),
                kind="recording_start_anchor",
                note=f"segment={item['segment_id']}",
            ))
            samples.append(ClockSample(
                source_time=float(anchors["coverage_end_anchor"]["source_time"]),
                platform_epoch=float(anchors["coverage_end_anchor"]["platform_epoch"]),
                kind="recording_coverage_end_anchor",
                note=f"segment={item['segment_id']}",
            ))
        if not samples:
            return None
        return fit_clock(samples)

    def best_model(self) -> tuple[ClockModel | None, str]:
        recording = self.recording_model()
        if recording is not None and len(recording.residuals) >= 1:
            return recording, "recording_segment"
        snapshot = self.snapshot_model()
        if snapshot is not None:
            return snapshot, "latest_snapshot_minus_calibration_latency"
        return None, "none"

    def clock_report(self) -> dict:
        return {
            "latency_calibration_seconds": self.latency,
            "schedule_bias_seconds": self.bias,
            "snapshot_sample_count": len(self.snapshot_samples),
            "recording_sample_count": len(self.segment_samples),
            "snapshot_samples": self.snapshot_samples,
            "recording_samples": self.segment_samples,
            "segments": self.segments,
            "loop_boundaries": self.loop_boundaries,
        }


def load_frozen_run():
    result = read_json(RUN_DIR / "result.json")
    candidates = read_json(RUN_DIR / "candidates.json")
    config = read_json(RUN_DIR / "config.json")
    config["video_duration"] = float(result["source"]["duration"])
    config["video_frame_count"] = int(result["source"]["frame_count"])
    config["source_name"] = result["source"]["name"]
    return result, candidates, config


def calibrate(args) -> int:
    round_dir = args.round_dir.resolve()
    (round_dir / "alignment").mkdir(parents=True, exist_ok=True)
    result, candidates, config = load_frozen_run()
    index = SourceIndex(SOURCE, fps=config["video_fps"])
    controller = CameraController(round_dir, camera=args.camera)
    enabled = controller.set_enabled(True, "calibration-enable")
    if not enabled.get("ready", {}).get("ok"):
        raise SystemExit(f"camera did not start: {enabled}")
    save_epoch = enabled["save"]["save_finished_epoch"]
    start_epoch = time.time()
    aligner = LiveAligner(round_dir, args.camera, index, latency=0.0, bias=0.0)
    snapshots = []
    # Wait for the camera's first real frame before starting the timed
    # calibration window.  A placeholder latest.jpg is not a clock sample.
    first_valid_deadline = time.time() + args.snapshot_wait_seconds
    while time.time() < first_valid_deadline and not aligner.snapshot_samples:
        record = aligner.capture_snapshot()
        if record:
            snapshots.append(record)
        aligner.read_segments(save_epoch)
        time.sleep(0.25)
    capture_started = time.time()
    deadline = capture_started + args.snapshot_seconds
    last_snapshot = 0.0
    while time.time() < deadline:
        now = time.time()
        if now - last_snapshot >= 0.35:
            record = aligner.capture_snapshot()
            if record:
                snapshots.append(record)
            last_snapshot = now
        aligner.read_segments(save_epoch)
        time.sleep(0.05)
    aligner.read_segments(save_epoch)
    # Keep the camera running long enough for the recording maintainer to move
    # the first closed cache segment out of the container's tmpfs.  Without
    # this hold, a config-save restart can destroy the still-cached first
    # segment and leave no recording evidence.
    hold_until = time.time() + args.hold_seconds
    while time.time() < hold_until:
        if time.time() - last_snapshot >= 0.5:
            aligner.capture_snapshot()
            last_snapshot = time.time()
        aligner.read_segments(save_epoch)
        time.sleep(0.1)
    aligner.read_segments(save_epoch)
    disabled = controller.set_enabled(False, "calibration-disable")
    time.sleep(3.0)
    aligner.read_segments(save_epoch)

    snapshot_model = aligner.snapshot_model()
    recording_model = aligner.recording_model()
    latency = None
    if snapshot_model and recording_model:
        latency = snapshot_model.t0 - recording_model.t0
    calibration = {
        "schema": "unified-platform-calibration-v1",
        "created_at_utc": utc_now(),
        "camera": args.camera,
        "source_sha256": config["source_sha256"],
        "snapshot_seconds_requested": args.snapshot_seconds,
        "save_epoch": save_epoch,
        "enable": enabled,
        "disable": disabled,
        "snapshots": snapshots,
        "snapshot_model_latency_bearing": snapshot_model.as_dict() if snapshot_model else None,
        "snapshot_model_corrected": snapshot_model.shifted(-latency).as_dict() if (snapshot_model and latency is not None) else None,
        "recording_model": recording_model.as_dict() if recording_model else None,
        "latency_seconds": latency,
        "latency_note": (
            "latest.jpg response timestamp minus the time of the frame it shows, "
            "estimated by comparing snapshot-fit t0 with recording-fit t0 on the same replay"
        ),
        "accepted_snapshots": sum(1 for item in snapshots if item.get("accepted")),
        "total_snapshots": len(snapshots),
    }
    if latency is None or not (-1.0 <= latency <= 5.0):
        calibration["status"] = "needs_review"
        calibration["reason"] = "latency_not_estimable_or_implausible"
    else:
        calibration["status"] = "verified"
    write_json(round_dir / "platform" / "calibration.json", calibration)
    write_json(round_dir / "alignment" / "calibration_alignment.json", {
        "snapshot_model_latency_bearing": calibration["snapshot_model_latency_bearing"],
        "snapshot_model_corrected": calibration["snapshot_model_corrected"],
        "recording_model": calibration["recording_model"],
        "latency_seconds": latency,
        "segments": aligner.segments,
    })
    print(json.dumps({
        "status": calibration["status"],
        "latency_seconds": latency,
        "accepted_snapshots": calibration["accepted_snapshots"],
        "recording_samples": len(aligner.segment_samples),
        "snapshot_model": calibration["snapshot_model_latency_bearing"],
        "snapshot_model_corrected": calibration["snapshot_model_corrected"],
        "recording_model": calibration["recording_model"],
        "loops": aligner.loop_boundaries,
    }, ensure_ascii=False, indent=2))
    return 0


def inject(args) -> int:
    round_dir = args.round_dir.resolve()
    (round_dir / "platform").mkdir(parents=True, exist_ok=True)
    result, events, config = load_frozen_run()
    observations = [json.loads(line) for line in (RUN_DIR / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    events = annotate_causal_stay_events(events, observations, config)
    calibration_path = round_dir / "platform" / "calibration.json"
    if not calibration_path.is_file():
        raise SystemExit("calibration.json missing; run calibrate first")
    calibration = read_json(calibration_path)
    if calibration.get("status") != "verified":
        raise SystemExit(f"calibration not verified: {calibration.get('status')} {calibration.get('reason')}")
    latency = float(calibration["latency_seconds"])
    replay_id = args.replay_id
    loop_index = int(args.loop_index)
    zone_version = args.zone_version
    camera = args.camera

    platform_candidates = summarize_replay(
        events, config, result["run_id"], camera=camera,
        replay_id=replay_id, loop_index=loop_index, zone_version=zone_version,
    )
    for candidate in platform_candidates:
        candidate["request_plan"] = event_request_plan(candidate)
    platform_candidates.sort(key=lambda c: c["condition_met_video_time"])

    index = SourceIndex(SOURCE, fps=config["video_fps"])
    controller = CameraController(round_dir, camera=camera)
    enabled = controller.set_enabled(True, "main-enable")
    if not enabled.get("ready", {}).get("ok"):
        raise SystemExit(f"camera did not start: {enabled}")
    save_epoch = enabled["save"]["save_finished_epoch"]
    api_ready_epoch = enabled["ready"].get("elapsed", 0.0) + enabled["save"]["save_finished_epoch"]
    aligner = LiveAligner(round_dir, camera, index, latency=latency, bias=args.schedule_bias)
    ledger = Ledger(LEDGER)
    client = PlatformClient(ORIGIN, timeout=15.0)
    submissions = []
    pending = list(platform_candidates)
    last_snapshot = 0.0
    last_segment_check = 0.0
    started = time.time()
    loop1_started = None
    expected_end = None
    stop_reason = None
    try:
        while True:
            now = time.time()
            if now - last_snapshot >= args.snapshot_interval:
                aligner.capture_snapshot()
                last_snapshot = now
            if now - last_segment_check >= args.segment_interval:
                new_segments = aligner.read_segments(save_epoch)
                if new_segments and aligner.loop_boundaries and loop1_started is None:
                    loop1_started = now
                last_segment_check = now
            model, model_source = aligner.best_model()
            if model is not None and expected_end is None:
                expected_end = model.platform_epoch(config["video_duration"])
            if model is not None:
                for candidate in list(pending):
                    target = model.platform_epoch(candidate["condition_met_video_time"]) + args.schedule_bias
                    if now >= target - 0.05:
                        plan = candidate["request_plan"]
                        submit_started = time.time()
                        outcome = submit_candidate(
                            ledger, client, candidate,
                            duration=plan["duration"],
                            pre_capture=plan["pre_capture"],
                            emit_epoch=target,
                            emit_mode="offline_replay_injection",
                            wait_clip=False,
                            wait_description=False,
                        )
                        submit_finished = time.time()
                        record = {
                            "candidate_id": candidate["candidate_id"],
                            "kind": candidate["kind"],
                            "ledger_key": candidate_key(candidate),
                            "replay_id": replay_id,
                            "loop_index": loop_index,
                            "source_video_time": candidate["source_video_time"],
                            "condition_met_video_time": candidate["condition_met_video_time"],
                            "evidence_start": candidate["evidence_start"],
                            "evidence_end": candidate["evidence_end"],
                            "target_emit_epoch": target,
                            "submit_started_epoch": submit_started,
                            "submit_finished_epoch": submit_finished,
                            "submit_lateness_seconds": submit_started - target,
                            "clock_model_source": model_source,
                            "clock_model_at_submit": model.as_dict(),
                            "request_plan": plan,
                            "outcome": outcome,
                        }
                        submissions.append(record)
                        pending.remove(candidate)
                        print(json.dumps({
                            "submitted": candidate["candidate_id"],
                            "kind": candidate["kind"],
                            "state": outcome.get("state"),
                            "late": round(submit_started - target, 3),
                            "model": model_source,
                            "samples": len(model.residuals),
                        }, ensure_ascii=False), flush=True)
            if loop1_started is not None and now >= loop1_started + args.loop1_hold_seconds:
                stop_reason = "loop_1_detected_and_controlled_stop"
                break
            if expected_end is not None and now >= expected_end + args.tail_seconds:
                stop_reason = "main_replay_end_plus_tail"
                break
            if now - started > args.max_seconds:
                stop_reason = "max_seconds_guard"
                break
            if not pending and expected_end is not None and now >= expected_end + 1.0:
                # Keep sampling until the tail/loop deadline; the loop above
                # still applies.
                pass
            time.sleep(0.05)
    except BaseException:
        controller.set_enabled(False, "main-error-disable")
        raise
    finally:
        ledger.close()
    replay_end_epoch = time.time()
    aligner.read_segments(save_epoch)
    disabled = controller.set_enabled(False, "main-disable")
    time.sleep(3.0)
    aligner.read_segments(save_epoch)

    # Final fit uses only loop 0 segments.  The intra-replay samples prove the
    # mapping did not come from a historical LOOP_START or a wall-clock modulo.
    final_model = aligner.recording_model()
    if final_model is None:
        raise SystemExit("no recording segments decoded for the main replay")
    loop0 = [s for s in aligner.segments if s["loop_index"] == 0]
    loop1 = [s for s in aligner.segments if s["loop_index"] == 1]
    fit = final_model.as_dict()
    replay_manifest = {
        "schema": "unified-platform-replay-v1",
        "created_at_utc": utc_now(),
        "replay_id": replay_id,
        "camera": camera,
        "loop_index": loop_index,
        "platform_origin": ORIGIN,
        "platform_version": (request_json("GET", "/api/version")[1] or {}),
        "source": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": config["source_sha256"],
            "duration": config["video_duration"],
            "fps": config["video_fps"],
            "frame_count": config["video_frame_count"] if "video_frame_count" in config else 3315,
        },
        "derived_input": {
            "container_path": enabled.get("config_input"),
            "input_args": enabled.get("config_input_args"),
            "host_path": str((ROOT / "deploy/frigate/runtime/input/a2_unified_mot20_05_h264.mp4").relative_to(ROOT)),
            "sha256": sha256_file(ROOT / "deploy/frigate/runtime/input/a2_unified_mot20_05_h264.mp4"),
        },
        "config_sha256": sha256_file(ROOT / "deploy/frigate/runtime/config/config.yml"),
        "calibration_latency_seconds": latency,
        "schedule_bias_seconds": args.schedule_bias,
        "config_save_epoch": save_epoch,
        "api_ready_epoch": api_ready_epoch,
        "injection_started_epoch": started,
        "replay_end_epoch": replay_end_epoch,
        "stop_reason": stop_reason,
        "eof_observed": bool(loop1) or (loop0 and loop0[-1]["last_source_time"] >= config["video_duration"] - 2.0),
        "auto_reopen_observed": bool(loop1),
        "loops": [
            {
                "loop_index": 0,
                "first_source_time": loop0[0]["first_source_time"] if loop0 else None,
                "last_source_time": loop0[-1]["last_source_time"] if loop0 else None,
                "segment_count": len(loop0),
                "used_for_events": True,
            },
            *[
                {
                    "loop_index": boundary["loop_index"],
                    "first_source_time": boundary["new_first_source_time"],
                    "first_watermark": boundary.get("first_watermark"),
                    "segment_count": len([s for s in loop1 if s["loop_index"] == boundary["loop_index"]]),
                    "used_for_events": False,
                    "note": boundary["note"],
                }
                for boundary in aligner.loop_boundaries
            ],
        ],
        "loop_boundaries": aligner.loop_boundaries,
        "ffmpeg_cmdline_at_enable": ((enabled.get("stats") or {}).get("ffmpeg_pid")),
        "notes": [
            "Candidates are pre-computed offline results injected at their mapped source times.",
            "This is an offline replay injection, not proof of real-time inference or end-to-end alarm latency.",
            "Clip download and description writes are deferred to the reconciliation phase.",
        ],
    }
    write_json(round_dir / "replay_manifest.json", replay_manifest)
    write_json(round_dir / "alignment" / "clock_alignment.json", {
        "schema": "unified-platform-clock-alignment-v1",
        "created_at_utc": utc_now(),
        "replay_id": replay_id,
        "camera": camera,
        "source_sha256": config["source_sha256"],
        "method": "burned-in frame number OCR + independent image-hash match; recording end_time anchors the last frame",
        "calibration": calibration,
        "live_alignment": aligner.clock_report(),
        "final_recording_fit": fit,
        "final_recording_fit_status": "verified" if final_model.samples >= 2 and final_model.max_abs_residual <= 1.0 else "needs_review",
        "three_point_check": three_point_check(aligner, config),
        "event_adjacent_pending": "computed in unity evidence phase from the downloaded clips",
    })
    with (round_dir / "candidate_event_mapping.jsonl").open("w", encoding="utf-8") as handle:
        for record in submissions:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_json(round_dir / "platform" / "submission_summary.json", {
        "total_candidates": len(platform_candidates),
        "submitted": len(submissions),
        "states": _count_states(submissions),
        "replay_id": replay_id,
        "loop_index": loop_index,
        "stop_reason": stop_reason,
        "loops": replay_manifest["loops"],
    })
    print(json.dumps({
        "replay_id": replay_id,
        "stop_reason": stop_reason,
        "submitted": len(submissions),
        "total": len(platform_candidates),
        "states": _count_states(submissions),
        "loops": replay_manifest["loops"],
        "fit": {k: fit[k] for k in ("t0_epoch", "drift_source_seconds_per_platform_second", "samples", "rmse_seconds", "max_abs_residual_seconds")},
        "three_point_check": replay_manifest.get("three_point_check"),
    }, ensure_ascii=False, indent=2))
    return 0


def three_point_check(aligner: LiveAligner, config: dict) -> dict:
    """Pick start/middle/end segments and check coverage-end anchors.

    The points are fit samples in this runtime report; the archived correction
    report separately provides the 11-fit/3-holdout check.
    """
    samples = sorted(aligner.segment_samples, key=lambda s: s["last_source_time"])
    if not samples:
        return {"status": "failed", "reason": "no_recording_samples"}
    duration = config["video_duration"]

    def nearest(target):
        return min(samples, key=lambda s: abs(s["last_source_time"] - target))

    points = []
    model = aligner.recording_model()
    for label, target in (("start", min(8.0, duration * 0.1)), ("middle", duration * 0.5), ("end", duration * 0.9)):
        sample = nearest(target)
        anchors = sample.get("segment_anchors") or clock_segment_anchors(sample, fps=aligner.index.fps)
        anchor = anchors["coverage_end_anchor"]
        predicted = model.platform_epoch(anchor["source_time"]) if model else None
        points.append({
            "label": label,
            "target_source_time": target,
            "matched_source_time": anchor["source_time"],
            "watermark": sample["decoded"]["last"].get("watermark", {}).get("frame_number"),
            "segment_end_epoch": sample["segment_end_epoch"],
            "predicted_epoch": predicted,
            "residual_seconds": (
                None if predicted is None else anchor["platform_epoch"] - predicted
            ),
            "sample_role": "fit_internal_runtime",
            "frame_period_seconds": 1.0 / aligner.index.fps,
            "methods_agree": sample["decoded"].get("methods_agree"),
        })
    max_residual = max(abs(p["residual_seconds"]) for p in points if p["residual_seconds"] is not None)
    return {
        "status": "verified" if len({p["label"] for p in points}) == 3 and max_residual <= 1.0 + 1e-6 and all(p["methods_agree"] is not False for p in points) else "needs_review",
        "max_abs_residual_seconds": max_residual,
        "max_allowed_residual_seconds": 1.0,
        "sample_role": "fit_internal_runtime",
        "points": points,
        "frame_period_seconds": 1.0 / aligner.index.fps,
        "note": "coverage-end anchors; numeric residual is at frame resolution, not independent physical-clock precision",
    }


def _count_states(submissions: list[dict]) -> dict:
    counts = {}
    for item in submissions:
        state = (item.get("outcome") or {}).get("state", "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def main():
    global RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    parser.add_argument("--camera", default=CAMERA_DEFAULT)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    cal = sub.add_parser("calibrate")
    cal.add_argument("--snapshot-seconds", type=float, default=12.0)
    cal.add_argument("--snapshot-wait-seconds", type=float, default=30.0)
    cal.add_argument("--hold-seconds", type=float, default=20.0)

    inj = sub.add_parser("inject")
    inj.add_argument("--replay-id", required=True)
    inj.add_argument("--loop-index", type=int, default=0)
    inj.add_argument("--zone-version", default="roi-0.05-0.95-v1")
    inj.add_argument("--schedule-bias", type=float, default=0.4)
    inj.add_argument("--snapshot-interval", type=float, default=0.6)
    inj.add_argument("--segment-interval", type=float, default=0.7)
    inj.add_argument("--tail-seconds", type=float, default=12.0)
    inj.add_argument("--loop1-hold-seconds", type=float, default=15.0)
    inj.add_argument("--max-seconds", type=float, default=240.0)

    args = parser.parse_args()
    RUN_DIR = args.run_dir.resolve()
    if args.command == "calibrate":
        return calibrate(args)
    if args.command == "inject":
        return inject(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
