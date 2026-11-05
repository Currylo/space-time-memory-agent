#!/usr/bin/env python3
"""Run E01 manifest, split, zone and annotation checks.

This script is dependency-free (standard library only) and can run with the
system Python. It reads the generated manifest and never modifies videos.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.e01_checks import print_result, run_checks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="E01 数据基础检查")
    parser.add_argument("--project-dir", type=Path, default=PROJECT_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_DIR / "reports" / "E01检查结果.json",
    )
    parser.add_argument("--print-json", action="store_true", help="同时打印完整 JSON")
    args = parser.parse_args()

    project_dir = args.project_dir if args.project_dir.is_absolute() else Path.cwd() / args.project_dir
    result = run_checks(project_dir)
    print_result(result)

    output = args.output if args.output.is_absolute() else project_dir / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n检查结果已写入: {output}")
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["summary"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
