#!/usr/bin/env python3
"""A2/P1: strict tracker-only comparison on fixed MOT17 detections.

Both trackers receive exactly the same per-frame YOLOv8n detections from the
existing ByteTrack run.  No detector is re-run.  ByteTrack and BoT-SORT use
Ultralytics' tracker implementations and their default YAML parameters; this
isolates association from detection, unlike the earlier model.track reference.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
from st_memory.records import sha256  # noqa: E402

from ultralytics.trackers.bot_sort import BOTSORT  # noqa: E402
from ultralytics.trackers.byte_tracker import BYTETracker  # noqa: E402
from ultralytics.utils import IterableSimpleNamespace, yaml_load  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_detections(path: Path) -> dict[int, list[dict]]:
    data = defaultdict(list)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            frame = int(row["frame_index"]) + 1
            for box, score, cls in zip(row["bbox_xyxy"], row["score"], row["class_id"]):
                if int(cls) != 0:
                    continue
                x1, y1, x2, y2 = [float(value) for value in box]
                width = max(0.0, x2 - x1)
                height = max(0.0, y2 - y1)
                if width <= 0 or height <= 0:
                    continue
                data[frame].append({"xyxy": [x1, y1, x2, y2], "score": float(score), "cls": 0})
    return data


class DetectionResults:
    """Minimal results object with the numpy fields BYTETracker/BOTSORT read."""

    def __init__(self, rows: list[dict]):
        if not rows:
            self.conf = np.zeros((0,), dtype=np.float32)
            self.cls = np.zeros((0,), dtype=np.float32)
            self.xywh = np.zeros((0, 4), dtype=np.float32)
            return
        xyxy = np.asarray([row["xyxy"] for row in rows], dtype=np.float32)
        width = xyxy[:, 2] - xyxy[:, 0]
        height = xyxy[:, 3] - xyxy[:, 1]
        self.xywh = np.column_stack(
            ((xyxy[:, 0] + xyxy[:, 2]) / 2.0, (xyxy[:, 1] + xyxy[:, 3]) / 2.0, width, height)
        ).astype(np.float32)
        self.conf = np.asarray([row["score"] for row in rows], dtype=np.float32)
        self.cls = np.asarray([row["cls"] for row in rows], dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict tracker-only MOT17 comparison")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/a2_mot17_tracker_only.json")
    parser.add_argument("--trackers", nargs="*", default=["bytetrack", "botsort"])
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = json.loads((PROJECT_DIR / config["manifest"]).read_text(encoding="utf-8"))
    detection_run = PROJECT_DIR / config["detections_run"]
    detection_run_json = json.loads((detection_run / "run.json").read_text(encoding="utf-8"))
    if detection_run_json.get("status") != "completed":
        raise SystemExit("detection run is not completed")

    ultralytics_cfg = Path(__import__("ultralytics").__file__).parent / "cfg" / "trackers"
    for tracker_name in args.trackers:
        run_id = f"{config['run_prefix']}-{tracker_name}-001"
        run_dir = PROJECT_DIR / "runs" / run_id
        if run_dir.exists():
            raise FileExistsError(f"run directory already exists: {run_dir}")
        run_dir.mkdir(parents=True)
        yaml_path = ultralytics_cfg / config["trackers"][tracker_name]
        tracker_args = IterableSimpleNamespace(**yaml_load(yaml_path))
        record = {
            "run_id": run_id,
            "experiment": "A2-MOT17-TRACKER-ONLY",
            "status": "running",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "config_hash": sha256(args.config.resolve()),
            "tracker_name": tracker_name,
            "tracker_yaml": str(yaml_path),
            "tracker_yaml_sha256": sha256(yaml_path),
            "detections_run": config["detections_run"],
            "detections_manifest_sha256": sha256(detection_run / "run.json"),
            "detector_weights_hash": detection_run_json["weights_hash"],
            "sequences": {},
            "note": "Same per-frame detections for both trackers; no detector re-run.",
        }
        write_json(run_dir / "config.json", {"config": config, "tracker": tracker_name})
        write_json(run_dir / "run.json", record)

        overall_start = time.perf_counter()
        for sequence in manifest["sequences"]:
            name = sequence["sequence"]
            detections = load_detections(detection_run / name / "detections.jsonl")
            frames = int(sequence["frame_count"])
            fps = float(sequence["frame_rate_fps"])
            scale_x = float(sequence["frame_scale_x"])
            scale_y = float(sequence["frame_scale_y"])
            width = float(sequence["width"])
            height = float(sequence["height"])
            image_dir = PROJECT_DIR / "data/a2/mot17/raw" / name / "img1"
            tracker = BOTSORT(tracker_args, frame_rate=round(fps)) if tracker_name == "botsort" else BYTETracker(tracker_args, frame_rate=round(fps))
            rows = []
            track_ids = set()
            seq_start = time.perf_counter()
            for frame in range(1, frames + 1):
                frame_rows = detections.get(frame, [])
                frame_width = int(sequence["frame_width"])
                frame_height = int(sequence["frame_height"])
                boxes = DetectionResults(frame_rows)
                image = cv2.imread(str(image_dir / f"{frame:06d}.jpg")) if tracker_name == "botsort" else None
                tracked = tracker.update(boxes, image)
                for item in tracked:
                    if len(item) < 8:
                        continue
                    x1, y1, x2, y2, track_id = [float(value) for value in item[:5]]
                    score = float(item[5])
                    track_id = int(track_id)
                    x1 = max(0.0, min(width - 1.0, x1 * scale_x))
                    y1 = max(0.0, min(height - 1.0, y1 * scale_y))
                    x2 = max(0.0, min(width - 1.0, x2 * scale_x))
                    y2 = max(0.0, min(height - 1.0, y2 * scale_y))
                    box_width = max(0.0, x2 - x1)
                    box_height = max(0.0, y2 - y1)
                    if box_width <= 0 or box_height <= 0:
                        continue
                    rows.append((frame, track_id, x1, y1, box_width, box_height, score))
                    track_ids.add(track_id)
            pred_dir = run_dir / name / "predictions"
            pred_dir.mkdir(parents=True)
            pred_path = pred_dir / f"{name}.txt"
            with pred_path.open("w", encoding="utf-8") as handle:
                for row in sorted(rows):
                    handle.write(f"{row[0]},{row[1]},{row[2]:.3f},{row[3]:.3f},{row[4]:.3f},{row[5]:.3f},{row[6]:.6f},-1,-1,-1\n")
            seq_record = {
                "status": "completed",
                "processed_frames": frames,
                "prediction_file": str(pred_path.relative_to(PROJECT_DIR)),
                "prediction_sha256": sha256(pred_path),
                "prediction_rows": len(rows),
                "track_count": len(track_ids),
                "wall_seconds": time.perf_counter() - seq_start,
            }
            record["sequences"][name] = seq_record
            write_json(run_dir / name / "run.json", seq_record)
            write_json(run_dir / "run.json", record)
            print(json.dumps({"tracker": tracker_name, **seq_record}, ensure_ascii=False), flush=True)

        record.update({
            "status": "completed",
            "processed_frames": sum(item["processed_frames"] for item in record["sequences"].values()),
            "wall_seconds": time.perf_counter() - overall_start,
        })
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "run.json", record)
        print(json.dumps({"tracker": tracker_name, "run_id": run_id, "status": "completed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
