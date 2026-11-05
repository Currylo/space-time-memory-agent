#!/usr/bin/env python3
"""Verify the E03 SQLite store against source JSONL and run metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.track_store import StoreError, verify_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="E03 存储完整性检查")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "e03" / "track_store.sqlite")
    parser.add_argument("--run", type=Path, default=PROJECT_DIR / "runs" / "e00-public-demo-005")
    parser.add_argument("--videos", type=Path, default=PROJECT_DIR / "data" / "e01" / "videos.jsonl")
    parser.add_argument("--zones", type=Path, default=PROJECT_DIR / "data" / "e01" / "zones" / "zones.json")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "reports" / "E03完整性检查.json")
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_DIR / path

    try:
        result = verify_store(
            db_path=resolve(args.db),
            run_dir=resolve(args.run),
            videos_manifest=resolve(args.videos),
            zones_config=resolve(args.zones),
        )
    except StoreError as exc:
        print(f"VERIFY_ERROR: {exc}", file=sys.stderr)
        return 2
    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n检查结果已写入: {output}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
