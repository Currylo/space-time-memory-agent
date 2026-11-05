#!/usr/bin/env python3
"""A2: fetch the seven MOT17 training scenes (FRCNN variant) one by one.

MOT17-02/04/05/09/10/11/13 are the seven distinct training scenes.  Each
scene has DPM/FRCNN/SDP detector variants with identical images and GT; only
the FRCNN variant is registered here, so scene counts are not inflated.

The existing single-sequence fetcher is reused unchanged.  This wrapper adds
idempotent skip, per-sequence logs and a machine-readable fetch summary.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch seven MOT17 training scenes")
    parser.add_argument("--sequences", nargs="*", default=SEQUENCES)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk-kb", type=int, default=1024)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "data/a2/mot17/raw")
    parser.add_argument("--log-dir", type=Path, default=PROJECT_DIR / "reports/a2/mot17_fetch_logs")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for sequence in args.sequences:
        output = args.output_dir / sequence
        image_count = len(list((output / "img1").glob("*.jpg"))) if (output / "img1").is_dir() else 0
        gt_exists = (output / "gt" / "gt.txt").is_file()
        if image_count > 0 and gt_exists:
            print(f"[skip] {sequence}: {image_count} images + gt already present", flush=True)
            results.append({
                "sequence": sequence,
                "status": "skipped_existing",
                "image_count": image_count,
                "gt_present": gt_exists,
            })
            continue

        started = time.monotonic()
        command = [
            sys.executable,
            str(PROJECT_DIR / "scripts" / "e02_fetch_mot17_sequence.py"),
            "--sequence",
            sequence,
            "--output",
            str(output),
            "--workers",
            str(args.workers),
            "--chunk-kb",
            str(args.chunk_kb),
        ]
        log_path = args.log_dir / f"{sequence}.log"
        print(f"[fetch] {sequence} -> {log_path}", flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            log.write("$ " + " ".join(command) + "\n")
            log.flush()
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True)
        result = {
            "sequence": sequence,
            "status": "completed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "log": str(log_path.relative_to(PROJECT_DIR)),
        }
        if completed.returncode == 0:
            result["image_count"] = len(list((output / "img1").glob("*.jpg")))
            result["gt_present"] = (output / "gt" / "gt.txt").is_file()
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if completed.returncode != 0:
            break

    summary = {
        "schema": "a2-mot17-fetch-summary-v1",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sequences": results,
        "completed": sum(1 for item in results if item["status"] in {"completed", "skipped_existing"}),
        "failed": sum(1 for item in results if item["status"] == "failed"),
    }
    summary_path = PROJECT_DIR / "data/a2/mot17_fetch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
