#!/usr/bin/env python3
"""Import one completed run into the E03 SQLite store.

The source run is never modified. Re-running the same input is idempotent;
different content under the same key raises a conflict. Use --max-batches to
simulate an interruption, then run the same command again to resume.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.track_store import (  # noqa: E402
    ConflictError,
    ImportValidationError,
    InputChangedError,
    StoreError,
    import_run,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="E03 轨迹 SQLite 导入")
    parser.add_argument("--run", type=Path, default=PROJECT_DIR / "runs" / "e00-public-demo-005")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "e03" / "track_store.sqlite")
    parser.add_argument("--videos", type=Path, default=PROJECT_DIR / "data" / "e01" / "videos.jsonl")
    parser.add_argument("--zones", type=Path, default=PROJECT_DIR / "data" / "e01" / "zones" / "zones.json")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=None, help="只导入前 N 批，模拟中断后退出；再次运行恢复")
    parser.add_argument("--export-zones", type=Path, default=PROJECT_DIR / "data" / "e03" / "zone_assignments.jsonl")
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_DIR / path

    try:
        result = import_run(
            db_path=resolve(args.db),
            run_dir=resolve(args.run),
            videos_manifest=resolve(args.videos),
            zones_config=resolve(args.zones),
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            export_zones=None if args.no_export else resolve(args.export_zones),
        )
    except InputChangedError as exc:
        print(f"INPUT_CHANGED: {exc}", file=sys.stderr)
        return 3
    except ConflictError as exc:
        print(f"CONFLICT: {exc}", file=sys.stderr)
        return 4
    except (ImportValidationError, StoreError) as exc:
        print(f"IMPORT_ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"UNEXPECTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "paused":
        print(
            "\n已按 --max-batches 暂停；再次运行同一命令（不带或带更大的 --max-batches）即可恢复。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
