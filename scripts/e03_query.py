#!/usr/bin/env python3
"""Query the E03 SQLite store with evidence coordinates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.track_store import QueryError, query_points, query_track_state  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="E03 轨迹/证据查询")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "e03" / "track_store.sqlite")
    parser.add_argument("--run-id")
    parser.add_argument("--camera-id")
    parser.add_argument("--track-id", type=int)
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    parser.add_argument("--zone-id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--summary", action="store_true", help="只返回轨迹状态；需要 --run-id 和 --track-id")
    args = parser.parse_args()

    db = args.db if args.db.is_absolute() else PROJECT_DIR / args.db
    try:
        if args.summary:
            if not args.run_id or args.track_id is None:
                raise QueryError("--summary 需要同时提供 --run-id 和 --track-id")
            result = query_track_state(db, args.run_id, args.track_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        rows = query_points(
            db,
            run_id=args.run_id,
            camera_id=args.camera_id,
            track_id=args.track_id,
            start=args.start,
            end=args.end,
            zone_id=args.zone_id,
            limit=args.limit,
        )
    except QueryError as exc:
        print(f"QUERY_ERROR: {exc}", file=sys.stderr)
        return 2
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
