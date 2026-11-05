#!/usr/bin/env python3
"""A2/P0: extract pose features from GMDCSA-24 fall/ADL videos.

The run uses YOLOv8n-pose (COCO 17 keypoints).  For each sampled frame the
largest person is used and the following features are stored:

  time, bbox centre/bottom/height, aspect ratio,
  torso angle (hip midpoint -> shoulder midpoint, 0 = horizontal),
  pose confidence.

This is a self-built baseline, not a mature-platform capability test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT_DIR / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_DIR / ".cache/matplotlib"))

DEFAULT_EVENTS = PROJECT_DIR / "data/a2/gmd_csa24/events.csv"
DEFAULT_OUTPUT = PROJECT_DIR / "data/a2/gmd_csa24"
DEFAULT_WEIGHTS = PROJECT_DIR / "models/yolov8n-pose.pt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract GMDCSA-24 pose features")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--stride", type=int, default=3, help="Process every Nth frame")
    parser.add_argument("--device", default="0")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test video limit")
    args = parser.parse_args()

    import cv2
    from ultralytics import YOLO

    events = pd.read_csv(args.events)
    if args.limit:
        events = events.head(args.limit)
    weights = args.weights.resolve(strict=True)
    model = YOLO(str(weights))
    rows = []
    video_records = []
    started = time.perf_counter()
    for index, event in events.iterrows():
        video_path = PROJECT_DIR / event["video_path"]
        if not video_path.is_file():
            video_records.append({"video_id": event["video_id"], "status": "missing_video"})
            continue
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            video_records.append({"video_id": event["video_id"], "status": "unreadable"})
            continue
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frame_height = float(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_width = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_index = 0
        sampled = 0
        video_started = time.perf_counter()
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % args.stride == 0:
                result = model.predict(
                    frame,
                    imgsz=args.imgsz,
                    conf=args.conf,
                    classes=[0],
                    device=args.device,
                    verbose=False,
                )[0]
                if result.boxes is not None and len(result.boxes) > 0:
                    xyxy = result.boxes.xyxy.detach().cpu().numpy()
                    kp = result.keypoints.xy.detach().cpu().numpy()
                    kc = result.keypoints.conf.detach().cpu().numpy()
                    areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
                    person = int(np.argmax(areas))
                    x1, y1, x2, y2 = [float(value) for value in xyxy[person]]
                    width = max(0.0, x2 - x1)
                    height = max(0.0, y2 - y1)
                    shoulders = (kp[person, 5] + kp[person, 6]) / 2.0
                    hips = (kp[person, 11] + kp[person, 12]) / 2.0
                    vector = shoulders - hips
                    torso_angle = float(
                        np.degrees(np.arctan2(abs(vector[1]), abs(vector[0]) + 1e-6))
                    )
                    pose_conf = float(np.mean(kc[person, [5, 6, 11, 12]]))
                    rows.append(
                        {
                            "video_id": event["video_id"],
                            "subject": int(event["subject"]),
                            "kind": event["kind"],
                            "file_name": event["file_name"],
                            "time": frame_index / fps if fps > 0 else 0.0,
                            "frame_index": frame_index,
                            "frame_width": frame_width,
                            "frame_height": frame_height,
                            "box_x1": x1,
                            "box_y1": y1,
                            "box_x2": x2,
                            "box_y2": y2,
                            "box_width": width,
                            "box_height": height,
                            "aspect": width / height if height > 0 else 0.0,
                            "center_y": (y1 + y2) / 2.0,
                            "bottom_y": y2,
                            "bottom_ratio": y2 / frame_height if frame_height > 0 else 0.0,
                            "torso_angle": torso_angle,
                            "pose_conf": pose_conf,
                        }
                    )
                    sampled += 1
            frame_index += 1
        cap.release()
        video_records.append(
            {
                "video_id": event["video_id"],
                "status": "processed",
                "fps": fps,
                "frames": frame_index,
                "sampled": sampled,
                "wall_seconds": time.perf_counter() - video_started,
            }
        )
        if (index + 1) % 10 == 0:
            print(f"processed {index + 1}/{len(events)} videos, rows={len(rows)}", flush=True)

    features = pd.DataFrame(rows)
    features.to_csv(args.output / "pose_features.csv", index=False)
    summary = {
        "schema": "a2-gmd-pose-features-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events_csv": str(args.events.relative_to(PROJECT_DIR)),
        "weights": str(weights.relative_to(PROJECT_DIR)),
        "weights_sha256": sha256_file(weights),
        "stride": args.stride,
        "imgsz": args.imgsz,
        "conf": args.conf,
        "device": args.device,
        "videos_requested": len(events),
        "videos_processed": sum(1 for item in video_records if item["status"] == "processed"),
        "feature_rows": len(features),
        "wall_seconds": time.perf_counter() - started,
        "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "video_records": video_records,
        "limitations": [
            "Largest-person selection only; no multi-person identity assignment.",
            "Pose features are sampled every Nth frame; events are interpolated only by the evaluation hysteresis.",
            "This is a self-built baseline, not a mature-platform validation.",
        ],
    }
    (args.output / "pose_feature_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in ("videos_processed", "feature_rows", "wall_seconds")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
