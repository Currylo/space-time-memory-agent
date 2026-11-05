#!/usr/bin/env python3
"""E02: run the frozen YOLOv8n + Supervision ByteTrack baseline on MOT17 images.

This adapter reads only the official image sequence and the frozen config.  It
does not read official public detections or GT boxes.  Predictions are written
in MOTChallenge format for TrackEval.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
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
from st_memory.records import sha256

os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT_DIR / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_DIR / ".cache/matplotlib"))
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_row(stream, row: dict) -> None:
    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="E02 MOT17 frozen baseline inference")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/e02_mot17_frcnn_baseline.json")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test limit; final run uses full config")
    args = parser.parse_args()

    import cv2
    import numpy as np
    import supervision as sv
    import torch
    from ultralytics import YOLO

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    run_id = args.run_id or config["run_id"]
    if Path(run_id).name != run_id:
        raise ValueError("run_id must be a single directory name")
    run_dir = PROJECT_DIR / "runs" / run_id
    if run_dir.exists():
        raise FileExistsError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    weights = (PROJECT_DIR / config["weights"]).resolve(strict=True)
    sequence_dir = (PROJECT_DIR / config["sequence_dir"]).resolve(strict=True)
    image_dir = (PROJECT_DIR / config["image_dir"]).resolve(strict=True)
    sequence_manifest_path = PROJECT_DIR / "data" / "e02" / "sequence_manifest.json"
    sequence_manifest = json.loads(sequence_manifest_path.read_text(encoding="utf-8"))
    lock = json.loads((PROJECT_DIR / "components.lock.json").read_text(encoding="utf-8"))

    if sha256(weights) != lock["assets"]["detector_weights"]["sha256"]:
        raise ValueError("detector weights hash differs from components lock")
    if sequence_manifest["image_manifest_sha256"] != config["expected"]["image_manifest_sha256"]:
        raise ValueError("sequence image manifest hash differs from frozen config")
    if sequence_manifest["gt_sha256"] != config["expected"]["gt_sha256"]:
        raise ValueError("GT hash differs from frozen config")
    for package, expected in lock["runtime_versions"].items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        if actual != expected:
            raise ValueError(f"{package} differs from pinned runtime: {actual} != {expected}")

    images = sorted(image_dir.glob("*.jpg"))
    if len(images) != config["expected"]["image_count"]:
        raise ValueError(f"expected {config['expected']['image_count']} images, found {len(images)}")
    limit = min(len(images), int(args.limit or config["max_frames"] or len(images)))
    if limit <= 0:
        raise ValueError("limit must be positive")

    record = {
        "run_id": run_id,
        "experiment": "E02",
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join([sys.executable, *sys.argv]),
        "config_hash": sha256(config_path),
        "weights_hash": sha256(weights),
        "components_lock_hash": sha256(PROJECT_DIR / "components.lock.json"),
        "dataset_id": config["dataset_id"],
        "sequence": config["sequence"],
        "evaluation_split": config["evaluation_split"],
        "source_kind": config["source_kind"],
        "sequence_manifest": str(sequence_manifest_path.relative_to(PROJECT_DIR)),
        "sequence_manifest_sha256": sha256(sequence_manifest_path),
        "image_manifest_sha256": sequence_manifest["image_manifest_sha256"],
        "gt_sha256": sequence_manifest["gt_sha256"],
        "public_detections_used": False,
        "ground_truth_used_as_predictions": False,
        "processed_frames": 0,
        "detection_count": 0,
        "track_point_count": 0,
        "track_count": 0,
    }
    write_json(run_dir / "config.json", config)
    write_json(run_dir / "run.json", record)

    try:
        if str(config["device"]) != "cpu" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; frozen E00 baseline used CUDA")
        torch.manual_seed(int(config["seed"]))
        np.random.seed(int(config["seed"]))
        torch.set_num_threads(int(config["cpu_threads"]))
        cv2.setNumThreads(int(config["cpu_threads"]))
        torch.backends.cudnn.benchmark = False

        model = YOLO(str(weights))
        tracker = sv.ByteTrack(frame_rate=round(float(config["fps"])), **config["tracker"])
        records = []
        timings = []
        track_ids = set()
        start = time.perf_counter()
        with open(run_dir / "detections.jsonl", "w", encoding="utf-8", buffering=1) as det_stream, \
             open(run_dir / "tracks.jsonl", "w", encoding="utf-8", buffering=1) as trk_stream, \
             open(run_dir / "frames.jsonl", "w", encoding="utf-8", buffering=1) as frame_stream:
            for index, image_path in enumerate(images[:limit]):
                frame_start = time.perf_counter()
                frame = cv2.imread(str(image_path))
                if frame is None:
                    raise RuntimeError(f"cannot read image {image_path}")
                video_time = index / float(config["fps"])
                result = model.predict(
                    frame,
                    device=config["device"],
                    imgsz=config["image_size"],
                    conf=config["confidence"],
                    iou=config["iou"],
                    classes=config["classes"],
                    verbose=False,
                )[0]
                detected = sv.Detections.from_ultralytics(result)
                dump_row(det_stream, {
                    "frame_index": index,
                    "mot_frame": index + 1,
                    "video_time": video_time,
                    "image": image_path.name,
                    "bbox_xyxy": detected.xyxy.tolist(),
                    "score": detected.confidence.tolist(),
                    "class_id": detected.class_id.tolist(),
                })
                tracked = tracker.update_with_detections(detected)
                for bbox, score, class_id, track_id in zip(
                    tracked.xyxy, tracked.confidence, tracked.class_id, tracked.tracker_id
                ):
                    if track_id is None:
                        continue
                    track_id = int(track_id)
                    class_id = int(class_id)
                    if class_id != 0:
                        continue
                    x1, y1, x2, y2 = [float(value) for value in bbox]
                    width = max(0.0, min(float(config["width"]) - 1.0, x2) - max(0.0, x1))
                    height = max(0.0, min(float(config["height"]) - 1.0, y2) - max(0.0, y1))
                    if width <= 0 or height <= 0:
                        continue
                    mot_frame = index + 1
                    records.append((mot_frame, track_id, max(0.0, min(float(config["width"]) - 1.0, x1)), max(0.0, min(float(config["height"]) - 1.0, y1)), width, height, float(score)))
                    track_ids.add(track_id)
                    dump_row(trk_stream, {
                        "run_id": run_id,
                        "sequence": config["sequence"],
                        "track_id": track_id,
                        "frame_index": index,
                        "mot_frame": mot_frame,
                        "video_time": video_time,
                        "bbox": [max(0.0, min(float(config["width"]) - 1.0, x1)), max(0.0, min(float(config["height"]) - 1.0, y1)), x2, y2],
                        "position": [(x1 + x2) / 2.0, y2],
                        "score": float(score),
                        "class_id": class_id,
                        "source_component": "supervision.ByteTrack",
                        "detector": "YOLOv8n",
                        "evidence_ref": {"path": str(image_path), "frame_index": index, "mot_frame": mot_frame},
                    })
                record["processed_frames"] += 1
                record["detection_count"] += int(len(detected))
                record["track_point_count"] = len(records)
                record["track_count"] = len(track_ids)
                elapsed = time.perf_counter() - frame_start
                timings.append(elapsed)
                dump_row(frame_stream, {
                    "frame_index": index,
                    "mot_frame": index + 1,
                    "video_time": video_time,
                    "image": image_path.name,
                    "detected_count": int(len(detected)),
                    "tracked_count": int(len(tracked)),
                    "processing_seconds": elapsed,
                })
                if (index + 1) % 50 == 0:
                    print(f"frame={index + 1}/{limit} tracks={len(track_ids)} points={len(records)}", flush=True)
                    write_json(run_dir / "run.json", record)
        pred_dir = run_dir / "predictions"
        pred_dir.mkdir()
        pred_path = pred_dir / f"{config['sequence']}.txt"
        with pred_path.open("w", encoding="utf-8") as handle:
            for row in sorted(records):
                mot_frame, track_id, x, y, w, h, score = row
                handle.write(f"{mot_frame},{track_id},{x:.3f},{y:.3f},{w:.3f},{h:.3f},{score:.6f},-1,-1,-1\n")
        record.update({
            "status": "completed",
            "processed_frames": len(images[:limit]),
            "detection_count": record["detection_count"],
            "track_point_count": len(records),
            "track_count": len(track_ids),
            "prediction_file": str(pred_path.relative_to(PROJECT_DIR)),
            "prediction_sha256": sha256(pred_path),
            "prediction_rows": len(records),
            "wall_seconds": time.perf_counter() - start,
            "frame_processing_seconds_p50": float(np.percentile(timings, 50)),
            "frame_processing_seconds_p95": float(np.percentile(timings, 95)),
            "timing_scope": "image decode + YOLO predict + ByteTrack + write; offline only",
            "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "gpu_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else None,
            "notes": [
                "Predictions use only official images; official det.txt and GT boxes were not used as system detections.",
                "Prediction frame numbers are 1-based MOT frame numbers.",
                "No parameter tuning was performed after seeing TrackEval results.",
            ],
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
