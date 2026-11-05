#!/usr/bin/env python3
"""A2 first same-video / same-zone / same-dwell platform comparison.

Self-developed branch: YOLOv8n + ByteTrack on the exact Frigate input video,
sampled at the platform analysis rate (3 fps), bottom-centre zone membership,
and the same 10-second continuous in-zone dwell definition.

Platform branch: Frigate person events carrying the configured zone, mapped to
source-video time using the input loop start. No labels are invented: matching
is a temporal-overlap diagnostic, not a precision/recall claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

ZONE = [(0.05, 0.05), (0.95, 0.05), (0.95, 0.95), (0.05, 0.95)]


def point_in_polygon(x, y, polygon):
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_self(video, weights, output, sample_fps, dwell_seconds, max_seconds=None):
    import cv2
    import numpy as np
    import supervision as sv
    import torch
    from ultralytics import YOLO

    torch.manual_seed(0)
    np.random.seed(0)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps
    limit_seconds = min(duration, max_seconds) if max_seconds else duration
    stride = max(1, round(fps / sample_fps))
    model = YOLO(str(weights))
    tracker = sv.ByteTrack(frame_rate=sample_fps)
    track_state = {}
    events = []
    samples_path = output / "self_tracks_3fps.jsonl"
    preview_dir = output / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    processed = 0
    with samples_path.open("w", encoding="utf-8") as samples:
        for frame_index in range(frame_count):
            ok, frame = cap.read()
            if not ok:
                break
            video_time = frame_index / fps
            if video_time >= limit_seconds:
                break
            if frame_index % stride:
                continue
            result = model.predict(
                frame, device="0", imgsz=640, conf=0.1, iou=0.7, classes=[0], verbose=False
            )[0]
            detections = sv.Detections.from_ultralytics(result)
            tracked = tracker.update_with_detections(detections)
            processed += 1
            for bbox, score, class_id, track_id in zip(
                tracked.xyxy, tracked.confidence, tracked.class_id, tracked.tracker_id
            ):
                if track_id is None:
                    continue
                track_id = int(track_id)
                box = [float(v) for v in bbox]
                x = (box[0] + box[2]) / 2 / width
                y = box[3] / height
                inside = point_in_polygon(x, y, ZONE)
                samples.write(json.dumps({
                    "frame_index": frame_index,
                    "video_time": round(video_time, 3),
                    "track_id": track_id,
                    "bbox_xyxy": [round(v, 2) for v in box],
                    "score": round(float(score), 4),
                    "in_zone": inside,
                }, ensure_ascii=False) + "\n")
                state = track_state.setdefault(track_id, {
                    "track_id": track_id,
                    "first_time": video_time,
                    "last_time": video_time,
                    "in_zone_since": None,
                    "last_in_zone_time": None,
                    "max_dwell": 0.0,
                    "event": None,
                })
                state["last_time"] = video_time
                if inside:
                    if state["last_in_zone_time"] is None or video_time - state["last_in_zone_time"] > 0.4:
                        state["in_zone_since"] = video_time
                        state["max_dwell"] = 0.0
                    state["last_in_zone_time"] = video_time
                    state["max_dwell"] = video_time - state["in_zone_since"]
                    if state["event"] is None and state["max_dwell"] >= dwell_seconds:
                        state["event"] = {
                            "track_id": track_id,
                            "start_time": round(state["in_zone_since"], 3),
                            "trigger_time": round(video_time, 3),
                            "end_time": None,
                            "duration_at_trigger": round(state["max_dwell"], 3),
                            "score_max": round(float(score), 4),
                        }
                        events.append(state["event"])
                        preview = frame.copy()
                        p1 = (max(0, int(box[0])), max(0, int(box[1])))
                        p2 = (min(width - 1, int(box[2])), min(height - 1, int(box[3])))
                        cv2.rectangle(preview, p1, p2, (0, 255, 0), 2)
                        cv2.putText(
                            preview,
                            f"self long_stay track={track_id} t={video_time:.1f}s",
                            (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2,
                        )
                        cv2.imwrite(
                            str(preview_dir / f"track-{track_id}-t{video_time:.1f}.jpg"),
                            preview,
                        )
                else:
                    if state["event"] is not None and state["event"]["end_time"] is None:
                        state["event"]["end_time"] = round(state["last_in_zone_time"], 3)
                        state["event"]["end_reason"] = "left_zone"
                    state["in_zone_since"] = None
                    state["last_in_zone_time"] = None
                    state["max_dwell"] = 0.0
    cap.release()
    for state in track_state.values():
        if state["event"] is not None and state["event"]["end_time"] is None:
            state["event"]["end_time"] = round(state["last_in_zone_time"] or state["last_time"], 3)
            state["event"]["end_reason"] = "video_end_or_track_lost"
    for event in events:
        event["duration"] = round((event["end_time"] or event["trigger_time"]) - event["start_time"], 3)
    write_json(output / "self_events.json", {
        "video": str(video),
        "video_fps": fps,
        "width": width,
        "height": height,
        "duration_seconds": duration,
        "sample_fps": sample_fps,
        "frame_stride": stride,
        "processed_frames": processed,
        "zone_normalized": ZONE,
        "dwell_seconds": dwell_seconds,
        "events": sorted(events, key=lambda item: item["start_time"]),
        "track_count": len(track_state),
    })
    return {
        "fps": fps, "width": width, "height": height, "duration": duration,
        "processed": processed, "events": events, "tracks": len(track_state),
    }


def platform_events_from_api(origin, output, camera):
    import urllib.parse
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"{origin}/api/events?cameras={camera}&limit=1000"
    with opener.open(url, timeout=30) as response:
        data = json.loads(response.read())
    (output / "platform_events_raw.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=ROOT / "deploy/frigate/runtime/input/public_demo_h264_long.mp4")
    parser.add_argument("--weights", type=Path, default=ROOT / "models/yolov8n.pt")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=3.0)
    parser.add_argument("--dwell-seconds", type=float, default=10.0)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--origin", default="http://127.0.0.1:15000")
    parser.add_argument("--camera", default="a1_public_demo")
    parser.add_argument("--loop-start", type=float, default=1793785296.5,
                        help="epoch of source video t=0 for the first platform loop")
    parser.add_argument("--loop-seconds", type=float, default=280.0)
    parser.add_argument("--match-overlap", type=float, default=2.0)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)

    self_result = run_self(
        args.video, args.weights, args.output, args.sample_fps, args.dwell_seconds, args.max_seconds
    )
    platform = platform_events_from_api(args.origin, args.output, args.camera)
    loop_start = args.loop_start
    loop_end = loop_start + args.loop_seconds
    platform_zone = []
    for event in platform:
        start = event.get("start_time")
        if start is None or not (loop_start <= start < loop_end):
            continue
        if not event.get("zones"):
            continue
        end = event.get("end_time")
        source_start = start - loop_start
        source_end = (min(end, loop_end) if end is not None else loop_end) - loop_start
        platform_zone.append({
            "event_id": event.get("id"),
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "duration": round(source_end - source_start, 3),
            "ongoing": end is None,
            "zones": event.get("zones"),
        })

    self_events = self_result["events"]
    pairs = []
    used_self = set()
    for p_index, p_event in enumerate(sorted(platform_zone, key=lambda item: item["source_start"])):
        best = None
        for s_index, s_event in enumerate(self_events):
            if s_index in used_self:
                continue
            overlap = min(p_event["source_end"], s_event["end_time"] or s_event["trigger_time"]) - max(
                p_event["source_start"], s_event["start_time"]
            )
            if overlap >= args.match_overlap and (best is None or overlap > best[0]):
                best = (overlap, s_index)
        if best is not None:
            used_self.add(best[1])
            pairs.append({
                "platform_event": p_event["event_id"],
                "self_track": self_events[best[1]]["track_id"],
                "overlap_seconds": round(best[0], 3),
                "platform_start": p_event["source_start"],
                "self_start": self_events[best[1]]["start_time"],
            })

    platform_long = [item for item in platform_zone if item["duration"] >= args.dwell_seconds]

    def any_overlap(first_start, first_end, intervals):
        for start, end in intervals:
            if min(first_end, end) - max(first_start, start) >= args.match_overlap:
                return True
        return False

    self_intervals = [(event["start_time"], event["end_time"] or event["trigger_time"]) for event in self_events]
    platform_intervals = [(item["source_start"], item["source_end"]) for item in platform_zone]
    platform_any = sum(
        1 for item in platform_zone
        if any_overlap(item["source_start"], item["source_end"], self_intervals)
    )
    platform_long_any = sum(
        1 for item in platform_long
        if any_overlap(item["source_start"], item["source_end"], self_intervals)
    )
    self_any = sum(
        1 for event in self_events
        if any_overlap(event["start_time"], event["end_time"] or event["trigger_time"], platform_intervals)
    )
    summary = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "video": str(args.video),
        "video_duration_seconds": self_result["duration"],
        "sample_fps": args.sample_fps,
        "dwell_seconds": args.dwell_seconds,
        "zone_normalized": ZONE,
        "loop_start_epoch": loop_start,
        "loop_start_local": datetime.fromtimestamp(loop_start).isoformat(),
        "loop_seconds": args.loop_seconds,
        "platform_zone_events_total": len(platform_zone),
        "platform_zone_events_ge_dwell": len(platform_long),
        "self_long_stay_events": len(self_events),
        "self_unique_tracks": self_result["tracks"],
        "temporal_matches": len(pairs),
        "platform_only": len(platform_zone) - len(pairs),
        "self_only": len(self_events) - len(pairs),
        "platform_events_with_any_self_overlap": platform_any,
        "platform_ge_dwell_events_with_any_self_overlap": platform_long_any,
        "self_events_with_any_platform_overlap": self_any,
        "match_overlap_seconds": args.match_overlap,
        "pairs": pairs,
        "platform_zone_events": platform_zone,
        "caveats": [
            "The A1 demo zone covers almost the whole frame (0.05-0.95), so in-zone dwell mostly measures track time in view, not semantic loitering.",
            "Platform uses Frigate CPU TFLite detector; self-developed branch uses YOLOv8n. This is a full-pipeline comparison, not a rule-only ablation.",
            "Platform analysis runs at 3 fps and the self branch is sampled at 3 fps to match; tracker internals still differ.",
            "Any-overlap coverage is inflated by long tracks (for example self track 3 spans 279.7 s); use one-to-one matches for interpretation.",
            "No independent event ground truth exists for this public clip, so match counts are temporal-overlap diagnostics, not precision/recall.",
        ],
    }
    write_json(args.output / "comparison.json", summary)
    print(json.dumps({
        "output": str(args.output),
        "video_duration_seconds": summary["video_duration_seconds"],
        "platform_zone_events_total": summary["platform_zone_events_total"],
        "platform_zone_events_ge_dwell": summary["platform_zone_events_ge_dwell"],
        "self_long_stay_events": summary["self_long_stay_events"],
        "temporal_matches": summary["temporal_matches"],
        "platform_only": summary["platform_only"],
        "self_only": summary["self_only"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
