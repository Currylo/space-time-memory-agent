#!/usr/bin/env python3
"""Reconcile one controlled same-source replay into evidence and a page dataset.

This phase is deliberately separate from the injection scheduler.  It downloads
clips only after all candidate events have ended, verifies their burned-in frame
numbers and coverage, writes provenance descriptions, and produces:

* candidate_event_mapping.jsonl (one line per frozen candidate)
* evidence_manifest.json
* platform/evidence_summary.json
* platform/no_trigger_window.json
* demo/data/unified_platform.json

It never submits a new candidate event and never writes a human review.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_memory.a2_adapter import (  # noqa: E402
    Ledger,
    PlatformClient,
    candidate_key,
    description_for,
    sha256_file,
)
from st_memory.unified_platform import (  # noqa: E402
    ClockModel,
    SourceIndex,
    annotate_causal_stay_events,
    evidence_coverage,
    source_frame_number,
    summarize_replay,
)

ORIGIN = "http://127.0.0.1:15000"
LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
SOURCE = ROOT / "data/a2/unified/MOT20-05-raw.mp4"
RUN_DIR = ROOT / "runs/unified-mot20-05-detect-pose-v2-001"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(url: str, timeout: float = 60.0) -> tuple[int | None, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except Exception:  # noqa: BLE001
        return None, b""


def request_json(method: str, path: str, body=None, timeout: float = 30.0):
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


def decode_clip_samples(path: Path, index: SourceIndex, positions: list[int] | None = None) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"decoded": False, "error": "cannot_open"}
    fps = float(cap.get(cv2.CAP_PROP_FPS) or index.fps)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if count <= 0:
        cap.release()
        return {"decoded": False, "error": "no_frame_count"}
    if positions is None:
        positions = sorted(set([
            0,
            max(0, count // 4),
            max(0, count // 2),
            max(0, (count * 3) // 4),
            count - 1,
        ]))
    samples = []
    for position in positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, position)
        ok, frame = cap.read()
        if not ok:
            samples.append({"position": position, "decoded": False})
            continue
        match = index.match(frame)
        samples.append({"position": position, "decoded": True, **match})
    cap.release()
    first = samples[0] if samples else {}
    last = samples[-1] if samples else {}

    def st(sample):
        if not sample:
            return None
        return sample.get("watermark_source_time", sample.get("source_time"))

    clip_start = st(first)
    clip_end = st(last)
    residuals = []
    for sample in samples:
        if not sample.get("decoded") or clip_start is None:
            continue
        expected = clip_start + sample["position"] / fps
        actual = st(sample)
        if actual is not None:
            residuals.append({
                "position": sample["position"],
                "clip_time": sample["position"] / fps,
                "expected_source_time": round(expected, 6),
                "actual_source_time": round(actual, 6),
                "residual_seconds": round(actual - expected, 6),
                "watermark": (sample.get("watermark") or {}).get("frame_number"),
                "methods_agree": sample.get("methods_agree"),
            })
    return {
        "decoded": True,
        "fps": fps,
        "frame_count": count,
        "duration": count / fps,
        "samples": samples,
        "clip_source_start": clip_start,
        "clip_source_end": clip_end,
        "clip_watermark_start": (first.get("watermark") or {}).get("frame_number"),
        "clip_watermark_end": (last.get("watermark") or {}).get("frame_number"),
        "intra_clip_residuals": residuals,
        "max_abs_intra_residual": max((abs(r["residual_seconds"]) for r in residuals), default=None),
        "methods_agree_all": all(s.get("methods_agree") is not False for s in samples if s.get("decoded")),
    }


def draw_source_evidence(candidate: dict, clip_path: Path, clip_info: dict, observations: list[dict], output: Path, index: SourceIndex):
    import cv2
    import numpy as np

    evidence_time = float(candidate["evidence_frame_video_time"])
    cap = cv2.VideoCapture(str(SOURCE))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(evidence_time * fps)))
    ok, source_frame = cap.read()
    cap.release()
    if not ok:
        return {"drawn": False, "error": "source_frame_decode_failed"}
    roi = [0.05, 0.05, 0.95, 0.95]
    h, w = source_frame.shape[:2]
    x1, y1, x2, y2 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
    cv2.rectangle(source_frame, (x1, y1), (x2, y2), (190, 210, 40), 2)
    # nearest observation row for the target/region annotation
    nearest = min(observations, key=lambda row: abs(row["time"] - evidence_time))
    target_box = None
    target_id = candidate.get("track_id")
    if target_id is not None:
        for person in nearest.get("people", []):
            if person.get("id") == target_id:
                box = person["box"]
                target_box = [int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)]
                cv2.rectangle(source_frame, (target_box[0], target_box[1]), (target_box[2], target_box[3]), (0, 255, 255), 3)
                cv2.putText(source_frame, f"track {target_id}", (target_box[0], max(18, target_box[1] - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
                break
    cv2.putText(source_frame, f"SOURCE t={evidence_time:.2f}s count={nearest.get('count')}", (14, h - 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    # clip frame near the evidence time
    clip_frame = None
    clip_time = None
    if clip_info.get("decoded") and clip_info.get("clip_source_start") is not None:
        clip_time = evidence_time - clip_info["clip_source_start"]
        if 0 <= clip_time <= clip_info["duration"]:
            cap = cv2.VideoCapture(str(clip_path))
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(clip_time * clip_info["fps"])))
            ok, frame = cap.read()
            cap.release()
            if ok:
                clip_frame = frame
    if clip_frame is None:
        clip_frame = np.zeros_like(source_frame)
        cv2.putText(clip_frame, "CLIP FRAME NOT AVAILABLE", (40, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    else:
        cv2.putText(clip_frame, f"PLATFORM CLIP t={evidence_time:.2f}s", (14, h - 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    combined = np.concatenate([source_frame, clip_frame], axis=1)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), combined)
    return {
        "drawn": True,
        "path": str(output.relative_to(ROOT)),
        "evidence_time": evidence_time,
        "nearest_observation_time": nearest["time"],
        "region": roi,
        "target_track_id": target_id,
        "target_box_pixels": target_box,
        "source_count": nearest.get("count"),
        "clip_time": clip_time,
    }


def window_evidence(candidate: dict, observations: list[dict], output_json: Path, output_png: Path, config: dict) -> dict:
    import cv2
    import numpy as np

    kind = candidate["kind"]
    start = float(candidate.get("rule_window_start", candidate["evidence_start"]))
    end = float(candidate.get("rule_window_end", candidate["evidence_end"]))
    rows = [row for row in observations if start - 1e-6 <= row["time"] <= end + 1e-6]
    if kind == "crowd":
        crowd = config["crowd"]
        to_at_least = int(crowd["to_at_least"])
        from_below = int(crowd["from_below"])
        window_seconds = float(crowd["window_seconds"])
        sustain_seconds = float(crowd["sustain_seconds"])
        points = [{"time": row["time"], "count": row["count"]} for row in rows]
        sustain_start = end - sustain_seconds
        sustain_counts = [row["count"] for row in rows if row["time"] >= sustain_start - 1e-6]
        from_count = candidate["features"].get("from_count")
        to_count = candidate["features"].get("to_count")
        ok = (
            bool(sustain_counts)
            and (end - start) + 1e-6 >= window_seconds
            and int(from_count or 0) < from_below
            and min(sustain_counts) >= to_at_least
            and max((row["count"] for row in rows), default=0) >= to_at_least
        )
        result = {
            "kind": "crowd",
            "window_start": start,
            "window_end": end,
            "window_seconds": end - start,
            "comparison_window_start": start,
            "comparison_window_end": end,
            "comparison_window_seconds": round(end - start, 6),
            "required_coverage_start": candidate.get("required_coverage_start", start),
            "required_coverage_end": candidate.get("required_coverage_end", candidate["condition_met_video_time"]),
            "required_coverage_seconds": candidate.get("required_coverage_seconds", round(candidate["condition_met_video_time"] - start, 6)),
            "thresholds": {
                "window_seconds": window_seconds,
                "from_below": from_below,
                "to_at_least": to_at_least,
                "sustain_seconds": sustain_seconds,
            },
            "from_count": from_count,
            "to_count": to_count,
            "sustain_start": sustain_start,
            "sustain_min_count": min(sustain_counts) if sustain_counts else None,
            "sustain_samples": len(sustain_counts),
            "condition_ok": ok,
            "sample_count": len(points),
            "points": points,
        }
        canvas = np.full((260, 900, 3), 245, dtype=np.uint8)
        max_count = max(1, max((p["count"] for p in points), default=1))
        for i in range(1, len(points)):
            x1 = int((points[i - 1]["time"] - start) / max(1e-9, end - start) * 840) + 30
            y1 = 220 - int(points[i - 1]["count"] / max_count * 180)
            x2 = int((points[i]["time"] - start) / max(1e-9, end - start) * 840) + 30
            y2 = 220 - int(points[i]["count"] / max_count * 180)
            cv2.line(canvas, (x1, y1), (x2, y2), (40, 130, 150), 2)
        threshold_y = 220 - int(to_at_least / max_count * 180)
        cv2.line(canvas, (30, threshold_y), (870, threshold_y), (80, 120, 200), 1)
        cv2.putText(canvas, f"60s window {start:.1f}-{end:.1f}s  from={from_count} to={to_count}  threshold=>={to_at_least}",
                    (30, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (30, 40, 50), 1, cv2.LINE_AA)
        cv2.putText(canvas, "threshold", (875, threshold_y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 120, 200), 1, cv2.LINE_AA)
        cv2.imwrite(str(output_png), canvas)
        result["chart"] = str(output_png.relative_to(ROOT))
    elif kind == "stay":
        target = candidate.get("track_id")
        points = []
        for row in rows:
            person = next((p for p in row.get("people", []) if p.get("id") == target), None)
            if person is not None:
                points.append({
                    "time": row["time"],
                    "inside": bool(person.get("inside")),
                    "dwell": float(person.get("dwell", 0.0)),
                    "box": person.get("box"),
                })
        threshold = float(candidate["features"].get("threshold_seconds", 10.0))
        sample_interval = 0.2
        max_dwell = max((p["dwell"] for p in points), default=0.0)
        first_dwell = points[0]["dwell"] if points else None
        valid_sample_count = len(points)
        valid_seconds = round(valid_sample_count * sample_interval, 6)
        condition = float(candidate["condition_met_video_time"])
        span = condition - start
        result = {
            "kind": "stay",
            "track_id": target,
            "window_start": start,
            "window_end": end,
            "comparison_window_start": start,
            "comparison_window_end": end,
            "comparison_window_seconds": round(end - start, 6),
            "required_coverage_start": candidate.get("required_coverage_start", start),
            "required_coverage_end": candidate.get("required_coverage_end", condition),
            "required_coverage_seconds": candidate.get("required_coverage_seconds", round(condition - start, 6)),
            "condition_met_video_time": condition,
            "condition_in_evidence": False,
            "observed_seconds": points[-1]["time"] - points[0]["time"] if points else 0.0,
            "valid_sample_count": valid_sample_count,
            "valid_seconds": valid_seconds,
            "span_seconds": round(span, 6),
            "missing_gap_seconds": round(max(0.0, span - valid_seconds), 6),
            "max_dwell": max_dwell,
            "first_dwell_seconds": first_dwell,
            "threshold_seconds": threshold,
            "condition_ok": (
                valid_sample_count >= int(round(threshold / sample_interval))
                and first_dwell is not None
                and first_dwell <= sample_interval + 1e-6
                and max_dwell + 1e-6 >= threshold
                and all(p["inside"] for p in points)
            ),
            "sample_count": len(points),
            "points": points,
        }
        canvas = np.full((260, 900, 3), 245, dtype=np.uint8)
        threshold = float(candidate["features"].get("threshold_seconds", 10.0))
        max_y = max(threshold * 1.2, max((p["dwell"] for p in points), default=threshold))
        for i in range(1, len(points)):
            x1 = int((points[i - 1]["time"] - start) / max(1e-9, end - start) * 840) + 30
            y1 = 220 - int(points[i - 1]["dwell"] / max_y * 180)
            x2 = int((points[i]["time"] - start) / max(1e-9, end - start) * 840) + 30
            y2 = 220 - int(points[i]["dwell"] / max_y * 180)
            cv2.line(canvas, (x1, y1), (x2, y2), (40, 130, 150), 2)
        threshold_y = 220 - int(threshold / max_y * 180)
        cv2.line(canvas, (30, threshold_y), (870, threshold_y), (80, 120, 200), 1)
        cv2.putText(canvas, f"track {target} valid {valid_seconds:.2f}s / span {span:.2f}s / gap {max(0.0, span-valid_seconds):.2f}s / threshold {threshold:.1f}s",
                    (30, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (30, 40, 50), 1, cv2.LINE_AA)
        cv2.imwrite(str(output_png), canvas)
        result["chart"] = str(output_png.relative_to(ROOT))
    else:
        result = {"kind": kind, "note": "no candidate of this kind in the frozen run", "condition_ok": None}
    write_json(output_json, result)
    return result


def main() -> int:
    global RUN_DIR
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    parser.add_argument("--camera", default="a2_unified_mot20_05")
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--profile", choices=("baseline", "coverage"), default="baseline")
    parser.add_argument("--no-trigger-start", type=float, default=60.0)
    parser.add_argument("--no-trigger-end", type=float, default=90.0)
    args = parser.parse_args()
    RUN_DIR = args.run_dir.resolve()
    round_dir = args.round_dir.resolve()
    for sub in ("clips", "alignment", "platform"):
        (round_dir / sub).mkdir(parents=True, exist_ok=True)

    manifest = read_json(round_dir / "replay_manifest.json")
    alignment = read_json(round_dir / "alignment" / "clock_alignment.json")
    result = read_json(RUN_DIR / "result.json")
    config = read_json(RUN_DIR / "config.json")
    config["video_duration"] = float(result["source"]["duration"])
    config["video_frame_count"] = int(result["source"]["frame_count"])
    config["source_name"] = result["source"]["name"]
    events = read_json(RUN_DIR / "candidates.json")
    replay_id = manifest["replay_id"]
    camera = args.camera
    fit = alignment["final_recording_fit"]
    clock = ClockModel(t0=fit["t0_epoch"], drift=fit["drift_source_seconds_per_platform_second"], samples=fit["samples"], rmse=fit["rmse_seconds"], max_abs_residual=fit["max_abs_residual_seconds"])

    observations = [json.loads(line) for line in (RUN_DIR / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    events = annotate_causal_stay_events(events, observations, config)
    platform_candidates = summarize_replay(
        events, config, result["run_id"], camera=camera, replay_id=replay_id,
        loop_index=int(manifest["loop_index"]), zone_version="roi-0.05-0.95-v1",
    )
    platform_candidates.sort(key=lambda c: c["condition_met_video_time"])

    status, event_payload = request_json("GET", f"/api/events?cameras={camera}&limit=1000")
    all_events = event_payload if isinstance(event_payload, list) else []
    round_sub_labels = {"a2:" + candidate_key(c)[:24] for c in platform_candidates}
    manual_events = {
        event["id"]: event for event in all_events
        if (event.get("data") or {}).get("type") == "api" and event.get("sub_label") in round_sub_labels
    }
    native_events = [event for event in all_events if (event.get("data") or {}).get("type") != "api"]
    ledger = Ledger(LEDGER)
    client = PlatformClient(ORIGIN, timeout=30.0)
    index = SourceIndex(SOURCE, fps=config["video_fps"])

    records = []
    clips_manifest = []
    failures = []
    description_writes = []
    try:
        for candidate in platform_candidates:
            key = candidate_key(candidate)
            row = ledger.get(key)
            record = {
                "candidate_id": candidate["candidate_id"],
                "kind": candidate["kind"],
                "rule_id": candidate["rule_id"],
                "rule_version": candidate["rule_version"],
                "zone_id": candidate["zone_id"],
                "zone_version": candidate["zone_version"],
                "camera": camera,
                "source_video_sha256": candidate["source_video_sha256"],
                "run_id": candidate["run_id"],
                "replay_id": replay_id,
                "loop_index": candidate["loop_index"],
                "track_id": candidate["track_id"],
                "source_video_time": candidate["source_video_time"],
                "condition_met_video_time": candidate["condition_met_video_time"],
                "evidence_frame_video_time": candidate["evidence_frame_video_time"],
                "evidence_start": candidate["evidence_start"],
                "evidence_end": candidate["evidence_end"],
                "rule_window_start": candidate["rule_window_start"],
                "rule_window_end": candidate["rule_window_end"],
                "features": candidate["features"],
                "score": None,
                "score_type": candidate["score_type"],
                "ledger_key": key,
                "ledger_state": row.get("status") if row else "not_submitted",
                "event_id": row.get("event_id") if row else None,
                "submit_state": "not_submitted",
                "review_status": row.get("review_status") if row else "pending_review",
                "reviews": ledger.reviews(key) if row else [],
                "platform": {},
                "clip": {},
                "evidence": {},
            }
            for field in ("required_coverage_start", "required_coverage_end", "required_coverage_seconds", "comparison_window_start", "comparison_window_end", "comparison_window_seconds"):
                record[field] = candidate[field]
            if row is None:
                record["notes"] = ["candidate has no ledger row; not submitted"]
                records.append(record)
                failures.append({"candidate_id": candidate["candidate_id"], "stage": "ledger", "error": "no ledger row"})
                continue
            event_id = row.get("event_id")
            event = manual_events.get(event_id)
            if event is None:
                # The event may be outside the 1000-event API page; fetch it directly.
                fetch_status, event = request_json("GET", f"/api/events/{event_id}")
                if fetch_status != 200 or not isinstance(event, dict):
                    record["submit_state"] = row.get("status") or "unknown"
                    record["notes"] = [f"platform event not found: {fetch_status}"]
                    records.append(record)
                    failures.append({"candidate_id": candidate["candidate_id"], "stage": "event", "error": f"event {event_id} not found"})
                    continue
            event_source_start = clock.source_time(float(event["start_time"]))
            event_source_end = clock.source_time(float(event["end_time"])) if event.get("end_time") is not None else None
            record["submit_state"] = "created"
            record["event_id"] = event_id
            record["platform"] = {
                "event_id": event_id,
                "event_start_epoch": event["start_time"],
                "event_end_epoch": event.get("end_time"),
                "event_source_start": round(event_source_start, 6),
                "event_source_end": None if event_source_end is None else round(event_source_end, 6),
                "start_margin_seconds": round(candidate["evidence_start"] - event_source_start, 6),
                "end_margin_seconds": None if event_source_end is None else round(event_source_end - candidate["evidence_end"], 6),
                "has_clip": bool(event.get("has_clip")),
                "has_snapshot": bool(event.get("has_snapshot")),
                "sub_label": event.get("sub_label"),
                "type": (event.get("data") or {}).get("type"),
                "camera": event.get("camera"),
            }
            if event.get("end_time") is not None:
                ledger.mark_completed(key, event)
                record["ledger_state"] = "completed"
            # Deferred description/provenance write.
            description = description_for(candidate, key, "offline_replay_injection") + f"; event_id={event_id}"
            desc_status, desc_body = client.set_description(event_id, description)
            description_writes.append({"candidate_id": candidate["candidate_id"], "event_id": event_id, "status": desc_status, "body": desc_body})
            record["platform"]["description_status"] = desc_status
            # Clip download.  All events have ended by now; retry transient
            # 404/empty responses instead of treating the first one as final.
            clip_path = round_dir / "clips" / f"{candidate['candidate_id']}.mp4"
            clip_status, clip_body = None, b""
            for attempt in range(12):
                clip_status, clip_body = fetch(f"{ORIGIN}/api/events/{event_id}/clip.mp4", timeout=120.0)
                if clip_status == 200 and len(clip_body) > 1000:
                    break
                time.sleep(2.0)
            if clip_status == 200 and len(clip_body) > 1000:
                clip_path.write_bytes(clip_body)
                clip_info = decode_clip_samples(clip_path, index)
                coverage = evidence_coverage(
                    evidence_start=candidate.get("required_coverage_start", candidate["rule_window_start"]),
                    evidence_end=candidate.get("required_coverage_end", candidate["rule_window_end"]),
                    clip_source_start=clip_info.get("clip_source_start"),
                    clip_source_end=clip_info.get("clip_source_end"),
                    clip_duration=clip_info.get("duration"),
                    clip_fps=clip_info.get("fps", 25.0),
                    comparison_window_seconds=candidate.get("comparison_window_seconds"),
                )
                record["clip"] = {
                    "http_status": clip_status,
                    "bytes": len(clip_body),
                    "sha256": hashlib.sha256(clip_body).hexdigest(),
                    "path": str(clip_path.relative_to(ROOT)),
                    "url": f"/api/unified/clip/{candidate['candidate_id']}",
                    **clip_info,
                    "coverage": coverage,
                }
                record["evidence"]["coverage"] = coverage
                if not clip_info.get("decoded"):
                    failures.append({"candidate_id": candidate["candidate_id"], "stage": "clip_decode", "error": clip_info.get("error")})
                elif coverage["status"] != "verified":
                    failures.append({"candidate_id": candidate["candidate_id"], "stage": "clip_coverage", "error": coverage["problems"]})
                clips_manifest.append({"candidate_id": candidate["candidate_id"], "event_id": event_id, **record["clip"]})
            else:
                record["clip"] = {"http_status": clip_status, "bytes": len(clip_body), "error": "clip_not_downloadable"}
                record["evidence"]["coverage"] = {"status": "missing", "problems": ["clip_not_downloadable"]}
                failures.append({"candidate_id": candidate["candidate_id"], "stage": "clip_download", "error": f"HTTP {clip_status}"})
            # Evidence frame / target / region image.
            evidence_image = round_dir / "alignment" / f"{candidate['candidate_id']}-evidence.jpg"
            if record["clip"].get("decoded"):
                record["evidence"]["image"] = draw_source_evidence(
                    candidate, clip_path, record["clip"], observations, evidence_image, index
                )
            # Window evidence.
            window_json = round_dir / "alignment" / f"{candidate['candidate_id']}-window.json"
            window_png = round_dir / "alignment" / f"{candidate['candidate_id']}-window.png"
            record["evidence"]["window"] = window_evidence(candidate, observations, window_json, window_png, config)
            record["evidence"]["status"] = (
                "verified"
                if record["clip"].get("coverage", {}).get("status") == "verified"
                and record["evidence"]["window"].get("condition_ok") is True
                else "partial_or_failed"
            )
            if args.profile != "baseline":
                record["clip"]["url"] = f"/api/unified/clip/{candidate['candidate_id']}?profile={args.profile}"
            records.append(record)

        # Explicit no-trigger window check.  A crowd candidate's 60 s evidence
        # clip legitimately overlaps a later time window; that is not a new
        # trigger.  The trigger time is the condition-met time, so check that.
        window_start = args.no_trigger_start
        window_end = args.no_trigger_end
        record_by_event = {record["event_id"]: record for record in records}
        manual_conditions_in_window = []
        manual_clip_overlaps = []
        for event in manual_events.values():
            record = record_by_event.get(event["id"])
            if record and window_start <= record["condition_met_video_time"] <= window_end:
                manual_conditions_in_window.append(event["id"])
            event_start = clock.source_time(float(event["start_time"]))
            event_end = clock.source_time(float(event["end_time"])) if event.get("end_time") is not None else event_start
            if event_end >= window_start and event_start <= window_end:
                manual_clip_overlaps.append(event["id"])
        native_in_window = []
        for event in native_events:
            event_start = clock.source_time(float(event["start_time"]))
            event_end = clock.source_time(float(event["end_time"])) if event.get("end_time") is not None else event_start
            if event_end >= window_start and event_start <= window_end:
                native_in_window.append(event["id"])
        ledger_in_window = [
            record for record in records
            if window_start <= record["condition_met_video_time"] <= window_end
        ]
        no_trigger = {
            "schema": "unified-platform-no-trigger-v1",
            "window_source_seconds": [window_start, window_end],
            "manual_api_event_conditions_in_window": manual_conditions_in_window,
            "manual_api_event_condition_count": len(manual_conditions_in_window),
            "manual_api_clip_overlaps_in_window": manual_clip_overlaps,
            "manual_api_clip_overlap_note": (
                "Crowd candidates use a complete 60 s evidence clip, so their clips can overlap "
                "a window in which no new condition is met. Overlap is evidence coverage, not a trigger."
            ),
            "ledger_candidates_in_window": [r["candidate_id"] for r in ledger_in_window],
            "native_object_events_in_window_count": len(native_in_window),
            "native_object_events_in_window_note": (
                "Frigate's stock object tracker created these events because snapshots are globally enabled; "
                "they are has_clip=false object events, not external candidate events. They are excluded from "
                "candidate mapping and are not counted as a candidate trigger."
            ),
            "pass": len(manual_conditions_in_window) == 0 and len(ledger_in_window) == 0,
        }
        write_json(round_dir / "platform" / "no_trigger_window.json", no_trigger)

        # Complete mapping file (one line per frozen candidate).
        with (round_dir / "candidate_event_mapping.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        summary = {
            "schema": "unified-platform-evidence-summary-v1",
            "created_at_utc": utc_now(),
            "replay_id": replay_id,
            "camera": camera,
            "total_candidates": len(platform_candidates),
            "submit_states": _counts(r["submit_state"] for r in records),
            "evidence_states": _counts(r["evidence"].get("status", "missing") for r in records),
            "kinds": _counts(r["kind"] for r in records),
            "clips_downloaded": sum(1 for r in records if r["clip"].get("http_status") == 200),
            "clips_decoded": sum(1 for r in records if r["clip"].get("decoded")),
            "coverage_verified": sum(1 for r in records if r["clip"].get("coverage", {}).get("status") == "verified"),
            "window_condition_verified": sum(1 for r in records if r["evidence"].get("window", {}).get("condition_ok") is True),
            "pending_review": sum(1 for r in records if r["review_status"] == "pending_review"),
            "reviews_present": sum(1 for r in records if r["reviews"]),
            "manual_api_events": len(manual_events),
            "native_object_events": len(native_events),
            "description_writes_ok": sum(1 for item in description_writes if item["status"] == 200),
            "failures": failures,
        }
        write_json(round_dir / "platform" / "evidence_summary.json", summary)

        evidence_manifest = {
            "schema": "unified-platform-evidence-manifest-v1",
            "created_at_utc": utc_now(),
            "round_id": round_dir.name,
            "replay": {
                "replay_id": replay_id,
                "camera": camera,
                "loop_index": manifest["loop_index"],
                "stop_reason": manifest["stop_reason"],
                "eof_observed": manifest["eof_observed"],
                "auto_reopen_observed": manifest["auto_reopen_observed"],
            },
            "source": {
                "path": str(SOURCE.relative_to(ROOT)),
                "sha256": config["source_sha256"],
                "duration": config["video_duration"],
                "fps": config["video_fps"],
                "frame_count": config["video_frame_count"],
            },
            "derived_input": manifest["derived_input"],
            "clock": {
                "t0_epoch": fit["t0_epoch"],
                "drift_source_seconds_per_platform_second": fit["drift_source_seconds_per_platform_second"],
                "samples": fit["samples"],
                "rmse_seconds": fit["rmse_seconds"],
                "max_abs_residual_seconds": fit["max_abs_residual_seconds"],
                "latency_seconds": alignment["calibration"]["latency_seconds"],
                "latency_seconds_used_in_injection": manifest["calibration_latency_seconds"],
                "frame_period_seconds": 1.0/config["video_fps"],
                "three_point_check": alignment["three_point_check"],
                "status": alignment["final_recording_fit_status"],
            },
            "clips": clips_manifest,
            "candidates": records,
            "no_trigger_window": no_trigger,
            "description_writes": description_writes,
            "failures": failures,
            "limits": [
                "Rules are model-derived candidates, not GT.",
                "Fall output is zero; this is not proof that no fall occurred.",
                "Candidates were injected from frozen offline results at mapped source times; this is not real-time inference or end-to-end alarm latency.",
                "Manual Frigate events do not create native Review items; review history is the SQLite ledger.",
                "Native objects and external candidates from other replays are excluded from this replay's candidate counts.",
            ],
        }
        write_json(round_dir / "evidence_manifest.json", evidence_manifest)

        # Page dataset.  Keep media URLs relative to the 8765 origin.
        page_candidates = []
        for record in records:
            page_candidates.append({
                **record,
                "platform_event_url": f"http://127.0.0.1:15000/explore?event_id={record['event_id']}",
                "source_url": "/media/unified/original.mp4",
                "clip_url": record["clip"].get("url"),
                "window_chart_url": f"/api/unified/alignment/{record['candidate_id']}-window.png",
                "evidence_image_url": f"/api/unified/alignment/{record['candidate_id']}-evidence.jpg",
            })
        page_data = {
            "schema": "same-source-platform-v1",
            "round_id": round_dir.name,
            "generated_at_utc": utc_now(),
            "replay": manifest,
            "clock": evidence_manifest["clock"],
            "source": evidence_manifest["source"],
            "input": evidence_manifest["derived_input"],
            "platform": {
                "origin": ORIGIN,
                "version": (request_json("GET", "/api/version")[1] or {}),
                "manual_api_events": len(manual_events),
                "native_object_events": len(native_events),
                "online": True,
            },
            "summary": summary,
            "candidates": page_candidates,
            "no_trigger_window": no_trigger,
            "limits": evidence_manifest["limits"],
        }
        if args.profile != "baseline":
            for record in page_data["candidates"]:
                for field in ("window_chart_url", "evidence_image_url"):
                    if record.get(field):
                        record[field] += f"?profile={args.profile}"
            page_data["profile"] = args.profile
        page_data["clock"]["frame_period_seconds"] = 1.0/config["video_fps"]
        destination = "unified_platform.json" if args.profile == "baseline" else f"unified_platform_{args.profile}.json"
        write_json(ROOT / "demo" / "data" / destination, page_data)
        write_json(round_dir / "platform" / "page_dataset_snapshot.json", page_data)
        print(json.dumps({
            "total_candidates": summary["total_candidates"],
            "submit_states": summary["submit_states"],
            "evidence_states": summary["evidence_states"],
            "clips_decoded": summary["clips_decoded"],
            "coverage_verified": summary["coverage_verified"],
            "window_condition_verified": summary["window_condition_verified"],
            "pending_review": summary["pending_review"],
            "reviews_present": summary["reviews_present"],
            "manual_api_events": summary["manual_api_events"],
            "native_object_events": summary["native_object_events"],
            "no_trigger_pass": no_trigger["pass"],
            "failures": failures,
        }, ensure_ascii=False, indent=2))
    finally:
        ledger.close()
    return 0


def _counts(values) -> dict:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
