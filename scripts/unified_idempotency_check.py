#!/usr/bin/env python3
"""Idempotency, concurrency and interruption-recovery checks.

Part A uses the real ledger and the real Frigate API: every one of the 18
already-created candidates is submitted again and must be a ledger duplicate
with zero new POSTs.

Parts B-D use a local mock client and temporary ledgers to exercise concurrent
claims, a crash between POST and mark_created, and timeout/unknown retention.
The mock request log is separate from the real platform; it is labelled as a
simulation, not additional platform evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_memory.a2_adapter import (  # noqa: E402
    Ledger,
    PlatformClient,
    candidate_key,
    submit_candidate,
)

LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
PLATFORM = ROOT / "demo/data/unified_platform.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def candidate(loop_index=0, replay_id="idempotency-test"):
    return {
        "candidate_id": f"test-{replay_id}-{loop_index}",
        "source_video_sha256": "a" * 64,
        "camera": "test_camera",
        "label": "person",
        "rule_id": "region_stay",
        "rule_version": "test-v1",
        "zone_id": "test-zone",
        "zone_version": "test-roi-v1",
        "run_id": "test-run",
        "loop_index": loop_index,
        "replay_id": replay_id,
        "track_id": 7,
        "source_video_time": 12.0,
        "condition_met_video_time": 22.0,
        "evidence_start": 10.0,
        "evidence_end": 22.0,
        "score": None,
        "score_type": "rule_boolean_not_a_probability",
        "rule_satisfied": True,
        "kind": "stay",
        "evidence_frame_video_time": 22.0,
        "features": {"observed_dwell_seconds": 12.0},
        "model_derived": True,
        "ground_truth": False,
    }


class MockClient:
    def __init__(self, *, fail_create=False, seeded_event=None, delay=0.0):
        self.create_calls = []
        self.find_calls = []
        self.get_calls = []
        self.description_calls = []
        self.events_by_label = {}
        self.fail_create = fail_create
        self.delay = delay
        self.counter = 0
        if seeded_event:
            self.events_by_label[seeded_event["sub_label"]] = seeded_event

    def create_event(self, candidate, duration, pre_capture, sub_label, description):
        self.create_calls.append({
            "at": time.time(),
            "candidate_id": candidate["candidate_id"],
            "duration": duration,
            "pre_capture": pre_capture,
            "sub_label": sub_label,
        })
        if self.delay:
            time.sleep(self.delay)
        if self.fail_create:
            raise TimeoutError("simulated timeout before response")
        self.counter += 1
        event_id = f"mock-event-{self.counter}"
        event = {
            "id": event_id,
            "start_time": time.time() - pre_capture,
            "end_time": time.time() + duration,
            "has_clip": True,
            "sub_label": sub_label,
        }
        self.events_by_label[sub_label] = event
        return 200, {"success": True, "event_id": event_id}

    def get_event(self, event_id):
        self.get_calls.append(event_id)
        for event in self.events_by_label.values():
            if event["id"] == event_id:
                return 200, event
        return 404, {"success": False}

    def set_description(self, event_id, description):
        self.description_calls.append({"event_id": event_id, "description": description})
        return 200, {"success": True}

    def find_by_sub_label(self, camera, sub_label):
        self.find_calls.append({"camera": camera, "sub_label": sub_label})
        return self.events_by_label.get(sub_label)


def duplicate_replay_real(round_dir: Path) -> dict:
    data = json.loads(PLATFORM.read_text(encoding="utf-8"))
    client = PlatformClient("http://127.0.0.1:15000", timeout=20.0)
    status, events_before = client.request("GET", "/api/events?cameras=a2_unified_mot20_05&limit=1000")
    manual_before = [e for e in events_before if (e.get("data") or {}).get("type") == "api"] if isinstance(events_before, list) else []
    ledger = Ledger(LEDGER)
    try:
        attempts_before = sum(len(ledger.attempts(item["ledger_key"])) for item in data["candidates"])
        results = []
        for item in data["candidates"]:
            candidate_obj = None
            row = ledger.get(item["ledger_key"])
            if row:
                candidate_obj = json.loads(row["candidate_json"])
            if not candidate_obj:
                results.append({"candidate_id": item["candidate_id"], "state": "missing_ledger_row"})
                continue
            plan = item.get("request_plan") or {}
            result = submit_candidate(
                ledger, client, candidate_obj,
                duration=int(plan.get("duration", 3)),
                pre_capture=int(plan.get("pre_capture", 2)),
                emit_mode="offline_replay_injection",
                wait_clip=False,
                wait_description=False,
            )
            results.append({"candidate_id": item["candidate_id"], "state": result.get("state"), "key": result.get("key")})
        attempts_after = sum(len(ledger.attempts(item["ledger_key"])) for item in data["candidates"])
    finally:
        ledger.close()
    status_after, events_after = client.request("GET", "/api/events?cameras=a2_unified_mot20_05&limit=1000")
    manual_after = [e for e in events_after if (e.get("data") or {}).get("type") == "api"] if isinstance(events_after, list) else []
    return {
        "part": "real_ledger_duplicate_replay",
        "at_utc": utc_now(),
        "real_platform": True,
        "candidates": len(results),
        "states": _counts(item["state"] for item in results),
        "manual_events_before": len(manual_before),
        "manual_events_after": len(manual_after),
        "ledger_attempts_before": attempts_before,
        "ledger_attempts_after": attempts_after,
        "new_posts": max(0, len(manual_after) - len(manual_before)),
        "new_attempt_rows": max(0, attempts_after - attempts_before),
        "results": results,
    }


def concurrent_claim() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite"
        client = MockClient(delay=0.4)
        candidate_obj = candidate(loop_index=1, replay_id="concurrency-test")
        results = []
        barrier = threading.Barrier(2)

        def run():
            ledger = Ledger(path)
            try:
                barrier.wait(timeout=5)
                results.append(submit_candidate(
                    ledger, client, candidate_obj, duration=3,
                    emit_mode="offline_replay_injection",
                    wait_clip=False, wait_description=False,
                ))
            finally:
                ledger.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        return {
            "part": "concurrent_claim",
            "at_utc": utc_now(),
            "real_platform": False,
            "states": _counts(item.get("state") for item in results),
            "create_calls": len(client.create_calls),
            "results": results,
            "mock_request_log": {"create_calls": client.create_calls, "find_calls": client.find_calls},
            "pass": len(client.create_calls) == 1 and sorted(item.get("state") for item in results) in (["created", "in_progress"], ["in_progress", "created"]),
        }


def crash_after_post_recovery() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite"
        candidate_obj = candidate(loop_index=2, replay_id="recovery-test")
        key = candidate_key(candidate_obj)
        ledger = Ledger(path)
        claim = ledger.claim(candidate_obj)
        ledger.mark_writing_attempt(key, 200, {"success": True, "event_id": "seeded-event"})
        ledger.close()
        # Simulate a process crash: the POST succeeded, but mark_created was
        # never called.  A new process must reconcile by sub_label, not POST.
        sub_label = "a2:" + key[:24]
        seeded = {
            "id": "seeded-event",
            "start_time": 100.0,
            "end_time": 120.0,
            "has_clip": True,
            "sub_label": sub_label,
        }
        client = MockClient(seeded_event=seeded)
        ledger = Ledger(path, stale_seconds=0.0)
        try:
            result = submit_candidate(
                ledger, client, candidate_obj, duration=3,
                emit_mode="offline_replay_injection",
                wait_clip=False, wait_description=False,
            )
            row = ledger.get(key)
        finally:
            ledger.close()
        return {
            "part": "crash_after_post_recovery",
            "at_utc": utc_now(),
            "real_platform": False,
            "claim_initial": claim.state,
            "result_state": result.get("state"),
            "event_id": result.get("event_id"),
            "create_calls": len(client.create_calls),
            "find_calls": len(client.find_calls),
            "ledger_status": row.get("status") if row else None,
            "pass": result.get("state") == "reconciled" and len(client.create_calls) == 0,
        }


def timeout_unknown_retention() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite"
        candidate_obj = candidate(loop_index=3, replay_id="timeout-test")
        key = candidate_key(candidate_obj)
        client = MockClient(fail_create=True)
        ledger = Ledger(path)
        try:
            first = submit_candidate(
                ledger, client, candidate_obj, duration=3,
                emit_mode="offline_replay_injection",
                wait_clip=False, wait_description=False,
            )
            row_after_first = ledger.get(key)
            second = submit_candidate(
                ledger, client, candidate_obj, duration=3,
                emit_mode="offline_replay_injection",
                wait_clip=False, wait_description=False,
            )
            row_after_second = ledger.get(key)
            attempts = ledger.attempts(key)
        finally:
            ledger.close()
        return {
            "part": "timeout_unknown_retention",
            "at_utc": utc_now(),
            "real_platform": False,
            "first_state": first.get("state"),
            "first_reconciled_event_id": first.get("reconciled_event_id"),
            "ledger_status_after_first": row_after_first.get("status") if row_after_first else None,
            "second_state": second.get("state"),
            "ledger_status_after_second": row_after_second.get("status") if row_after_second else None,
            "create_calls": len(client.create_calls),
            "find_calls": len(client.find_calls),
            "attempt_states": [item.get("state") for item in attempts],
            "pass": first.get("state") == "unknown" and row_after_first.get("status") == "unknown",
        }


def replay_identity_keys() -> dict:
    a = candidate(loop_index=4, replay_id="replay-a")
    b = dict(a)
    b["replay_id"] = "replay-b"
    return {
        "part": "replay_identity_keys",
        "at_utc": utc_now(),
        "same_candidate_fields_different_replay": {
            "key_a": candidate_key(a),
            "key_b": candidate_key(b),
            "different": candidate_key(a) != candidate_key(b),
        },
        "pass": candidate_key(a) != candidate_key(b),
    }


def _counts(values) -> dict:
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    parser.add_argument("--skip-real", action="store_true")
    args = parser.parse_args()
    round_dir = args.round_dir.resolve()
    result = {
        "schema": "unified-platform-idempotency-v1",
        "created_at_utc": utc_now(),
        "round_dir": str(round_dir),
        "real_duplicate": None if args.skip_real else duplicate_replay_real(round_dir),
        "concurrent": concurrent_claim(),
        "crash_recovery": crash_after_post_recovery(),
        "timeout_unknown": timeout_unknown_retention(),
        "replay_identity": replay_identity_keys(),
    }
    result["all_non_real_tests_pass"] = all(
        result[key].get("pass") for key in ("concurrent", "crash_recovery", "timeout_unknown", "replay_identity")
    )
    if result["real_duplicate"]:
        result["real_duplicate_pass"] = (
            result["real_duplicate"]["new_posts"] == 0
            and result["real_duplicate"]["new_attempt_rows"] == 0
            and result["real_duplicate"]["states"].get("duplicate") == result["real_duplicate"]["candidates"]
        )
    write_json(round_dir / "platform" / "idempotency_and_recovery.json", result)
    print(json.dumps({
        "real_duplicate": {k: result["real_duplicate"][k] for k in ("candidates", "states", "new_posts", "new_attempt_rows", "manual_events_before", "manual_events_after")} if result["real_duplicate"] else None,
        "real_duplicate_pass": result.get("real_duplicate_pass"),
        "concurrent": result["concurrent"],
        "crash_recovery": result["crash_recovery"],
        "timeout_unknown": result["timeout_unknown"],
        "replay_identity": result["replay_identity"],
        "all_non_real_tests_pass": result["all_non_real_tests_pass"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
