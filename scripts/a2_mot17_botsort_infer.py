#!/usr/bin/env python3
"""A2: BoT-SORT reference run on the seven MOT17 sequence-video scenes.

This is a tracker-implementation reference, not a controlled tracker-only
ablation: Ultralytics couples detection and tracking in ``model.track``.  The
detector weights/size/confidence/IoU are the same as the ByteTrack baseline,
but the association code and default BoT-SORT parameters differ from the
Supervision ByteTrack baseline.  Results are reported with that caveat.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shlex
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
from st_memory.records import sha256  # noqa: E402

os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT_DIR / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_DIR / ".cache/matplotlib"))
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="A2 MOT17 BoT-SORT reference inference")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/a2_mot17_botsort.json")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test limit per sequence")
    args = parser.parse_args()

    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_id = args.run_id or config["run_id"]
    run_dir = PROJECT_DIR / "runs" / run_id
    if run_dir.exists():
        existing = run_dir / "run.json"
        if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")).get("status") == "completed":
            print(f"run already completed: {run_dir}")
            return 0
        raise FileExistsError(f"run directory already exists and is not complete: {run_dir}")
    run_dir.mkdir(parents=True)

    manifest = json.loads((PROJECT_DIR / config["manifest"]).read_text(encoding="utf-8"))
    weights = (PROJECT_DIR / config["weights"]).resolve(strict=True)
    lock = json.loads((PROJECT_DIR / "components.lock.json").read_text(encoding="utf-8"))
    if sha256(weights) != lock["assets"]["detector_weights"]["sha256"]:
        raise ValueError("detector weights hash differs from components lock")

    record = {
        "run_id": run_id,
        "experiment": "A2-MOT17-BOTSORT",
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join([sys.executable, *sys.argv]),
        "config_hash": sha256(args.config.resolve()),
        "weights_hash": sha256(weights),
        "manifest": config["manifest"],
        "tracker_yaml": config["tracker_yaml"],
        "tracker_note": "Ultralytics model.track couples detection and association; not a tracker-only ablation.",
        "sequences": {},
    }
    write_json(run_dir / "config.json", config)
    write_json(run_dir / "run.json", record)

    try:
        if str(config["device"]) != "cpu" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable")
        torch.manual_seed(int(config["seed"]))
        np.random.seed(int(config["seed"]))
        overall_start = time.perf_counter()
        for sequence in manifest["sequences"]:
            name = sequence["sequence"]
            seq_dir = run_dir / name
            seq_dir.mkdir()
            images = sorted((PROJECT_DIR / "data/a2/mot17/raw" / name / "img1").glob("*.jpg"))
            if len(images) != sequence["frame_count"]:
                raise ValueError(f"{name}: {len(images)} images, expected {sequence['frame_count']}")
            source_manifest = json.loads(
                (PROJECT_DIR / "data/a2/mot17/raw" / name / "source_manifest.json").read_text(encoding="utf-8")
            )
            video_path = PROJECT_DIR / source_manifest["video_path"]
            if not video_path.is_file():
                raise FileNotFoundError(f"{name}: missing source video {video_path}")
            limit = min(len(images), int(args.limit or sequence["frame_count"]))
            scale_x = float(sequence["frame_scale_x"])
            scale_y = float(sequence["frame_scale_y"])
            width = float(sequence["width"])
            height = float(sequence["height"])
            seq_record = {
                "sequence": name,
                "status": "running",
                "processed_frames": 0,
                "frame_count": sequence["frame_count"],
                "frame_width": sequence["frame_width"],
                "frame_height": sequence["frame_height"],
                "width": width,
                "height": height,
                "frame_scale_x": scale_x,
                "frame_scale_y": scale_y,
                "detection_count": 0,
                "track_point_count": 0,
                "track_count": 0,
            }
            write_json(seq_dir / "run.json", seq_record)
            rows = []
            timings = []
            track_ids = set()
            started = time.perf_counter()
            try:
                # A fresh model per sequence resets the tracker state; this is
                # slower than reusing the model but removes cross-video ID
                # leakage.
                model = YOLO(str(weights))
                # Use the official sequence video, not a list of images: a
                # list is treated as independent sources by this Ultralytics
                # version and resets tracking between images.
                results = model.track(
                    source=str(video_path),
                    tracker=config["tracker_yaml"],
                    persist=False,
                    stream=True,
                    conf=config["confidence"],
                    iou=config["iou"],
                    imgsz=config["image_size"],
                    classes=config["classes"],
                    device=config["device"],
                    verbose=False,
                )
                for index, result in enumerate(results):
                    if index >= limit:
                        break
                    frame_start = time.perf_counter()
                    boxes = result.boxes
                    seq_record["detection_count"] += int(len(boxes))
                    if boxes.id is not None and boxes.xyxy is not None:
                        ids = boxes.id.detach().cpu().numpy().astype(int).tolist()
                        xyxy = boxes.xyxy.detach().cpu().numpy()
                        confs = boxes.conf.detach().cpu().numpy()
                        for track_id, bbox, score in zip(ids, xyxy, confs):
                            fx1, fy1, fx2, fy2 = [float(value) for value in bbox]
                            x1 = max(0.0, min(width - 1.0, fx1 * scale_x))
                            y1 = max(0.0, min(height - 1.0, fy1 * scale_y))
                            x2 = max(0.0, min(width - 1.0, fx2 * scale_x))
                            y2 = max(0.0, min(height - 1.0, fy2 * scale_y))
                            box_width = max(0.0, x2 - x1)
                            box_height = max(0.0, y2 - y1)
                            if box_width <= 0 or box_height <= 0:
                                continue
                            rows.append((index + 1, int(track_id), x1, y1, box_width, box_height, float(score)))
                            track_ids.add(int(track_id))
                    seq_record["processed_frames"] += 1
                    seq_record["track_point_count"] = len(rows)
                    seq_record["track_count"] = len(track_ids)
                    timings.append(time.perf_counter() - frame_start)
                    if (index + 1) % 100 == 0:
                        print(f"{name}: frame={index + 1}/{limit} tracks={len(track_ids)} points={len(rows)}", flush=True)
                        write_json(seq_dir / "run.json", seq_record)
                pred_dir = seq_dir / "predictions"
                pred_dir.mkdir()
                pred_path = pred_dir / f"{name}.txt"
                with pred_path.open("w", encoding="utf-8") as handle:
                    for frame, track_id, x, y, w, h, score in sorted(rows):
                        handle.write(f"{frame},{track_id},{x:.3f},{y:.3f},{w:.3f},{h:.3f},{score:.6f},-1,-1,-1\n")
                seq_record.update({
                    "status": "completed",
                    "processed_frames": limit,
                    "prediction_file": str(pred_path.relative_to(PROJECT_DIR)),
                    "prediction_sha256": sha256(pred_path),
                    "prediction_rows": len(rows),
                    "wall_seconds": time.perf_counter() - started,
                    "frame_processing_seconds_p50": float(np.percentile(timings, 50)),
                    "frame_processing_seconds_p95": float(np.percentile(timings, 95)),
                    "gpu_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else None,
                })
            except Exception:
                seq_record.update({"status": "failed", "error": traceback.format_exc()})
                raise
            finally:
                seq_record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                write_json(seq_dir / "run.json", seq_record)
            record["sequences"][name] = {
                key: seq_record[key]
                for key in (
                    "status", "processed_frames", "frame_count", "prediction_file", "prediction_sha256",
                    "prediction_rows", "detection_count", "track_point_count", "track_count",
                    "wall_seconds", "frame_processing_seconds_p50", "frame_processing_seconds_p95",
                    "gpu_peak_allocated_mib",
                )
            }
            write_json(run_dir / "run.json", record)
            print(json.dumps(record["sequences"][name], ensure_ascii=False), flush=True)
        record.update({
            "status": "completed",
            "sequence_count_completed": sum(1 for item in record["sequences"].values() if item["status"] == "completed"),
            "processed_frames": sum(item["processed_frames"] for item in record["sequences"].values()),
            "wall_seconds": time.perf_counter() - overall_start,
            "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        })
    except Exception:
        record.update({"status": "failed", "error": traceback.format_exc()})
        raise
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "run.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
