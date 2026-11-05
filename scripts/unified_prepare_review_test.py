#!/usr/bin/env python3
"""Create an isolated review-flow test ledger and page dataset.

This is for browser acceptance only.  It deliberately uses a separate SQLite
file and a separate JSONL log, and labels the candidate ``test_only``.  It never
writes the real ledger or claims a human review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_memory.a2_adapter import Ledger, candidate_key  # noqa: E402

REAL_PLATFORM = ROOT / "demo" / "data" / "unified_platform.json"
REAL_LEDGER = ROOT / "data" / "a2" / "adapter" / "ledger.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-data", type=Path, default=ROOT / "demo" / "data" / "unified_platform.test.json")
    parser.add_argument("--output-ledger", type=Path, default=ROOT / "data" / "a2" / "adapter" / "ledger.test.sqlite")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output_ledger.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite {args.output_ledger}; pass --force")
    if args.output_ledger.exists():
        args.output_ledger.unlink()
    data = json.loads(REAL_PLATFORM.read_text(encoding="utf-8"))
    source_candidate = data["candidates"][0]
    real = Ledger(REAL_LEDGER)
    try:
        row = real.get(source_candidate["ledger_key"])
        if row is None:
            raise SystemExit("real ledger row not found")
        candidate = json.loads(row["candidate_json"])
    finally:
        real.close()
    test_candidate = dict(candidate)
    test_candidate["replay_id"] = "playwright-review-flow-test"
    test_candidate["candidate_id"] = candidate["candidate_id"]
    key = candidate_key(test_candidate)
    ledger = Ledger(args.output_ledger)
    try:
        claim = ledger.claim(test_candidate)
        if claim.state != "claimed":
            raise SystemExit(f"unexpected claim state: {claim.state}")
        ledger.mark_created(key, "test-event-playwright-review", {"success": True, "test_only": True})
    finally:
        ledger.close()
    test_record = dict(source_candidate)
    test_record.update({
        "candidate_id": test_candidate["candidate_id"],
        "ledger_key": key,
        "replay_id": test_candidate["replay_id"],
        "event_id": "test-event-playwright-review",
        "platform_event_url": "#",
        "submit_state": "created",
        "review_status": "pending_review",
        "reviews": [],
        "test_only": True,
    })
    test_data = dict(data)
    test_data.update({
        "round_id": data["round_id"],
        "test_only": True,
        "test_notice": "浏览器复核流程测试：独立测试 ledger，记录不是真实人工复核。",
        "candidates": [test_record],
        "summary": dict(data.get("summary") or {}, test_only=True),
    })
    test_data["replay"] = dict(data.get("replay") or {}, test_only=True)
    test_data["replay"]["replay_id"] = test_candidate["replay_id"]
    test_data["replay"]["loop_index"] = test_candidate["loop_index"]
    args.output_data.parent.mkdir(parents=True, exist_ok=True)
    args.output_data.write_text(json.dumps(test_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "test_data": str(args.output_data.relative_to(ROOT)),
        "test_ledger": str(args.output_ledger.relative_to(ROOT)),
        "candidate_id": test_candidate["candidate_id"],
        "test_key": key,
        "real_ledger_untouched": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
