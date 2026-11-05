#!/usr/bin/env python3
"""Create one fall candidate and one normal negative from GMDCSA-24.

This does not re-run inference. It consumes the existing fixed-rule predictions
and source video, writes a unified adapter candidate, and saves the source clip
and frame used for later human review.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICTIONS = ROOT / "reports/a2/gmd/predictions_fixed.csv"
DEFAULT_DATASET = ROOT / "upstream/GMDCSA24"
DEFAULT_FFMPEG = "ffmpeg"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def video_path(dataset: Path, video_id: str) -> Path:
    match = re.fullmatch(r"S(\d)_(Fall|ADL)_(\d+)", video_id)
    if not match:
        raise SystemExit(f"unexpected video id: {video_id}")
    subject, kind, index = match.groups()
    return dataset / f"Subject {subject}" / kind / f"{int(index):02d}.mp4"


def probe(ffmpeg: str, path: Path):
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], text=True, capture_output=True, timeout=30)
    duration = None
    for line in result.stderr.splitlines():
        match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", line)
        if match:
            hours, minutes, seconds = match.groups()
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return duration, [line.strip() for line in result.stderr.splitlines() if "Duration:" in line or "Video:" in line]


def load_row(predictions: Path, video_id: str):
    with predictions.open() as handle:
        for row in csv.DictReader(handle):
            if row["video_id"] == video_id:
                return row
    raise SystemExit(f"video not found in predictions: {video_id}")


def parse_events(value: str):
    try:
        parsed = ast.literal_eval(value)
    except Exception:
        return []
    return [(float(start), float(end)) for start, end in parsed]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--fall-video", default="S1_Fall_01")
    parser.add_argument("--negative-video", default="S1_ADL_08")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)

    fall_row = load_row(args.predictions, args.fall_video)
    events = parse_events(fall_row["predicted_events_fixed"])
    if not events:
        raise SystemExit(f"{args.fall_video} has no predicted fall event")
    start, end = events[0]
    fall_path = video_path(args.dataset, args.fall_video)
    duration, stream_info = probe(args.ffmpeg, fall_path)
    condition_time = round(start, 3)
    candidate = {
        "candidate_id": f"{args.fall_video.lower()}-fall-{condition_time:.3f}",
        "source_video_sha256": sha256(fall_path),
        "camera": "a2_fall_demo",
        "label": "person",
        "rule_id": "fall_pose_rule",
        "rule_version": "gmd-fixed-v1",
        "zone_id": "a2_fall_full",
        "zone_version": "a2-fall-demo-v1",
        "run_id": "gmd-s1-fall01-demo",
        "loop_index": 0,
        "track_id": 1,
        "source_video_time": round(start, 3),
        "condition_met_video_time": condition_time,
        "evidence_start": round(max(0.0, start - 1.0), 3),
        "evidence_end": round(min(duration, end + 1.0), 3),
        "score": 0.80,
    }
    (args.output / "fall_candidate.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "source_probe.txt").write_text("\n".join(stream_info) + "\n", encoding="utf-8")

    frame_path = args.output / "fall_evidence_frame.png"
    subprocess.run([args.ffmpeg, "-v", "error", "-ss", f"{condition_time:.3f}", "-i", str(fall_path),
                    "-frames:v", "1", "-y", str(frame_path)], check=True)
    clip_path = args.output / "fall_evidence_clip.mp4"
    subprocess.run([args.ffmpeg, "-v", "error", "-ss", f"{candidate['evidence_start']:.3f}",
                    "-to", f"{candidate['evidence_end']:.3f}", "-i", str(fall_path),
                    "-c", "copy", "-y", str(clip_path)], check=True)

    negative_row = load_row(args.predictions, args.negative_video)
    negative_events = parse_events(negative_row["predicted_events_fixed"])
    negative_path = video_path(args.dataset, args.negative_video)
    negative_duration, negative_probe = probe(args.ffmpeg, negative_path)
    negative = {
        "video_id": args.negative_video,
        "source_video": str(negative_path),
        "source_video_sha256": sha256(negative_path),
        "duration_seconds": negative_duration,
        "predicted_events": negative_events,
        "triggered": bool(negative_events),
        "platform_event_created": False,
        "note": "Normal activity negative; no platform event is fabricated when the rule does not trigger.",
        "probe": negative_probe,
    }
    (args.output / "negative_case.json").write_text(json.dumps(negative, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "case_manifest.json").write_text(json.dumps({
        "created_at": datetime.now().astimezone().isoformat(),
        "fall_video": args.fall_video,
        "fall_source": str(fall_path),
        "condition_met_video_time": condition_time,
        "predicted_interval": [start, end],
        "candidate": candidate,
        "negative": negative,
        "scope": "GMDCSA-24 development video; single-case platform integration, not test-set gain",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "fall_video": args.fall_video,
        "condition_met_video_time": condition_time,
        "candidate_id": candidate["candidate_id"],
        "negative_video": args.negative_video,
        "negative_triggered": bool(negative_events),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
