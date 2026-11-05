#!/usr/bin/env python3
"""Build the E01 video manifest from curated source metadata.

Run with the project perception environment because OpenCV is used only for
probing container metadata:
    .venv/bin/python scripts/build_e01_manifest.py

The script never rewrites the videos. It records SHA256, FPS, frame count,
resolution, duration and the human-supplied provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = PROJECT_DIR / "data" / "e01" / "video_sources.json"
DEFAULT_OUTPUT = PROJECT_DIR / "data" / "e01" / "videos.jsonl"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path) -> dict:
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV 无法打开视频: {path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        width = int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
        height = int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        fourcc_value = int(capture.get(cv2.CAP_PROP_FOURCC))
        fourcc = fourcc_value.to_bytes(4, "little").decode("ascii", errors="replace").strip("\x00")
        ok, frame = capture.read()
        first_frame_decoded = bool(ok and frame is not None)
    finally:
        capture.release()

    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        raise RuntimeError(f"视频元数据无效: {path}: fps={fps}, frames={frame_count}, {width}x{height}")

    return {
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_seconds": round(frame_count / fps, 3),
        "codec_fourcc": fourcc,
        "first_frame_decoded": first_frame_decoded,
        "probe_method": "opencv_videocapture_metadata",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 E01 视频清单（只读视频，不修改媒体）")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    sources_path = args.sources if args.sources.is_absolute() else PROJECT_DIR / args.sources
    output_path = args.output if args.output.is_absolute() else PROJECT_DIR / args.output
    with sources_path.open("r", encoding="utf-8") as handle:
        source_config = json.load(handle)

    videos = []
    for source in source_config["videos"]:
        path = PROJECT_DIR / source["path"]
        if not path.is_file():
            raise SystemExit(f"已登记视频不存在: {path}")
        measured = probe_video(path)
        duration = measured["duration_seconds"]
        for window in source.get("used_windows", []):
            if window["start"] < 0 or window["end"] > duration + 0.05:
                raise SystemExit(
                    f"{source['video_id']} 的 used_window 超出视频时长: "
                    f"{window} > {duration}"
                )
        row = {
            "schema_version": "e01-video-v1",
            "video_id": source["video_id"],
            "path": source["path"],
            "source_url": source["source_url"],
            "source_kind": source["source_kind"],
            "source_group": source["source_group"],
            "camera_id": source["camera_id"],
            "camera_label": source["camera_label"],
            "absolute_capture_time": source.get("capture_time"),
            "time_basis": source["time_basis"],
            "purpose": source["purpose"],
            "split": source["split"],
            "is_test_set": bool(source["is_test_set"]),
            "test_role": source["test_role"],
            "used_windows": source.get("used_windows", []),
            "license_status": source["license_status"],
            "notes": source.get("notes", ""),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            **measured,
        }
        videos.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in videos:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.write("\n")

    print(f"已生成 {output_path}")
    for row in videos:
        print(
            f"  {row['video_id']}: {row['width']}x{row['height']}, "
            f"{row['fps']} FPS, {row['frame_count']} 帧, {row['duration_seconds']} 秒, "
            f"split={row['split']}, sha256={row['sha256'][:12]}…"
        )
    print(f"生成时间: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
