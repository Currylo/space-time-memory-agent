#!/usr/bin/env python3
"""Create a clean H.264 browser-compatible copy of the same source frames.

The frozen MOT20-05 file is mp4v; Chrome cannot decode it.  This script keeps
the original file untouched and produces a frame-preserving H.264 derivative
for the page's "original" mode.  It records both hashes and updates only the
demo dataset, not the frozen run result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_ffmpeg(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise SystemExit("ffmpeg not found; pass --ffmpeg")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/a2/unified/MOT20-05-raw.mp4")
    parser.add_argument("--existing", type=Path, default=ROOT / "deploy/frigate/runtime/input/a2_unified_mot20_05_h264.mp4")
    parser.add_argument("--output", type=Path, default=ROOT / "demo/media/unified/original_h264.mp4")
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists() or args.force:
        if args.existing.is_file() and not args.force:
            shutil.copyfile(args.existing, args.output)
        else:
            ffmpeg = find_ffmpeg(args.ffmpeg)
            result = subprocess.run([
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(args.source),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", "25", "-vsync", "cfr",
                "-movflags", "+faststart", "-an", str(args.output),
            ], text=True, capture_output=True)
            if result.returncode != 0:
                raise SystemExit(f"transcode failed: {result.stderr[-2000:]}")
    import cv2
    src_cap = cv2.VideoCapture(str(args.source))
    out_cap = cv2.VideoCapture(str(args.output))
    src_frames = int(src_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out_frames = int(out_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_fps = float(src_cap.get(cv2.CAP_PROP_FPS))
    out_fps = float(out_cap.get(cv2.CAP_PROP_FPS))
    src_cap.release(); out_cap.release()
    if src_frames != out_frames or abs(src_fps - out_fps) > 1e-6:
        raise SystemExit(f"derivative mismatch: frames {out_frames}/{src_frames}, fps {out_fps}/{src_fps}")
    data_path = ROOT / "demo/data/unified.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["files"]["original_h264"] = {
        "url": "/media/unified/original_h264.mp4",
        "sha256": sha256(args.output),
        "bytes": args.output.stat().st_size,
        "source_sha256": sha256(args.source),
        "note": "frame-preserving H.264 derivative of the frozen mp4v source; same 3315 frames, 25 fps, 132.6 s; original retained",
    }
    data.setdefault("source", {})["browser_original"] = {
        "url": "/media/unified/original_h264.mp4",
        "sha256": sha256(args.output),
        "derived_from_sha256": sha256(args.source),
        "note": "original mp4v is kept for audit; this clean H.264 copy is used only because Chrome cannot decode mp4v",
    }
    data_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "source_sha256": sha256(args.source),
        "output_sha256": sha256(args.output),
        "frames": out_frames,
        "fps": out_fps,
        "bytes": args.output.stat().st_size,
        "demo_data_updated": str(data_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
