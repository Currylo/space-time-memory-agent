#!/usr/bin/env python3
"""Check that a platform event clip contains the same source frames as the candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

W, H = 320, 180


def frames(ffmpeg, path, fps):
    process = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={W}:{H},format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=180,
    )
    data = np.frombuffer(process.stdout, dtype=np.uint8)
    count = len(data) // (W * H)
    return data[:count * W * H].reshape(count, H, W).astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--event-start", type=float, required=True)
    parser.add_argument("--loop-start", type=float, required=True)
    parser.add_argument("--loop-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source_frames = frames(args.ffmpeg, args.source, args.fps)
    clip_frames = frames(args.ffmpeg, args.clip, args.fps)
    rows = []
    for index, clip_frame in enumerate(clip_frames):
        mse = ((source_frames - clip_frame) ** 2).mean(axis=(1, 2))
        best = int(np.argmin(mse))
        clip_t = index / args.fps
        expected = (args.event_start + clip_t - args.loop_start) % args.loop_seconds
        rows.append({
            "clip_time": round(clip_t, 3),
            "best_source_time": round(best / args.fps, 3),
            "expected_source_time": round(expected, 3),
            "error_seconds": round((best / args.fps - expected + args.loop_seconds / 2) % args.loop_seconds - args.loop_seconds / 2, 3),
            "mse": round(float(mse[best]), 2),
        })
    errors = [row["error_seconds"] for row in rows if row["mse"] < 120]
    deltas = [rows[i + 1]["best_source_time"] - rows[i]["best_source_time"] for i in range(len(rows) - 1)]
    wrap_steps = sum(1 for delta in deltas if delta < -args.loop_seconds / 2)
    result = {
        "source": str(args.source),
        "clip": str(args.clip),
        "fps": args.fps,
        "event_start": args.event_start,
        "loop_start": args.loop_start,
        "loop_seconds": args.loop_seconds,
        "matches": rows,
        "median_mse": round(float(np.median([row["mse"] for row in rows])), 2),
        "median_error_seconds": round(float(np.median(errors)), 3) if errors else None,
        "constant_offset_seconds": round(float(np.median(errors)), 3) if errors else None,
        "wrap_steps": wrap_steps,
        "monotonic_1_to_1": wrap_steps <= 1 and all(
            delta >= -1 / args.fps or delta < -args.loop_seconds / 2
            for delta in deltas
        ),
    }
    (args.output / "clip_alignment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Side-by-side at the platform clip time nearest the expected fall frame.
    best_row = min(rows, key=lambda row: abs(row["expected_source_time"] - 4.0))
    clip_index = round(best_row["clip_time"] * args.fps)
    source_index = round(best_row["best_source_time"] * args.fps)
    clip_image = Image.fromarray(clip_frames[clip_index].astype(np.uint8)).convert("RGB").resize((960, 540))
    source_image = Image.fromarray(source_frames[source_index].astype(np.uint8)).convert("RGB").resize((960, 540))
    sheet = Image.new("RGB", (960, 1080), (0, 0, 0))
    sheet.paste(source_image, (0, 0)); sheet.paste(clip_image, (0, 540))
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 10), f"source t={best_row['best_source_time']:.2f}s", fill=(255, 255, 0))
    draw.text((10, 550), f"platform clip t={best_row['clip_time']:.2f}s", fill=(255, 255, 0))
    sheet.save(args.output / "clip-vs-source.png")
    print(json.dumps({k: result[k] for k in ("median_mse", "median_error_seconds", "monotonic_1_to_1")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
