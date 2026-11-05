import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.a2_adapter import Ledger, candidate_key, submit_candidate  # noqa: E402


def candidate(loop_index=0, track_id=7):
    return {
        "candidate_id": f"cand-{loop_index}-{track_id}",
        "source_video_sha256": "a" * 64,
        "camera": "cam",
        "label": "person",
        "rule_id": "long_stay",
        "rule_version": "v1",
        "zone_id": "zone",
        "zone_version": "v1",
        "run_id": "run-1",
        "loop_index": loop_index,
        "track_id": track_id,
        "source_video_time": 12.0,
        "condition_met_video_time": 22.0,
        "evidence_start": 10.0,
        "evidence_end": 25.0,
        "score": 0.8,
    }


class StubClient:
    def __init__(self, fail_create=False, reconcile_event=None):
        self.create_calls = []
        self.fail_create = fail_create
        self.reconcile_event = reconcile_event
        self.description = None

    def create_event(self, candidate, duration, pre_capture, sub_label, description):
        self.create_calls.append((candidate, duration, pre_capture, sub_label, description))
        if self.fail_create:
            raise TimeoutError("simulated timeout")
        return 200, {"success": True, "event_id": "evt-1"}

    def get_event(self, event_id):
        return 200, {
            "id": event_id, "start_time": 100.0, "end_time": 120.0,
            "has_clip": True, "sub_label": "a2:test",
        }

    def set_description(self, event_id, description):
        self.description = description
        return 200, {"success": True}

    def find_by_sub_label(self, camera, sub_label):
        return self.reconcile_event


class A2AdapterTests(unittest.TestCase):
    def test_candidate_key_binds_loop_and_track(self):
        self.assertNotEqual(candidate_key(candidate(loop_index=0)), candidate_key(candidate(loop_index=1)))
        self.assertNotEqual(candidate_key(candidate(track_id=7)), candidate_key(candidate(track_id=8)))

    def test_duplicate_across_ledger_instances_does_not_post_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite"
            client = StubClient()
            first = submit_candidate(Ledger(path), client, candidate(), 20)
            second = submit_candidate(Ledger(path), client, candidate(), 20)
            self.assertEqual(first["state"], "completed")
            self.assertEqual(second["state"], "duplicate")
            self.assertEqual(len(client.create_calls), 1)

    def test_concurrent_claim_is_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite"
            first = Ledger(path)
            second = Ledger(path)
            claim = first.claim(candidate())
            self.assertEqual(claim.state, "claimed")
            other = second.claim(candidate())
            self.assertEqual(other.state, "in_progress")

    def test_timeout_reconciles_without_second_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = StubClient(fail_create=True, reconcile_event={"id": "evt-reconciled", "start_time": 1, "end_time": 2, "has_clip": True})
            result = submit_candidate(Ledger(Path(tmp) / "ledger.sqlite"), client, candidate(), 20)
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["reconciled_event_id"], "evt-reconciled")
            self.assertEqual(len(client.create_calls), 1)

    def test_review_defaults_pending_and_requires_explicit_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite")
            submit_candidate(ledger, StubClient(), candidate(), 20)
            key = candidate_key(candidate())
            row = ledger.get(key)
            self.assertEqual(row["review_status"], "pending_review")
            self.assertEqual(ledger.reviews(key), [])
            record = ledger.add_review(key, "alice", "confirmed", "checked source and clip", None)
            self.assertEqual(record["reviewer"], "alice")
            self.assertEqual(ledger.get(key)["review_status"], "confirmed")
            self.assertEqual(len(ledger.reviews(key)), 1)

    def test_review_rejects_automatic_or_empty_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite")
            submit_candidate(ledger, StubClient(), candidate(), 20)
            key = candidate_key(candidate())
            with self.assertRaises(ValueError):
                ledger.add_review(key, "", "confirmed", "note", None)
            with self.assertRaises(ValueError):
                ledger.add_review(key, "alice", "confirmed", "", None)
            with self.assertRaises(ValueError):
                ledger.add_review(key, "alice", "maybe", "note", None)


if __name__ == "__main__":
    unittest.main()
