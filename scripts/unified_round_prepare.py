#!/usr/bin/env python3
"""Prepare one controlled same-source Frigate replay without changing the platform.

This script is deliberately additive:

* derives a browser/Frigate-compatible H.264 file from the frozen mp4v source;
* keeps the original file untouched;
* backs up the current Frigate config, the adapter ledger and the running
  Frigate SQLite database with SQLite's consistent backup API;
* snapshots the pre-change API/config/event state.

It does not save a new Frigate config, restart the container, write an event or
modify the old three cases.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/a2/unified/MOT20-05-raw.mp4"
DEFAULT_INPUT = ROOT / "deploy/frigate/runtime/input/a2_unified_mot20_05_h264.mp4"
DEFAULT_CONFIG = ROOT / "deploy/frigate/runtime/config/config.yml"
DEFAULT_LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
DEFAULT_FRIGATE_DB = ROOT / "deploy/frigate/runtime/config/frigate.db"
DEFAULT_ORIGIN = "http://127.0.0.1:15000"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_ffmpeg(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise SystemExit("ffmpeg not found; pass --ffmpeg")


def ffmpeg_info(ffmpeg: Path, path: Path) -> dict:
    result = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(path)], text=True, capture_output=True)
    lines = [line.strip() for line in result.stderr.splitlines() if "Duration:" in line or "Stream #" in line]
    return {"returncode": result.returncode, "lines": lines}


def probe_frames(path: Path) -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    count = 0
    first = None
    last = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if first is None:
            first = (int(frame[0, 0, 0]), int(frame[0, 0, 1]), int(frame[0, 0, 2]))
        last = (int(frame[-1, -1, 0]), int(frame[-1, -1, 1]), int(frame[-1, -1, 2]))
        count += 1
    cap.release()
    return {
        "fps": fps,
        "container_frame_count": total,
        "decoded_frame_count": count,
        "width": width,
        "height": height,
        "duration_seconds": count / fps if fps else None,
        "first_pixel_bgr": first,
        "last_pixel_bgr": last,
    }


def request_json(origin: str, path: str):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(origin + path, timeout=20) as response:
            payload = response.read()
            try:
                return response.status, json.loads(payload)
            except json.JSONDecodeError:
                return response.status, {"text": payload.decode(errors="replace")}
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload)
        except Exception:
            return error.code, {"raw": payload.decode(errors="replace")}
    except Exception as error:  # noqa: BLE001
        return None, {"error": f"{type(error).__name__}: {error}"}


def backup_sqlite(source: Path, target: Path, *, read_only: bool = False) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if read_only:
            uri = f"file:{source}?mode=ro"
            src = sqlite3.connect(uri, uri=True, timeout=10)
        else:
            src = sqlite3.connect(str(source), timeout=10)
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        return {"ok": True, "source": str(source), "target": str(target), "bytes": target.stat().st_size, "sha256": sha256(target)}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "source": str(source), "target": str(target), "error": f"{type(error).__name__}: {error}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--force-transcode", action="store_true")
    parser.add_argument("--skip-transcode", action="store_true")
    parser.add_argument("--snapshot-only", action="store_true")
    args = parser.parse_args()

    round_dir = args.round_dir.resolve()
    for sub in ("input", "backups/demo", "snapshots", "platform", "clips", "alignment", "browser", "logs"):
        (round_dir / sub).mkdir(parents=True, exist_ok=True)

    source = args.source.resolve(strict=True)
    source_hash = sha256(source)
    expected_hash = "e5d1099517a3d5fe8cded2035fce54ff641171b3be5b97cfcb774db90a64bc8e"
    if source_hash != expected_hash:
        raise SystemExit(f"source hash changed: {source_hash} != frozen {expected_hash}")

    if args.snapshot_only:
        manifest = json.loads((round_dir / "input" / "input_manifest.json").read_text(encoding="utf-8"))
        backups = json.loads((round_dir / "input" / "backups.json").read_text(encoding="utf-8"))
        prechange = {
            "schema": "unified-platform-prechange-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "platform_origin": args.origin,
            "backups": backups,
            "api": {},
        }
        for key, path in {
            "version": "/api/version",
            "config_raw": "/api/config/raw",
            "config": "/api/config",
            "stats": "/api/stats",
            "events": "/api/events?limit=500",
            "review": "/api/review",
        }.items():
            status, body = request_json(args.origin, path)
            prechange["api"][key] = {"status": status, "body": body}
        write_json(round_dir / "snapshots" / "prechange_snapshot.json", prechange)
        print(json.dumps({"snapshot_only": True, "version": prechange["api"]["version"]}, ensure_ascii=False, indent=2))
        return 0

    ffmpeg = find_ffmpeg(args.ffmpeg)
    input_path = args.input.resolve()
    input_path.parent.mkdir(parents=True, exist_ok=True)
    source_info = ffmpeg_info(ffmpeg, source)

    transcode = None
    if args.skip_transcode:
        transcode = {"skipped": True}
    else:
        if input_path.exists() and not args.force_transcode:
            transcode = {"reused_existing": True}
        else:
            command = [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", "25", "-vsync", "cfr",
                "-movflags", "+faststart", "-an", str(input_path),
            ]
            result = subprocess.run(command, text=True, capture_output=True)
            transcode = {
                "command": command,
                "returncode": result.returncode,
                "stderr": result.stderr[-4000:],
            }
            if result.returncode != 0:
                raise SystemExit(f"transcode failed: {result.stderr[-2000:]}")
    if not input_path.is_file():
        raise SystemExit(f"input file missing: {input_path}")

    source_frames = probe_frames(source)
    input_frames = probe_frames(input_path)
    if input_frames["decoded_frame_count"] != source_frames["decoded_frame_count"]:
        raise SystemExit(
            f"derived frame count differs: {input_frames['decoded_frame_count']} vs {source_frames['decoded_frame_count']}"
        )
    if abs(input_frames["fps"] - source_frames["fps"]) > 1e-6:
        raise SystemExit("derived fps differs from source")

    input_manifest = {
        "schema": "unified-platform-input-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source.relative_to(ROOT)),
            "sha256": source_hash,
            "bytes": source.stat().st_size,
            "ffmpeg_info": source_info,
            "probe": source_frames,
            "note": "frozen MOT20-05 original; mp4v is not reliably replayable by Frigate VOD",
        },
        "derived": {
            "path": str(input_path.relative_to(ROOT)),
            "sha256": sha256(input_path),
            "bytes": input_path.stat().st_size,
            "ffmpeg_info": ffmpeg_info(ffmpeg, input_path),
            "probe": input_frames,
            "transcode": transcode,
            "note": "frame-preserving H.264 derivative; source time = derived time; original retained",
        },
    }
    write_json(round_dir / "input" / "input_manifest.json", input_manifest)

    # Backups before any platform mutation.
    config_backup = round_dir / "backups" / "config.yml.pre-unified"
    shutil.copy2(DEFAULT_CONFIG, config_backup)
    ledger_backup = round_dir / "backups" / "ledger.sqlite"
    ledger_result = backup_sqlite(DEFAULT_LEDGER, ledger_backup)
    frigate_backup = round_dir / "backups" / "frigate.db"
    frigate_result = backup_sqlite(DEFAULT_FRIGATE_DB, frigate_backup, read_only=True)
    demo_files = [
        ROOT / "demo/showcase.html",
        ROOT / "demo/unified.html",
        ROOT / "demo/assets/unified.js",
        ROOT / "demo/assets/unified.css",
        ROOT / "demo/serve.py",
        ROOT / "demo/data/unified.json",
        ROOT / "demo/data/review_candidates.json",
    ]
    demo_hashes = {}
    for path in demo_files:
        if path.is_file():
            target = round_dir / "backups" / "demo" / path.name
            shutil.copy2(path, target)
            demo_hashes[str(path.relative_to(ROOT))] = {"path": str(target.relative_to(round_dir)), "sha256": sha256(path)}

    prechange = {
        "schema": "unified-platform-prechange-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform_origin": args.origin,
        "backups": {
            "config": {"path": str(config_backup.relative_to(round_dir)), "sha256": sha256(config_backup)},
            "ledger": ledger_result,
            "frigate_db": frigate_result,
            "demo": demo_hashes,
        },
        "api": {},
    }
    for key, path in {
        "version": "/api/version",
        "config_raw": "/api/config/raw",
        "config": "/api/config",
        "stats": "/api/stats",
        "events": "/api/events?limit=500",
        "review": "/api/review",
    }.items():
        status, body = request_json(args.origin, path)
        prechange["api"][key] = {"status": status, "body": body}
    write_json(round_dir / "snapshots" / "prechange_snapshot.json", prechange)
    write_json(round_dir / "input" / "backups.json", prechange["backups"])

    print(json.dumps({
        "round_dir": str(round_dir),
        "source_sha256": source_hash,
        "input_path": str(input_path),
        "input_sha256": input_manifest["derived"]["sha256"],
        "frames": input_frames["decoded_frame_count"],
        "fps": input_frames["fps"],
        "backups": {
            "config": prechange["backups"]["config"],
            "ledger_ok": ledger_result["ok"],
            "frigate_db_ok": frigate_result["ok"],
        },
        "api_version": prechange["api"]["version"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
