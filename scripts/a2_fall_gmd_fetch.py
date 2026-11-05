#!/usr/bin/env python3
"""A2/P0: fetch GMDCSA-24 videos and interval labels from the official GitHub repo.

The Zenodo record was blocked by a network-level 403 in this environment, but
the official GitHub repository serves the same released files.  The repository
LICENSE is MIT; the Zenodo record additionally states CC BY 4.0.  Verify the
dataset license before redistribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO = "ekramalam/GMDCSA24-A-Dataset-for-Human-Fall-Detection-in-Videos"
BRANCH = "master"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
DEFAULT_OUTPUT = PROJECT_DIR / "upstream" / "GMDCSA24"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def api_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "st-memory-a2"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def list_dir(subject: int, kind: str) -> list[dict]:
    path = urllib.parse.quote(f"Subject {subject}/{kind}")
    return api_json(f"{API}/contents/{path}?ref={BRANCH}")


def download_file(relative: str, output: Path, expected_size: int | None) -> dict:
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and expected_size is not None and target.stat().st_size == expected_size:
        return {"path": relative, "status": "skipped_existing", "bytes": target.stat().st_size}
    url = RAW + "/" + urllib.parse.quote(relative)
    part = target.with_suffix(target.suffix + ".part")
    started = time.monotonic()
    command = [
        "curl", "-L", "--fail", "--retry", "3", "--retry-delay", "2",
        "--connect-timeout", "20", "--max-time", "600", "-o", str(part), url,
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        if part.exists():
            part.unlink()
        raise RuntimeError(f"download failed for {relative}: {completed.stdout.strip()}")
    part.replace(target)
    return {
        "path": relative,
        "status": "downloaded",
        "bytes": target.stat().st_size,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def video_metadata(path: Path) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"readable": False}
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {
        "readable": True,
        "fps": fps,
        "frame_count": frames,
        "width": width,
        "height": height,
        "duration_seconds": frames / fps if fps > 0 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch GMDCSA-24 videos and CSV labels")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    task_sizes = {}
    for subject in (1, 2, 3, 4):
        for kind in ("ADL", "Fall"):
            for item in list_dir(subject, kind):
                if item["type"] != "file":
                    continue
                relative = f"Subject {subject}/{kind}/{item['name']}"
                task_sizes[relative] = item.get("size")
        # CSV lives one level above ADL/Fall.
        for csv_name in ("ADL.csv", "Fall.csv"):
            task_sizes[f"Subject {subject}/{csv_name}"] = None

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {}
        for relative, expected in task_sizes.items():
            futures[pool.submit(download_file, relative, output, expected)] = relative
        for future in as_completed(futures):
            relative = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"[{result['status']}] {relative} ({result['bytes'] / 1024 / 1024:.1f} MB)", flush=True)
            except Exception as exc:
                results.append({"path": relative, "status": "failed", "error": str(exc)})
                print(f"[failed] {relative}: {exc}", flush=True)

    manifest = {
        "schema": "a2-gmdcsa24-source-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": f"https://github.com/{REPO}",
        "branch": BRANCH,
        "license": {
            "github_repository": "MIT (LICENSE file)",
            "zenodo_record": "CC BY 4.0 (Zenodo record 12921216, access blocked in this environment)",
            "note": "Verify dataset redistribution terms before publishing media.",
        },
        "usage": "public fall-detection research; event-level fall/ADL validation",
        "download_results": sorted(results, key=lambda item: item["path"]),
        "files": [],
    }
    video_paths = sorted(output.rglob("*.mp4"))
    for path in video_paths:
        relative = str(path.relative_to(output))
        manifest["files"].append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                **video_metadata(path),
            }
        )
    manifest["video_count"] = len(manifest["files"])
    manifest_path = output / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "video_count": manifest["video_count"],
        "failures": sum(1 for item in results if item["status"] == "failed"),
        "manifest": str(manifest_path),
    }, ensure_ascii=False, indent=2))
    return 0 if not any(item["status"] == "failed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
