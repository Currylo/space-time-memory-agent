#!/usr/bin/env python3
"""CLI for the A2 external-candidate adapter.

Examples:
  python3 scripts/a2_adapter.py submit --candidate-json candidate.json --duration 20 \
      --emit-mode online --loop-start 1793785296.5 --loop-seconds 280
  python3 scripts/a2_adapter.py review --key <key> --reviewer alice \
      --decision confirmed --note "checked clip and source frame" --evidence clip.mp4
  python3 scripts/a2_adapter.py status --key <key>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.a2_adapter import (  # noqa: E402
    Ledger,
    PlatformClient,
    candidate_key,
    submit_candidate,
)

DEFAULT_LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"


def read_json(path: Path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--origin", default="http://127.0.0.1:15000")
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit")
    submit.add_argument("--candidate-json", type=Path, required=True)
    submit.add_argument("--duration", type=int, required=True)
    submit.add_argument("--pre-capture", type=int, default=2)
    submit.add_argument("--emit-mode", choices=("online", "offline"), default="offline")
    submit.add_argument("--loop-start", type=float, default=None)
    submit.add_argument("--loop-seconds", type=float, default=None)
    submit.add_argument("--no-wait-clip", action="store_true")
    submit.add_argument("--output", type=Path, default=None)

    review = sub.add_parser("review")
    review.add_argument("--key", default=None)
    review.add_argument("--candidate-id", default=None)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("confirmed", "rejected", "uncertain"), required=True)
    review.add_argument("--note", required=True)
    review.add_argument("--evidence", type=Path, default=None)

    status = sub.add_parser("status")
    status.add_argument("--key", default=None)
    status.add_argument("--candidate-id", default=None)

    import_existing = sub.add_parser("import-existing")
    import_existing.add_argument("--candidate-json", type=Path, required=True)
    import_existing.add_argument("--event-id", required=True)
    import_existing.add_argument("--source", default="legacy_bridge_result")

    sub.add_parser("list")

    args = parser.parse_args()
    ledger = Ledger(args.ledger)
    try:
        if args.command == "submit":
            candidate = read_json(args.candidate_json)
            key = candidate_key(candidate)
            emit_epoch = None
            if args.emit_mode == "online":
                if args.loop_start is None or args.loop_seconds is None:
                    raise SystemExit("--emit-mode online requires --loop-start and --loop-seconds")
                now = time.time()
                elapsed = (now - args.loop_start) % args.loop_seconds
                delta = candidate["condition_met_video_time"] - elapsed
                if delta < 0:
                    delta += args.loop_seconds
                emit_epoch = now + delta
            client = PlatformClient(args.origin)
            result = submit_candidate(
                ledger, client, candidate, args.duration, args.pre_capture,
                emit_epoch=emit_epoch, wait_clip=not args.no_wait_clip,
            )
            result["key"] = key
            result["emit_mode"] = args.emit_mode
            result["target_emit_local"] = datetime.fromtimestamp(emit_epoch).isoformat() if emit_epoch else None
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / "submit_result.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "review":
            key = args.key
            if key is None and args.candidate_id:
                row = ledger.get_by_candidate_id(args.candidate_id)
                key = row["key"] if row else None
            if not key:
                raise SystemExit("--key or --candidate-id is required")
            record = ledger.add_review(key, args.reviewer, args.decision, args.note, args.evidence)
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if args.command == "import-existing":
            candidate = read_json(args.candidate_json)
            key = candidate_key(candidate)
            client = PlatformClient(args.origin)
            status, event = client.get_event(args.event_id)
            if status != 200:
                raise SystemExit(f"event not found: {status} {event}")
            ledger.import_existing(key, event, args.source, candidate=candidate)
            print(json.dumps({"key": key, "event_id": args.event_id, "review_status": "pending_review"}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            key = args.key
            if key is None and args.candidate_id:
                row = ledger.get_by_candidate_id(args.candidate_id)
                key = row["key"] if row else None
            if not key:
                raise SystemExit("--key or --candidate-id is required")
            row = ledger.get(key)
            if row is None:
                print(json.dumps({"key": key, "found": False}, ensure_ascii=False, indent=2))
                return 2
            print(json.dumps({
                "candidate": row,
                "attempts": ledger.attempts(key),
                "reviews": ledger.reviews(key),
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "list":
            print(json.dumps(ledger.all_candidates(), ensure_ascii=False, indent=2))
            return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
