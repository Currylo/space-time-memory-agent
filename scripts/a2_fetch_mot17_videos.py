#!/usr/bin/env python3
"""A2: build seven MOT17 scenes from the official sequence videos.

Why videos instead of the 5.86 GB ZIP: the official per-sequence MP4 files are
small and the ZIP Range extraction was measured at well under 0.1 MB/s on
this host.  The MP4 is an official MOTChallenge sequence video, but it may be
downscaled/re-encoded relative to the original JPEG frames.  This script
therefore records the video hash, native video size, original GT size and the
per-axis scale factor.  Evaluation predictions are scaled back to the
original GT coordinate system; results are labelled as the "official sequence
video" variant, not as the original-JPEG variant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SEQUENCES = [
    "MOT17-02-FRCNN",
    "MOT17-04-FRCNN",
    "MOT17-05-FRCNN",
    "MOT17-09-FRCNN",
    "MOT17-10-FRCNN",
    "MOT17-11-FRCNN",
    "MOT17-13-FRCNN",
]
VIDEO_URL = "https://motchallenge.net/sequenceVideos/{sequence}-raw.mp4"
LABELS_URL = "https://motchallenge.net/data/MOT17Labels.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_seqinfo(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def download_video(sequence: str, video_dir: Path) -> dict:
    video_dir.mkdir(parents=True, exist_ok=True)
    target = video_dir / f"{sequence}.mp4"
    url = VIDEO_URL.format(sequence=sequence)
    if target.is_file() and target.stat().st_size > 0:
        return {"sequence": sequence, "status": "skipped_existing", "path": str(target), "url": url}
    started = time.monotonic()
    command = [
        "curl", "-L", "--fail", "--retry", "3", "--retry-delay", "2",
        "--connect-timeout", "20", "--max-time", "600", "-o", str(target), url,
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        if target.exists():
            target.unlink()
        raise RuntimeError(f"download failed for {sequence}: {completed.stdout.strip()}")
    return {
        "sequence": sequence,
        "status": "downloaded",
        "path": str(target),
        "url": url,
        "bytes": target.stat().st_size,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def ensure_labels(labels_zip: Path) -> None:
    if labels_zip.is_file():
        return
    labels_zip.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {LABELS_URL}", flush=True)
    urllib.request.urlretrieve(LABELS_URL, labels_zip)


def extract_one(sequence: str, video: Path, labels_zip: Path, raw_dir: Path) -> dict:
    import cv2

    image_dir = raw_dir / "img1"
    gt_dir = raw_dir / "gt"
    image_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(labels_zip) as zf:
        gt_name = f"train/{sequence}/gt/gt.txt"
        ini_name = f"train/{sequence}/seqinfo.ini"
        if gt_name not in zf.namelist() or ini_name not in zf.namelist():
            raise RuntimeError(f"labels archive lacks {sequence}")
        (raw_dir / "gt" / "gt.txt").write_bytes(zf.read(gt_name))
        (raw_dir / "seqinfo.ini").write_bytes(zf.read(ini_name))

    ini = parse_seqinfo(raw_dir / "seqinfo.ini")
    expected_frames = int(ini["seqLength"])
    original_width = int(ini["imWidth"])
    original_height = int(ini["imHeight"])

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video {video}")
    video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps = float(cap.get(cv2.CAP_PROP_FPS))
    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if video_frames != expected_frames:
        cap.release()
        raise RuntimeError(f"{sequence}: video has {video_frames} frames, seqinfo says {expected_frames}")

    existing = sorted(image_dir.glob("*.jpg"))
    if len(existing) != expected_frames:
        for path in existing:
            path.unlink()
        started = time.monotonic()
        written = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            target = image_dir / f"{written + 1:06d}.jpg"
            if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise RuntimeError(f"{sequence}: failed to write {target}")
            written += 1
            if written % 200 == 0:
                print(f"{sequence}: extracted {written}/{expected_frames}", flush=True)
        elapsed = time.monotonic() - started
        if written != expected_frames:
            raise RuntimeError(f"{sequence}: extracted {written} frames, expected {expected_frames}")
    else:
        elapsed = 0.0
    cap.release()

    scale_x = original_width / video_width
    scale_y = original_height / video_height
    manifest = {
        "schema": "a2-mot17-video-source-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sequence": sequence,
        "official_split": "train",
        "detector_variant": "FRCNN",
        "video_url": VIDEO_URL.format(sequence=sequence),
        "video_path": str(video.relative_to(PROJECT_DIR)),
        "video_bytes": video.stat().st_size,
        "video_sha256": sha256_file(video),
        "video_frame_count": video_frames,
        "video_fps": video_fps,
        "video_width": video_width,
        "video_height": video_height,
        "original_width": original_width,
        "original_height": original_height,
        "frame_scale_x": scale_x,
        "frame_scale_y": scale_y,
        "frame_count": expected_frames,
        "fps": int(ini["frameRate"]),
        "gt_path": str((raw_dir / "gt" / "gt.txt").relative_to(PROJECT_DIR)),
        "gt_sha256": sha256_file(raw_dir / "gt" / "gt.txt"),
        "seqinfo_sha256": sha256_file(raw_dir / "seqinfo.ini"),
        "labels_zip": str(labels_zip.relative_to(PROJECT_DIR)),
        "labels_zip_sha256": sha256_file(labels_zip),
        "image_dir": str(image_dir.relative_to(PROJECT_DIR)),
        "image_count": expected_frames,
        "extraction_note": (
            "Frames extracted from the official MOTChallenge sequence video, not from the original "
            "MOT17.zip JPEG frames. The video may be downscaled/re-encoded; predictions are scaled "
            "back to the original GT coordinate system before TrackEval."
        ),
        "license": "CC BY-NC-SA 3.0 (official MOTChallenge page); third-party source conditions may also apply",
        "usage_scope": "public benchmark development evaluation only; not field data; not an unseen test set; no redistribution",
        "extraction_seconds": round(elapsed, 2),
    }
    (raw_dir / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch MOT17 sequence videos and extract frames")
    parser.add_argument("--sequences", nargs="*", default=SEQUENCES)
    parser.add_argument("--video-dir", type=Path, default=PROJECT_DIR / "data/a2/mot17/videos")
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_DIR / "data/a2/mot17/raw")
    parser.add_argument("--labels-zip", type=Path, default=PROJECT_DIR / "data/e02/source/MOT17Labels.zip")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    args.video_dir.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    ensure_labels(args.labels_zip)

    download_results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(download_video, sequence, args.video_dir): sequence for sequence in args.sequences}
        for future in as_completed(futures):
            sequence = futures[future]
            try:
                result = future.result()
                download_results.append(result)
                print(f"[download] {sequence}: {result['status']}", flush=True)
            except Exception as exc:
                download_results.append({"sequence": sequence, "status": "failed", "error": str(exc)})
                print(f"[download] {sequence}: FAILED {exc}", flush=True)

    extracted = []
    for sequence in args.sequences:
        video = args.video_dir / f"{sequence}.mp4"
        raw_dir = args.raw_dir / sequence
        print(f"[extract] {sequence}", flush=True)
        try:
            manifest = extract_one(sequence, video, args.labels_zip, raw_dir)
            extracted.append(manifest)
            print(
                f"[extract] {sequence}: {manifest['frame_count']} frames, "
                f"video {manifest['video_width']}x{manifest['video_height']} -> "
                f"GT {manifest['original_width']}x{manifest['original_height']}, "
                f"scale {manifest['frame_scale_x']:.4f}x{manifest['frame_scale_y']:.4f}",
                flush=True,
            )
        except Exception as exc:
            extracted.append({"sequence": sequence, "status": "failed", "error": str(exc)})
            print(f"[extract] {sequence}: FAILED {exc}", flush=True)

    summary = {
        "schema": "a2-mot17-video-fetch-summary-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "downloads": sorted(download_results, key=lambda item: item["sequence"]),
        "extracted": sorted(extracted, key=lambda item: item["sequence"]),
        "completed": sum(1 for item in extracted if "frame_count" in item),
        "failed": sum(1 for item in extracted if item.get("status") == "failed"),
    }
    summary_path = PROJECT_DIR / "data/a2/mot17/video_fetch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
