#!/usr/bin/env python3
"""Import public-demo event provenance into a NEW developer-local review ledger.

No HTTP platform writes and no imported human review decisions. Existing ledgers
are refused, including real ledgers and previous local handoff review history.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.a2_adapter import Ledger, candidate_key  # noqa: E402


def initialize(seed_path: Path, output: Path) -> int:
    seed = json.loads(seed_path.read_text())
    if seed.get("schema") != "st-memory-handoff-ledger-seed-v1":
        raise ValueError("Unsupported handoff seed")
    keys = set()
    for item in seed["candidates"]:
        if item["key"] != candidate_key(item["candidate"]) or item["key"] in keys:
            raise ValueError("Invalid or duplicate candidate identity")
        keys.add(item["key"])
    if output.is_symlink() or output.exists() or any(Path(str(output) + s).exists() for s in ("-wal", "-shm")):
        raise FileExistsError(f"Existing ledger preserved; choose a new --output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb"):
        pass
    ledger = Ledger(output)
    try:
        ledger.connection.execute("BEGIN IMMEDIATE")
        for item in seed["candidates"]:
            ledger.import_existing(
                item["key"], item["event"],
                source=f"public-handoff:{seed['source_commit']}:{item['source']}",
                candidate=item["candidate"],
            )
        ledger.connection.execute("COMMIT")
        assert ledger.connection.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
    except Exception:
        if ledger.connection.in_transaction:
            ledger.connection.execute("ROLLBACK")
        raise
    finally:
        ledger.close()
    return len(keys)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=ROOT / "handoff/ledger_seed.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/a2/adapter/ledger.handoff.sqlite")
    args = parser.parse_args()
    count = initialize(args.seed, args.output)
    print(f"Imported {count} public-demo event snapshots; 0 human reviews: {args.output}")
    print(f"python3 demo/serve.py --review-ledger {args.output} --review-log data/a2/adapter/reviews.handoff.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
