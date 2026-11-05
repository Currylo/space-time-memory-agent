"""E00: local YOLO predict -> Supervision ByteTrack -> replay + JSONL.

All configuration paths are relative to the project root, independent of cwd.
The video clock is frame_index/fps for CFR demos, never wall-clock time.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import shlex
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.records import sha256

os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
# Upstream 8.3.40 predates torch's weights_only default; only hash-locked official weights.
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_row(stream, data):
    stream.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")


def run(config_path, run_id=None):
    import cv2
    import numpy as np
    import supervision as sv
    import torch
    from ultralytics import YOLO

    config = json.loads(config_path.read_text())
    if config["zones"]:
        raise ValueError("E00 adapter has no zone calibration yet; use empty zones")
    if config.get("source_kind") == "public_demo" and config["split"] != "development":
        raise ValueError("public demo cannot serve as field validation/test data")
    video = (ROOT / config["video"]).resolve(strict=True)
    weights = (ROOT / config["weights"]).resolve(strict=True)
    lock = json.loads((ROOT / "components.lock.json").read_text())
    for name, path in (("demo_video", video), ("detector_weights", weights)):
        if sha256(path) != lock["assets"][name]["sha256"]:
            raise ValueError(f"{name} checksum differs from component lock")
    for package, expected in lock["runtime_versions"].items():
        if importlib.metadata.version(package) != expected:
            raise ValueError(f"{package} differs from pinned runtime: {expected}")
    run_id = run_id or f"e00-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    if Path(run_id).name != run_id or run_id in (".", ".."):
        raise ValueError("run_id must be a single directory name")
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "config.json", config)
    source_hash = {str(p.relative_to(ROOT)): sha256(p) for base in ("src", "scripts")
                   for p in sorted((ROOT / base).rglob("*.py"))}
    manifest = {
        "video_id": config["video_id"], "camera_id": config["camera_id"],
        "source_kind": config["source_kind"], "split": config["split"],
        "source_group": config["source_group"], "source_url": config["source_url"],
        "usage_scope": config["usage_scope"], "path": str(video), "sha256": sha256(video),
        "absolute_capture_time": None, "timestamp_basis": "relative_seconds_frame_index_over_fps",
        "vfr_limit": "CFR demo only; variable-frame-rate sources require decoded PTS adapter",
    }
    record = {
        "run_id": run_id, "experiment": "E00-A", "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join([sys.executable, *sys.argv]),
        "config_hash": sha256(config_path), "model_hash": sha256(weights),
        "components_lock_hash": sha256(ROOT / "components.lock.json"),
        "code_version": {"kind": "source_sha256_no_git_repository", "files": source_hash},
        "data_version": manifest["sha256"], "seed": config["seed"],
        "source": manifest, "processed_frames": 0, "track_point_count": 0,
    }
    write_json(run_dir / "run.json", record)
    start = time.perf_counter()
    cap = None
    writer = None
    try:
        if str(config["device"]) != "cpu" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable in this context. Check doctor --context host; no CPU fallback.")
        torch.manual_seed(config["seed"])
        np.random.seed(config["seed"])
        torch.set_num_threads(config["cpu_threads"])
        cv2.setNumThreads(config["cpu_threads"])
        torch.backends.cudnn.benchmark = False
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError("cannot open video")
        fps = cap.get(cv2.CAP_PROP_FPS)
        width, height = (int(cap.get(p)) for p in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or min(width, height, frame_count) <= 0:
            raise ValueError("invalid video metadata")
        manifest.update({"fps": fps, "width": width, "height": height,
                         "frame_count": frame_count, "duration_seconds": frame_count / fps})
        write_json(run_dir / "manifest.json", manifest)
        record["source"] = manifest
        writer = cv2.VideoWriter(str(run_dir / "replay.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError("cannot open replay writer")
        model = YOLO(str(weights))
        tracker = sv.ByteTrack(frame_rate=round(fps), **config["tracker"])
        box_annotator, label_annotator = sv.BoxAnnotator(), sv.LabelAnnotator()
        traces = sv.TraceAnnotator(trace_length=round(fps * 3))
        stats = {}
        timings = []
        limit = min(frame_count, config["max_frames"]) if config["max_frames"] else frame_count
        with open(run_dir / "tracks.jsonl", "w", encoding="utf-8", buffering=1) as tracks, \
             open(run_dir / "detections.jsonl", "w", encoding="utf-8", buffering=1) as raw, \
             open(run_dir / "frames.jsonl", "w", encoding="utf-8", buffering=1) as frames:
            for index in range(limit):
                frame_start = time.perf_counter()
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f"decode failed before expected frame {index}/{limit}")
                video_time = index / fps
                result = model.predict(frame, device=config["device"], imgsz=config["image_size"],
                    conf=config["confidence"], iou=config["iou"], classes=config["classes"],
                    verbose=False)[0]
                detected = sv.Detections.from_ultralytics(result)
                dump_row(raw, {"frame_index": index, "video_time": video_time,
                    "bbox_xyxy": detected.xyxy.tolist(), "score": detected.confidence.tolist(),
                    "class_id": detected.class_id.tolist()})
                tracked = tracker.update_with_detections(detected)
                for bbox, score, class_id, track_id in zip(tracked.xyxy, tracked.confidence,
                                                          tracked.class_id, tracked.tracker_id):
                    track_id = int(track_id)
                    bbox = [float(v) for v in bbox]
                    trace_id = f"{config['site_id']}/{config['camera_id']}/{run_id}/{track_id}"
                    dump_row(tracks, {
                        "run_id": run_id, "site_id": config["site_id"], "camera_id": config["camera_id"],
                        "track_id": track_id, "trace_id": trace_id, "frame_index": index,
                        "video_time": video_time, "bbox": bbox,
                        "position": [(bbox[0] + bbox[2]) / 2, bbox[3]],
                        "coordinate_system": "image_pixels_xyxy_bottom_center", "time_unit": "seconds",
                        "zone_id": None, "zone_ids": [], "score": float(score), "class_id": int(class_id),
                        "quality": {"state": "unassessed", "occlusion": None},
                        "source_video_id": config["video_id"], "source_component": "supervision.ByteTrack",
                        "evidence_source": "visible_video", "evidence_ref": {"path": str(video),
                            "frame_index": index, "video_time": video_time},
                    })
                    record["track_point_count"] += 1
                    if track_id not in stats:
                        stats[track_id] = {"trace_id": trace_id, "track_id": track_id,
                            "source_video_id": config["video_id"], "start_time": video_time,
                            "observed_point_count": 0, "zone_sequence": [], "features": {},
                            "quality_summary": "unassessed"}
                    stats[track_id].update({"last_observed_time": video_time,
                                           "observed_point_count": stats[track_id]["observed_point_count"] + 1})
                canvas = traces.annotate(frame.copy(), tracked)
                canvas = box_annotator.annotate(canvas, tracked)
                canvas = label_annotator.annotate(canvas, tracked,
                    labels=[f"#{i} {s:.2f}" for i, s in zip(tracked.tracker_id, tracked.confidence)])
                cv2.putText(canvas, f"public_demo | t={video_time:.2f}s", (20, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                writer.write(canvas)
                if index == min(30, limit - 1):
                    cv2.imwrite(str(run_dir / "preview.jpg"), canvas)
                elapsed = time.perf_counter() - frame_start
                timings.append(elapsed)
                dump_row(frames, {"frame_index": index, "video_time": video_time,
                    "decoded_pts_ms_diagnostic": cap.get(cv2.CAP_PROP_POS_MSEC),
                    "detected_count": len(detected), "tracked_count": len(tracked),
                    "processing_seconds": elapsed, "wall_time_utc": datetime.now(timezone.utc).isoformat()})
                record["processed_frames"] += 1
                if (index + 1) % 50 == 0:
                    print(f"frame={index + 1}/{limit} points={record['track_point_count']}", flush=True)
                    write_json(run_dir / "run.json", record)
        with open(run_dir / "trace_summaries.jsonl", "w", encoding="utf-8") as stream:
            for track_id in sorted(stats):
                summary = stats[track_id]
                summary.update({"end_time": None, "state": "observation_window_closed",
                                "termination_reason": "offline_processing_boundary_not_confirmed_track_end"})
                dump_row(stream, summary)
        writer.release()
        writer = None
        check = cv2.VideoCapture(str(run_dir / "replay.mp4"))
        replay_frames = int(check.get(cv2.CAP_PROP_FRAME_COUNT))
        replay_readable, _ = check.read()
        check.release()
        if not replay_readable or replay_frames != record["processed_frames"]:
            raise RuntimeError("replay readback does not match processed frames")
        record.update({"status": "completed", "unique_track_count": len(stats),
            "processed_video_seconds": record["processed_frames"] / fps,
            "wall_seconds": time.perf_counter() - start,
            "frame_processing_seconds_p50": float(np.percentile(timings, 50)),
            "frame_processing_seconds_p95": float(np.percentile(timings, 95)),
            "timing_scope": "decode+predict+track+write; includes first-frame warmup; offline throughput only",
            "max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "gpu_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else None,
            "replay_frames_verified": replay_frames})
        record["processing_fps"] = record["processed_frames"] / record["wall_seconds"]
        record["artifact_hashes"] = {p.name: sha256(p) for p in sorted(run_dir.iterdir())
                                      if p.is_file() and p.name != "run.json"}
    except Exception:
        record.update({"status": "failed", "error": traceback.format_exc()})
        raise
    finally:
        if cap is not None:
            cap.release()
        if writer is not None:
            writer.release()
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "run.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return run_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/e00_public_demo.json")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    run(args.config.resolve(), args.run_id)
