#!/usr/bin/env python3
"""Deterministic interruption/resume verification for the E03 store.

A --max-batches import is a deterministic pause at a committed batch boundary.
It exercises the same recovery path as an interruption without killing the
process. The resumed store is compared with a fresh full import.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.track_store import import_run, query_track_state, verify_store  # noqa: E402


def table_rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return [tuple(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="E03 中断恢复验证")
    parser.add_argument("--run", type=Path, default=PROJECT_DIR / "runs" / "e00-public-demo-005")
    parser.add_argument("--videos", type=Path, default=PROJECT_DIR / "data" / "e01" / "videos.jsonl")
    parser.add_argument("--zones", type=Path, default=PROJECT_DIR / "data" / "e01" / "zones" / "zones.json")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--pause-after-batches", type=int, default=2)
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "reports" / "E03恢复验证.json")
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_DIR / path

    run_dir = resolve(args.run)
    videos = resolve(args.videos)
    zones = resolve(args.zones)
    output = resolve(args.output)

    with tempfile.TemporaryDirectory(prefix="e03-recovery-") as tmp:
        tmp_path = Path(tmp)
        full_db = tmp_path / "full.sqlite"
        resume_db = tmp_path / "resume.sqlite"

        full = import_run(
            full_db, run_dir, videos, zones,
            batch_size=args.batch_size, max_batches=None, export_zones=None,
        )
        paused = import_run(
            resume_db, run_dir, videos, zones,
            batch_size=args.batch_size, max_batches=args.pause_after_batches,
            export_zones=None,
        )
        paused_counts = table_rows(resume_db, "SELECT COUNT(*) FROM points")[0][0]
        paused_status = paused["status"]
        partial_state = query_track_state(resume_db, full["run_id"], 1)
        if paused_status != "paused":
            raise SystemExit(f"预期 paused，实际 {paused_status}")
        if paused_counts >= full["points_total"]:
            raise SystemExit("暂停时已经导入了全部点，无法证明恢复")

        resumed = import_run(
            resume_db, run_dir, videos, zones,
            batch_size=args.batch_size, max_batches=None, export_zones=None,
        )
        if resumed["status"] != "completed":
            raise SystemExit(f"恢复后状态不是 completed: {resumed['status']}")

        checks = {
            "points_equal": table_rows(
                full_db,
                "SELECT run_id,track_id,frame_index,content_hash FROM points ORDER BY run_id,track_id,frame_index",
            ) == table_rows(
                resume_db,
                "SELECT run_id,track_id,frame_index,content_hash FROM points ORDER BY run_id,track_id,frame_index",
            ),
            "frames_equal": table_rows(
                full_db,
                "SELECT run_id,frame_index,content_hash FROM frames ORDER BY run_id,frame_index",
            ) == table_rows(
                resume_db,
                "SELECT run_id,frame_index,content_hash FROM frames ORDER BY run_id,frame_index",
            ),
            "tracks_equal": table_rows(
                full_db,
                "SELECT run_id,track_id,trace_id,observed_point_count,first_observed_time,"
                "last_observed_time,activity_state,exit_status,window_state FROM tracks "
                "ORDER BY run_id,track_id",
            ) == table_rows(
                resume_db,
                "SELECT run_id,track_id,trace_id,observed_point_count,first_observed_time,"
                "last_observed_time,activity_state,exit_status,window_state FROM tracks "
                "ORDER BY run_id,track_id",
            ),
            "zones_equal": table_rows(
                full_db,
                "SELECT run_id,track_id,frame_index,zone_id,zone_version,derived_hash FROM point_zones "
                "ORDER BY run_id,track_id,frame_index,zone_id,zone_version",
            ) == table_rows(
                resume_db,
                "SELECT run_id,track_id,frame_index,zone_id,zone_version,derived_hash FROM point_zones "
                "ORDER BY run_id,track_id,frame_index,zone_id,zone_version",
            ),
        }
        full_verify = verify_store(full_db, run_dir, videos, zones)
        resume_verify = verify_store(resume_db, run_dir, videos, zones)
        result = {
            "schema_version": "e03-recovery-check-v1",
            "run_id": full["run_id"],
            "batch_size": args.batch_size,
            "pause_after_batches": args.pause_after_batches,
            "paused": {
                "status": paused_status,
                "points": paused_counts,
                "points_total": full["points_total"],
                "track_1_state": {
                    "observed_point_count": partial_state["observed_point_count"],
                    "summary_point_count": partial_state["summary_point_count"],
                    "window_state": partial_state["window_state"],
                    "exit_status": partial_state["exit_status"],
                    "activity_state": partial_state["activity_state"],
                },
            },
            "resumed": resumed,
            "full": full,
            "equality": checks,
            "verify_full": {"ok": full_verify["ok"], "errors": full_verify["errors"]},
            "verify_resumed": {"ok": resume_verify["ok"], "errors": resume_verify["errors"]},
            "ok": all(checks.values()) and full_verify["ok"] and resume_verify["ok"],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n恢复验证结果: {output}")
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
