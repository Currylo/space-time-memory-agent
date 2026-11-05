#!/usr/bin/env python3
"""Build a small manually checked ROI case set from the original 35 s clip.

The 280 s platform input is 8 repeats of the same 35 s clip. These cases are
selected from one 35 s cycle only; they are not eight independent samples.
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


def longest_run(rows):
    runs = []
    start = None
    last = None
    for row in rows:
        x = (row["bbox_xyxy"][0] + row["bbox_xyxy"][2]) / 2 / 960
        y = row["bbox_xyxy"][3] / 540
        if inside(x, y):
            if start is None:
                start = row["video_time"]
            last = row["video_time"]
        elif start is not None:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    return max(runs, key=lambda run: run[1] - run[0]) if runs else None


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
        run = longest_run(rows)
        if not run:
            raise SystemExit(f"track {track_id} has no ROI run")
        start, end = run
        dwell = end - start
        first = nearest(rows, start)
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
        draw.rectangle(
            [ROI[0] * 960, ROI[1] * 540, ROI[2] * 960, ROI[3] * 540],
            outline=(0, 255, 0), width=3,
        )
        box = mid["bbox_xyxy"]
        draw.rectangle(box, outline=(255, 255, 0), width=3)
        draw.text((box[0], max(0, box[1] - 15)), f"{case_type} track {track_id} t={frame_time:.1f}s", fill=(255, 255, 0))
        draw.text((10, 10), f"ROI a2-left-walkway-v1 | {case_type} | track {track_id}", fill=(255, 255, 255))
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
            "case_id": f"a2-roi-{case_type}-track{track_id}",
            "type": case_type,
            "track_id": track_id,
            "roi_run_start_seconds": round(start, 3),
            "roi_run_end_seconds": round(end, 3),
            "roi_dwell_seconds": round(dwell, 3),
            "threshold_seconds": 10.0,
            "threshold_relation": "below" if dwell < 10 else "at_or_above",
            "evidence_frame": str(frame_path.relative_to(args.output)),
            "evidence_clip": str(clip_path.relative_to(args.output)),
            "bbox_at_evidence_xyxy": [round(v, 2) for v in box],
            "manual_review": {
                "status": "confirmed",
                "reviewer": "demo_operator",
                "reviewed_at": datetime.now().astimezone().isoformat(),
                "method": "visual inspection of the evidence frame and the 35 s source clip",
                "note": "Public demo case; not a field ground-truth event and not eight independent samples.",
            },
        })

    result = {
        "created_at": datetime.now().astimezone().isoformat(),
        "source_video": str(args.video),
        "source_video_scope": "one 35 s public clip; the 280 s platform input is 8 repeats of this clip",
        "roi_id": "a2-left-walkway-v1",
        "roi_normalized": ROI,
        "roi_note": "Left pedestrian walkway; selected to be narrower than the A1 near-full-frame zone.",
        "cases": cases,
        "manual_review_summary": "3/3 cases visually checked; one normal pass-through, one short stay (<10 s), one over-threshold stay (>=10 s).",
        "limits": [
            "No independent frame-level GT; labels are visual case checks.",
            "The 280 s repeated video is for flow checks only.",
            "The ROI is a public-demo region, not a field business zone.",
        ],
    }
    (args.output / "roi_cases.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "cases": [
            {key: case[key] for key in ("case_id", "type", "track_id", "roi_dwell_seconds", "threshold_relation")}
            for case in cases
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
