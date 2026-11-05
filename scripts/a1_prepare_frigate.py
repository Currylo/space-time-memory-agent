#!/usr/bin/env python3
"""Prepare a local public-video Frigate smoke setup; never install or start Docker."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/frigate"
DEFAULT_VIDEO = ROOT / "data/a2/mot17/videos/MOT17-04-FRCNN.mp4"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def probe(path: Path, python: Path) -> dict:
    code = """import cv2,json,sys
c=cv2.VideoCapture(sys.argv[1])
if not c.isOpened(): raise RuntimeError('video cannot be opened')
fps=c.get(cv2.CAP_PROP_FPS); n=int(c.get(cv2.CAP_PROP_FRAME_COUNT))
ok,_=c.read()
result={'fps':fps,'frames':n,'width':int(c.get(cv2.CAP_PROP_FRAME_WIDTH)),
        'height':int(c.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        'duration_seconds':n/fps if fps>0 else None,'first_frame_decoded':ok,
        'video_fourcc':''.join(chr((int(c.get(cv2.CAP_PROP_FOURCC)) >> (8*i)) & 255) for i in range(4))}
c.release()
print(json.dumps(result))
"""
    result = subprocess.run([str(python), "-c", code, str(path)], capture_output=True,
                            text=True, timeout=30, check=True)
    info = json.loads(result.stdout)
    if not info["first_frame_decoded"] or info["fps"] <= 0 or info["frames"] <= 0:
        raise ValueError("Invalid or undecodable source video")
    return info


def validate_vod_codec(info: dict) -> None:
    # preset-record-generic copies video. MP4 container/decodability alone does
    # not establish compatibility with Frigate's nginx VOD repackager.
    if info.get("video_fourcc", "").lower() not in ("avc1", "avc3", "h264", "x264"):
        raise ValueError(
            f"A1 VOD input must be H.264; observed FourCC={info.get('video_fourcc')!r}. "
            "Keep the original and create a separate H.264/yuv420p derivative, then use --video. "
            "The record preset copies the input codec; an MP4 filename is insufficient."
        )


def build_config(loiter_seconds: int) -> dict:
    if loiter_seconds < 1:
        raise ValueError("loiter_seconds must be positive")
    return {
        "mqtt": {"enabled": False},
        "detectors": {"cpu_a1": {"type": "cpu", "num_threads": 3}},
        "model": {"path": "/cpu_model.tflite", "width": 320, "height": 320},
        "ffmpeg": {"hwaccel_args": [], "output_args": {"record": "preset-record-generic"}},
        "objects": {"track": ["person"]},
        "record": {
            "enabled": True, "continuous": {"days": 1}, "motion": {"days": 0},
            "alerts": {"pre_capture": 2, "post_capture": 2, "retain": {"days": 1, "mode": "all"}},
            "detections": {"pre_capture": 2, "post_capture": 2, "retain": {"days": 1, "mode": "all"}},
        },
        "snapshots": {"enabled": True, "bounding_box": True, "retain": {"default": 1}},
        "cameras": {
            "a1_public_demo": {
                "ffmpeg": {"inputs": [{"path": "/input/public_demo.mp4",
                    "input_args": ["-re", "-stream_loop", "-1"], "roles": ["detect", "record"]}]},
                "detect": {"enabled": True, "width": 640, "height": 360, "fps": 3},
                "zones": {"a1_demo_zone": {
                    "friendly_name": "公开样例观察区（演示）",
                    "coordinates": "0.05,0.05,0.95,0.05,0.95,0.95,0.05,0.95",
                    "objects": ["person"], "inertia": 3, "loitering_time": loiter_seconds,
                }},
                "review": {"alerts": {"labels": ["person"], "required_zones": ["a1_demo_zone"]}},
            }
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--loiter-seconds", type=int, default=10)
    parser.add_argument("--probe-python", type=Path, default=ROOT / ".venv/bin/python")
    args = parser.parse_args()
    source = args.video.resolve(strict=True)
    # Preserve the virtualenv executable path; resolving its symlink would
    # launch the base interpreter without the project's cv2 dependency.
    probe_python = args.probe_python.absolute()
    if not probe_python.is_file():
        raise FileNotFoundError(probe_python)
    info = probe(source, probe_python)
    validate_vod_codec(info)
    config = build_config(args.loiter_seconds)
    runtime = DEPLOY / "runtime"
    # Refuse to overwrite any previously prepared/live setup.
    for path in (runtime / "config/config.yml", runtime / "input/public_demo.mp4",
                 runtime / "source_manifest.json"):
        if path.exists():
            raise FileExistsError(f"Existing setup preserved; use a separately versioned directory: {path}")
    for name in ("config", "media", "input"):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    original_hash = digest(source)
    target = runtime / "input/public_demo.mp4"
    shutil.copyfile(source, target)
    if digest(target) != original_hash:
        raise ValueError("Copied public video checksum mismatch")
    # JSON is valid YAML and accepted by the YAML loader; no extra dependency.
    config_path = runtime / "config/config.yml"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "prepared_not_deployed", "frigate_version": "0.18.2",
        "image": "ghcr.io/blakeblackshear/frigate:0.18.2", "image_digest": None,
        "source_path": str(source), "copy_path": str(target), "source_sha256": original_hash,
        "source_kind": "public_benchmark_development", "video": info,
        "config_sha256": digest(config_path), "compose_sha256": digest(DEPLOY / "compose.yaml"),
        "camera_id": "a1_public_demo", "zone_id": "a1_demo_zone",
        "loiter_seconds": args.loiter_seconds,
        "zone_coordinates": config["cameras"]["a1_public_demo"]["zones"]["a1_demo_zone"]["coordinates"],
        "detector": "Frigate CPU TFLite stock model, not our YOLOv8n baseline",
        "scope": "A1 single-stream platform smoke test; not A2 performance or comparison",
        "limits": ["CPU functional setup, no GPU acceleration or throughput claim",
                   "Repeated playback is for platform smoke testing only",
                   "Source-video and platform wall-clock alignment is unverified",
                   "Do not count repeated loops as independent events or extend ground truth duration",
                   "10-second zone presence is not learned trajectory-level loitering classification",
                   "Fall and crowd-count behavior are not claimed as native platform capabilities",
                   "Exact container config validation and startup still required"],
        "source_refs": [
            "https://github.com/blakeblackshear/frigate/tree/v0.18.2",
            "https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/ffmpeg.py",
            "https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/zone.py",
            "https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/record.py",
        ],
    }
    (runtime / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
