#!/usr/bin/env python3
"""Submit the prepared GMDCSA-24 fall candidate through the persistent adapter.

Run after the a2_fall_demo camera is running. The script waits for the camera's
first recording, maps the candidate condition time into the current loop, and
does not emit before the condition time.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.a2_adapter import Ledger, PlatformClient, candidate_key, submit_candidate  # noqa: E402

DEFAULT_LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
DEFAULT_DB = ROOT / "deploy/frigate/runtime/config/frigate.db"


def camera_loop_start(db_path: Path, camera: str, timeout: float = 90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if db_path.exists():
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = connection.execute(
                "select min(start_time) from recordings where camera=?", (camera,)
            ).fetchone()
            connection.close()
            if row and row[0]:
                return float(row[0])
        time.sleep(2)
    raise SystemExit(f"no recording found for {camera} within {timeout}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera", default="a2_fall_demo")
    parser.add_argument("--loop-seconds", type=float, required=True)
    parser.add_argument("--duration", type=int, default=6)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--origin", default="http://127.0.0.1:15000")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    candidate = json.loads(args.candidate_json.read_text())
    if candidate["camera"] != args.camera:
        raise SystemExit(f"candidate camera {candidate['camera']} != {args.camera}")
    loop_start = camera_loop_start(args.db, args.camera)
    now = time.time()
    elapsed = (now - loop_start) % args.loop_seconds
    delta = candidate["condition_met_video_time"] - elapsed
    if delta < 0:
        delta += args.loop_seconds
    emit_epoch = now + delta
    ledger = Ledger(args.ledger)
    try:
        result = submit_candidate(
            ledger, PlatformClient(args.origin), candidate, args.duration,
            pre_capture=2, emit_epoch=emit_epoch, wait_clip=True,
        )
    finally:
        ledger.close()
    args.output.mkdir(parents=True, exist_ok=False)
    result.update({
        "key": candidate_key(candidate),
        "loop_start_epoch": loop_start,
        "loop_start_local": datetime.fromtimestamp(loop_start).isoformat(),
        "loop_seconds": args.loop_seconds,
        "target_emit_epoch": emit_epoch,
        "target_emit_local": datetime.fromtimestamp(emit_epoch).isoformat(),
        "emit_mode": "online_loop_aligned",
    })
    (args.output / "fall_submit_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "state": result.get("state"),
        "event_id": result.get("event_id"),
        "key": result["key"],
        "condition_met_video_time": candidate["condition_met_video_time"],
        "target_emit_local": result["target_emit_local"],
        "emit_minus_condition_seconds": result.get("emit_minus_condition_seconds"),
        "event_start": (result.get("event") or {}).get("start_time"),
        "event_end": (result.get("event") or {}).get("end_time"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
