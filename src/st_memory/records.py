"""Dependency-free evidence query and integrity checks."""

import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def query(path, camera_id=None, track_id=None, start=None, end=None, zone_id=None):
    if start is not None and end is not None and end < start:
        raise ValueError("end must be >= start")
    for row in read_jsonl(path):
        if camera_id is not None and row["camera_id"] != camera_id:
            continue
        if track_id is not None and row["track_id"] != track_id:
            continue
        if start is not None and row["video_time"] < start:
            continue
        if end is not None and row["video_time"] > end:
            continue
        if zone_id is not None and zone_id not in row.get("zone_ids", []):
            continue
        yield row


def verify_run(run_dir):
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text())
    seen = set()
    errors = []
    count = 0
    frame_counts = {}
    previous = None
    for row in read_jsonl(run_dir / "tracks.jsonl"):
        key = (row["run_id"], row["frame_index"], row["track_id"])
        if key in seen:
            errors.append(f"duplicate point: {key}")
        seen.add(key)
        count += 1
        frame_counts[row["frame_index"]] = frame_counts.get(row["frame_index"], 0) + 1
        if previous is not None and row["video_time"] < previous:
            errors.append("non-monotonic video time")
        previous = row["video_time"]
        if not 0 <= row["video_time"] < meta["source"]["duration_seconds"]:
            errors.append("video time outside source")
        if row["run_id"] != meta["run_id"]:
            errors.append("run_id mismatch")
    frames = list(read_jsonl(run_dir / "frames.jsonl"))
    if [f["frame_index"] for f in frames] != list(range(len(frames))):
        errors.append("missing or duplicated frame records")
    for frame in frames:
        if frame["tracked_count"] != frame_counts.get(frame["frame_index"], 0):
            errors.append(f"track count mismatch at frame {frame['frame_index']}")
    if count != meta.get("track_point_count"):
        errors.append("persisted point count differs from producer count")
    if len(frames) != meta.get("processed_frames"):
        errors.append("persisted frame count differs from producer count")
    if meta["status"] != "completed":
        errors.append(f"run not completed: {meta['status']}")
    for name, expected in meta.get("artifact_hashes", {}).items():
        if sha256(run_dir / name) != expected:
            errors.append(f"artifact checksum mismatch: {name}")
    return {"ok": not errors, "point_count": count, "frame_count": len(frames), "errors": errors}
