import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.unified_review_guard import validate_legacy_review, validate_unified_review  # noqa: E402


def payload():
    return {
        "clock": {"status": "verified"},
        "replay": {"replay_id": "replay-1", "camera": "cam", "loop_index": 0},
        "source": {"sha256": "a" * 64},
    }


def item(clip_sha):
    return {
        "candidate_id": "candidate-1",
        "camera": "cam",
        "replay_id": "replay-1",
        "loop_index": 0,
        "source_video_sha256": "a" * 64,
        "event_id": "event-1",
        "ledger_key": "key-1",
        "rule_window_start": 10.0,
        "rule_window_end": 70.0,
        "evidence": {"status": "verified", "window": {"condition_ok": True}},
        "clip": {
            "http_status": 200,
            "decoded": True,
            "coverage": {"status": "verified"},
            "sha256": clip_sha,
        },
    }


class UnifiedReviewGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.evidence = Path(self.tmp.name) / "clip.mp4"
        self.evidence.write_bytes(b"test-clip")
        self.clip_sha = hashlib.sha256(self.evidence.read_bytes()).hexdigest()
        self.row = {"key": "key-1", "event_id": "event-1"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_same_source_review_passes(self):
        result = validate_unified_review(payload(), item(self.clip_sha), self.row, self.evidence)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["errors"], [])

    def test_clock_needs_review_is_rejected(self):
        data = payload()
        data["clock"]["status"] = "needs_review"
        result = validate_unified_review(data, item(self.clip_sha), self.row, self.evidence)
        self.assertFalse(result["ok"])
        self.assertIn("clock_status_needs_review", result["errors"])

    def test_evidence_partial_is_rejected(self):
        candidate = item(self.clip_sha)
        candidate["evidence"]["status"] = "partial_or_failed"
        result = validate_unified_review(payload(), candidate, self.row, self.evidence)
        self.assertFalse(result["ok"])
        self.assertIn("evidence_status_partial_or_failed", result["errors"])

    def test_clip_coverage_partial_is_rejected(self):
        candidate = item(self.clip_sha)
        candidate["clip"]["coverage"]["status"] = "partial"
        result = validate_unified_review(payload(), candidate, self.row, self.evidence)
        self.assertFalse(result["ok"])
        self.assertIn("clip_coverage_partial", result["errors"])

    def test_clip_missing_is_rejected(self):
        result = validate_unified_review(payload(), item(self.clip_sha), self.row, Path(self.tmp.name) / "missing.mp4")
        self.assertFalse(result["ok"])
        self.assertIn("evidence_file_missing", result["errors"])

    def test_clip_hash_mismatch_is_rejected(self):
        result = validate_unified_review(payload(), item("b" * 64), self.row, self.evidence)
        self.assertFalse(result["ok"])
        self.assertIn("evidence_file_hash_mismatch", result["errors"])

    def test_event_id_mismatch_is_rejected(self):
        row = {"key": "key-1", "event_id": "other-event"}
        result = validate_unified_review(payload(), item(self.clip_sha), row, self.evidence)
        self.assertFalse(result["ok"])
        self.assertIn("event_id_mismatch", result["errors"])

    def test_source_or_replay_mismatch_is_rejected(self):
        candidate = item(self.clip_sha)
        candidate["source_video_sha256"] = "b" * 64
        result = validate_unified_review(payload(), candidate, self.row, self.evidence)
        self.assertFalse(result["ok"])
        self.assertIn("source_hash_mismatch", result["errors"])

    def test_legacy_review_keeps_legacy_rule(self):
        candidate = {"key": "legacy-key", "evidence": "old.mp4"}
        row = {"key": "legacy-key", "event_id": "old-event"}
        result = validate_legacy_review(candidate, row, self.evidence)
        self.assertTrue(result["ok"], result)
        missing = validate_legacy_review(candidate, row, Path(self.tmp.name) / "missing.mp4")
        self.assertFalse(missing["ok"])
        self.assertIn("evidence_file_missing", missing["errors"])


if __name__ == "__main__":
    unittest.main()
