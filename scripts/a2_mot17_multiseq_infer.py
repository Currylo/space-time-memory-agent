#!/usr/bin/env python3
"""A2: run the frozen YOLOv8n + ByteTrack baseline on seven MOT17 scenes.

Only official images are read.  Official detections and GT boxes are never
used as system predictions.  The detector weights, input size, confidence,
IoU and ByteTrack parameters are frozen in the config and shared by every
sequence.  Each scene gets a fresh tracker so identities cannot leak across
videos.
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
from st_memory.records import sha256  # noqa: E402

os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT_DIR / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_DIR / ".cache/matplotlib"))
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_row(stream, row: dict) -> None:
    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def validate_sequence(sequence: dict, project_dir: Path) -> tuple[Path, Path, list[Path]]:
    sequence_dir = (project_dir / "data/a2/mot17/raw" / sequence["sequence"]).resolve()
    image_dir = sequence_dir / "img1"
    gt_path = sequence_dir / "gt" / "gt.txt"
    if not image_dir.is_dir() or not gt_path.is_file():
        raise FileNotFoundError(f"{sequence['sequence']}: missing img1 or gt/gt.txt")
    images = sorted(image_dir.glob("*.jpg"))
    if len(images) != int(sequence["frame_count"]):
        raise ValueError(
            f"{sequence['sequence']}: {len(images)} images, expected {sequence['frame_count']}"
        )
    if sha256(gt_path) != sequence["gt_sha256"]:
        raise ValueError(f"{sequence['sequence']}: GT hash differs from manifest")
    return sequence_dir, gt_path, images


def main() -> int:
    parser = argparse.ArgumentParser(description="A2 MOT17 seven-scene baseline inference")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/a2_mot17_multiseq_baseline.json")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test limit per sequence; final run uses full config")
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
        existing = run_dir / "run.json"
        if existing.is_file():
            record = json.loads(existing.read_text(encoding="utf-8"))
            if record.get("status") == "completed":
                print(f"run already completed: {run_dir}")
                return 0
        raise FileExistsError(f"run directory already exists and is not complete: {run_dir}")
    run_dir.mkdir(parents=True)

    manifest_path = (PROJECT_DIR / config["manifest"]).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sequences = manifest["sequences"]
    if len(sequences) != 7:
        raise ValueError(f"expected seven registered sequences, found {len(sequences)}")
    weights = (PROJECT_DIR / config["weights"]).resolve(strict=True)
    lock = json.loads((PROJECT_DIR / "components.lock.json").read_text(encoding="utf-8"))
    if sha256(weights) != lock["assets"]["detector_weights"]["sha256"]:
        raise ValueError("detector weights hash differs from components lock")
    for package, expected in lock["runtime_versions"].items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        if actual != expected:
            raise ValueError(f"{package} differs from pinned runtime: {actual} != {expected}")

    write_json(run_dir / "config.json", config)
    record = {
        "run_id": run_id,
        "experiment": "A2-MOT17",
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join([sys.executable, *sys.argv]),
        "config_hash": sha256(config_path),
        "manifest": str(manifest_path.relative_to(PROJECT_DIR)),
        "manifest_sha256": sha256(manifest_path),
        "weights_hash": sha256(weights),
        "components_lock_hash": sha256(PROJECT_DIR / "components.lock.json"),
        "sequence_count": len(sequences),
        "processed_frames": 0,
        "detection_count": 0,
        "track_point_count": 0,
        "track_count": 0,
        "public_detections_used": False,
        "ground_truth_used_as_predictions": False,
        "sequences": {},
    }
    write_json(run_dir / "run.json", record)

    try:
        if str(config["device"]) != "cpu" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable")
        torch.manual_seed(int(config["seed"]))
        np.random.seed(int(config["seed"]))
        torch.set_num_threads(int(config["cpu_threads"]))
        cv2.setNumThreads(int(config["cpu_threads"]))
        torch.backends.cudnn.benchmark = False

        model = YOLO(str(weights))
        overall_start = time.perf_counter()
        for sequence in sequences:
            sequence_name = sequence["sequence"]
            seq_dir = run_dir / sequence_name
            seq_dir.mkdir()
            sequence_dir, gt_path, images = validate_sequence(sequence, PROJECT_DIR)
            limit = min(len(images), int(args.limit or sequence["frame_count"]))
            fps = float(sequence["frame_rate_fps"])
            width = float(sequence["width"])
            height = float(sequence["height"])
            frame_width = float(sequence.get("frame_width", width))
            frame_height = float(sequence.get("frame_height", height))
            scale_x = float(sequence.get("frame_scale_x", width / frame_width))
            scale_y = float(sequence.get("frame_scale_y", height / frame_height))
            tracker = sv.ByteTrack(frame_rate=round(fps), **config["tracker"])
            seq_record = {
                "sequence": sequence_name,
                "status": "running",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "frame_count": int(sequence["frame_count"]),
                "processed_frames": 0,
                "fps": fps,
                "width": width,
                "height": height,
                "frame_width": frame_width,
                "frame_height": frame_height,
                "frame_scale_x": scale_x,
                "frame_scale_y": scale_y,
                "image_dir": str((sequence_dir / "img1").relative_to(PROJECT_DIR)),
                "gt_path": str(gt_path.relative_to(PROJECT_DIR)),
                "gt_sha256": sequence["gt_sha256"],
                "image_manifest_sha256": sequence["image_manifest_sha256"],
                "detection_count": 0,
                "track_point_count": 0,
                "track_count": 0,
            }
            write_json(seq_dir / "run.json", seq_record)
            records = []
            timings = []
            track_ids = set()
            sequence_start = time.perf_counter()
            try:
                with open(seq_dir / "detections.jsonl", "w", encoding="utf-8", buffering=1) as det_stream, \
                     open(seq_dir / "tracks.jsonl", "w", encoding="utf-8", buffering=1) as trk_stream, \
                     open(seq_dir / "frames.jsonl", "w", encoding="utf-8", buffering=1) as frame_stream:
                    for index, image_path in enumerate(images[:limit]):
                        frame_start = time.perf_counter()
                        frame = cv2.imread(str(image_path))
                        if frame is None:
                            raise RuntimeError(f"cannot read image {image_path}")
                        video_time = index / fps
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
                            fx1, fy1, fx2, fy2 = [float(value) for value in bbox]
                            fx1 = max(0.0, min(frame_width - 1.0, fx1))
                            fy1 = max(0.0, min(frame_height - 1.0, fy1))
                            fx2 = max(0.0, min(frame_width - 1.0, fx2))
                            fy2 = max(0.0, min(frame_height - 1.0, fy2))
                            frame_box_width = max(0.0, fx2 - fx1)
                            frame_box_height = max(0.0, fy2 - fy1)
                            if frame_box_width <= 0 or frame_box_height <= 0:
                                continue
                            x1 = max(0.0, min(width - 1.0, fx1 * scale_x))
                            y1 = max(0.0, min(height - 1.0, fy1 * scale_y))
                            x2 = max(0.0, min(width - 1.0, fx2 * scale_x))
                            y2 = max(0.0, min(height - 1.0, fy2 * scale_y))
                            box_width = max(0.0, x2 - x1)
                            box_height = max(0.0, y2 - y1)
                            if box_width <= 0 or box_height <= 0:
                                continue
                            mot_frame = index + 1
                            records.append((mot_frame, track_id, x1, y1, box_width, box_height, float(score)))
                            track_ids.add(track_id)
                            dump_row(trk_stream, {
                                "run_id": run_id,
                                "sequence": sequence_name,
                                "track_id": track_id,
                                "frame_index": index,
                                "mot_frame": mot_frame,
                                "video_time": video_time,
                                "bbox": [x1, y1, x2, y2],
                                "bbox_xywh": [x1, y1, box_width, box_height],
                                "bbox_frame_xyxy": [fx1, fy1, fx2, fy2],
                                "position": [(x1 + x2) / 2.0, y2],
                                "score": float(score),
                                "class_id": class_id,
                                "source_component": "supervision.ByteTrack",
                                "detector": "YOLOv8n",
                                "evidence_ref": {"path": str(image_path), "frame_index": index, "mot_frame": mot_frame},
                            })
                        seq_record["processed_frames"] += 1
                        seq_record["detection_count"] += int(len(detected))
                        seq_record["track_point_count"] = len(records)
                        seq_record["track_count"] = len(track_ids)
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
                        if (index + 1) % 100 == 0:
                            print(f"{sequence_name}: frame={index + 1}/{limit} tracks={len(track_ids)} points={len(records)}", flush=True)
                            write_json(seq_dir / "run.json", seq_record)
                pred_dir = seq_dir / "predictions"
                pred_dir.mkdir()
                pred_path = pred_dir / f"{sequence_name}.txt"
                with pred_path.open("w", encoding="utf-8") as handle:
                    for row in sorted(records):
                        mot_frame, track_id, x, y, w, h, score = row
                        handle.write(f"{mot_frame},{track_id},{x:.3f},{y:.3f},{w:.3f},{h:.3f},{score:.6f},-1,-1,-1\n")
                seq_record.update({
                    "status": "completed",
                    "processed_frames": len(images[:limit]),
                    "prediction_file": str(pred_path.relative_to(PROJECT_DIR)),
                    "prediction_sha256": sha256(pred_path),
                    "prediction_rows": len(records),
                    "wall_seconds": time.perf_counter() - sequence_start,
                    "frame_processing_seconds_p50": float(np.percentile(timings, 50)),
                    "frame_processing_seconds_p95": float(np.percentile(timings, 95)),
                    "timing_scope": "image decode + YOLO predict + ByteTrack + write; offline only",
                    "gpu_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else None,
                })
            except Exception:
                seq_record.update({"status": "failed", "error": traceback.format_exc()})
                raise
            finally:
                seq_record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
                write_json(seq_dir / "run.json", seq_record)
            record["sequences"][sequence_name] = {
                key: seq_record[key]
                for key in (
                    "status", "processed_frames", "frame_count", "prediction_file", "prediction_sha256",
                    "prediction_rows", "detection_count", "track_point_count", "track_count",
                    "wall_seconds", "frame_processing_seconds_p50", "frame_processing_seconds_p95",
                    "gpu_peak_allocated_mib",
                )
            }
            record["processed_frames"] += seq_record["processed_frames"]
            record["detection_count"] += seq_record["detection_count"]
            record["track_point_count"] += seq_record["track_point_count"]
            record["track_count"] += seq_record["track_count"]
            write_json(run_dir / "run.json", record)
            print(json.dumps(record["sequences"][sequence_name], ensure_ascii=False), flush=True)

        record.update({
            "status": "completed",
            "sequence_count_completed": sum(
                1 for item in record["sequences"].values() if item["status"] == "completed"
            ),
            "wall_seconds": time.perf_counter() - overall_start,
            "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "gpu_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else None,
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
