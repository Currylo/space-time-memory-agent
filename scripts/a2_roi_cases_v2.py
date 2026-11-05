#!/usr/bin/env python3
"""ROI case builder v2 with explicit observed-frame coverage and pending review.

Fixes the v1 timing shortcut: gaps longer than max_gap split a run, dwell is
observed samples * sample period, and span includes frame coverage. Review is
NOT auto-confirmed; a separate operator submission is required.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ROI = (0.10, 0.30, 0.38, 0.95)
SAMPLE_PERIOD = 1.0 / 3.0
MAX_GAP = 0.4


def inside(x, y):
    return ROI[0] <= x <= ROI[2] and ROI[1] <= y <= ROI[3]


def load_tracks(path):
    tracks = defaultdict(list)
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            tracks[row["track_id"]].append(row)
    for rows in tracks.values():
        rows.sort(key=lambda row: row["video_time"])
    return tracks


def observed_runs(rows):
    runs = []
    current = None
    last_row = None
    for row in rows:
        x = (row["bbox_xyxy"][0] + row["bbox_xyxy"][2]) / 2 / 960
        y = row["bbox_xyxy"][3] / 540
        in_zone = inside(x, y)
        if in_zone:
            if current is None:
                current = {"start": row["video_time"], "last": row["video_time"], "count": 1, "first_row": row}
            else:
                gap = row["video_time"] - current["last"]
                if gap > MAX_GAP:
                    runs.append(_finish(current, row["video_time"]))
                    current = {"start": row["video_time"], "last": row["video_time"], "count": 1, "first_row": row}
                else:
                    current["last"] = row["video_time"]
                    current["count"] += 1
        elif current is not None:
            runs.append(_finish(current, row["video_time"]))
            current = None
        last_row = row
    if current is not None:
        runs.append(_finish(current, None))
    return runs


def _finish(current, next_outside_time):
    observed = current["count"] * SAMPLE_PERIOD
    span = current["last"] - current["start"] + SAMPLE_PERIOD
    return {
        "start": current["start"],
        "end": current["last"],
        "observed_dwell_seconds": round(observed, 3),
        "span_seconds": round(span, 3),
        "gap_seconds": round(max(0.0, span - observed), 3),
        "observed_points": current["count"],
        "right_censored": next_outside_time is None,
        "first_row": current["first_row"],
    }


def nearest(rows, time_value):
    return min(rows, key=lambda row: abs(row["video_time"] - time_value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--pass-track", type=int, default=38)
    parser.add_argument("--short-track", type=int, default=40)
    parser.add_argument("--long-track", type=int, default=24)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)
    (args.output / "frames").mkdir()
    (args.output / "clips").mkdir()

    tracks = load_tracks(args.tracks)
    selected = [
        ("normal_pass_through", args.pass_track),
        ("short_stay", args.short_track),
        ("over_threshold_stay", args.long_track),
    ]
    cases = []
    for case_type, track_id in selected:
        rows = tracks.get(track_id)
        if not rows:
            raise SystemExit(f"track {track_id} not found")
        runs = observed_runs(rows)
        if not runs:
            raise SystemExit(f"track {track_id} has no ROI run")
        run = max(runs, key=lambda item: item["observed_dwell_seconds"])
        start, end = run["start"], run["end"]
        mid = nearest(rows, (start + end) / 2)
        frame_time = mid["video_time"]
        frame_path = args.output / "frames" / f"{case_type}-t{frame_time:.1f}.png"
        subprocess.run(
            [args.ffmpeg, "-v", "error", "-ss", f"{frame_time:.3f}", "-i", str(args.video),
             "-frames:v", "1", "-y", str(frame_path)],
            check=True,
        )
        image = Image.open(frame_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle([ROI[0] * 960, ROI[1] * 540, ROI[2] * 960, ROI[3] * 540], outline=(0, 255, 0), width=3)
        box = mid["bbox_xyxy"]
        draw.rectangle(box, outline=(255, 255, 0), width=3)
        draw.text((box[0], max(0, box[1] - 15)), f"{case_type} track {track_id} t={frame_time:.1f}s", fill=(255, 255, 0))
        draw.text((10, 10), f"ROI a2-left-walkway-v1 | {case_type} | observed={run['observed_dwell_seconds']:.2f}s", fill=(255, 255, 255))
        image.save(frame_path)
        clip_path = args.output / "clips" / f"{case_type}-track{track_id}.mp4"
        clip_start = max(0.0, start - 2.0)
        clip_end = min(35.0, end + 2.0)
        subprocess.run(
            [args.ffmpeg, "-v", "error", "-ss", f"{clip_start:.3f}", "-to", f"{clip_end:.3f}",
             "-i", str(args.video), "-c", "copy", "-y", str(clip_path)],
            check=True,
        )
        cases.append({
            "case_id": f"a2-roi-v2-{case_type}-track{track_id}",
            "type": case_type,
            "track_id": track_id,
            "roi_run_start_seconds": round(start, 3),
            "roi_run_end_seconds": round(end, 3),
            "observed_dwell_seconds": run["observed_dwell_seconds"],
            "span_seconds": run["span_seconds"],
            "gap_seconds": run["gap_seconds"],
            "observed_points": run["observed_points"],
            "threshold_seconds": 10.0,
            "threshold_relation": "below" if run["observed_dwell_seconds"] < 10 else "at_or_above",
            "right_censored": run["right_censored"],
            "evidence_frame": str(frame_path.relative_to(args.output)),
            "evidence_clip": str(clip_path.relative_to(args.output)),
            "bbox_at_evidence_xyxy": [round(v, 2) for v in box],
            "measurement_source": "derived_from_model_tracks_with_observed_frame_coverage",
            "review_status": "pending_review",
            "review_note": "v1 confirmed fields were generator-written and are not used as human review evidence.",
        })

    result = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source_video": str(args.video),
        "source_video_scope": "one 35 s public clip; the 280 s platform input is 8 repeats of this clip",
        "roi_id": "a2-left-walkway-v1",
        "roi_normalized": ROI,
        "timing_method": "observed samples * 1/3 s; gaps > 0.4 s split runs; span includes frame period",
        "sample_period_seconds": SAMPLE_PERIOD,
        "max_gap_seconds": MAX_GAP,
        "cases": cases,
        "review_policy": "pending_review by default; operator must submit confirmed/rejected/uncertain with identity, time, note and evidence hash",
        "limits": [
            "No independent frame-level GT; these are model-derived case measurements, not human GT.",
            "The 280 s repeated video is for flow checks only.",
            "The ROI is a public-demo region, not a field business zone.",
            "Review status in v1 was auto-written; v2 keeps pending_review.",
        ],
    }
    (args.output / "roi_cases_v2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "cases": [
            {key: case[key] for key in ("case_id", "type", "track_id", "observed_dwell_seconds", "gap_seconds", "threshold_relation", "review_status")}
            for case in cases
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
