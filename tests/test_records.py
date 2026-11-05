import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.records import query, sha256, verify_run


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rows = [
            {"run_id": "run", "frame_index": 0, "track_id": 1, "camera_id": "c1", "video_time": 0.0, "zone_ids": []},
            {"run_id": "run", "frame_index": 1, "track_id": 1, "camera_id": "c1", "video_time": 0.5, "zone_ids": ["z1"]},
        ]
        self.write_rows("tracks.jsonl", self.rows)
        self.write_rows("frames.jsonl", [{"frame_index": i, "tracked_count": 1} for i in range(2)])
        self.meta = {"status": "completed", "run_id": "run", "source": {"duration_seconds": 1.0},
                     "track_point_count": 2, "processed_frames": 2,
                     "artifact_hashes": {"tracks.jsonl": sha256(self.root / "tracks.jsonl")}}
        self.write_meta()

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, name, rows):
        (self.root / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def write_meta(self):
        (self.root / "run.json").write_text(json.dumps(self.meta))

    def test_closed_time_range_and_evidence_query(self):
        result = list(query(self.root / "tracks.jsonl", camera_id="c1", track_id=1, start=0.5, end=0.5, zone_id="z1"))
        self.assertEqual([r["frame_index"] for r in result], [1])
        self.assertEqual(list(query(self.root / "tracks.jsonl", camera_id="missing")), [])
        with self.assertRaises(ValueError):
            list(query(self.root / "tracks.jsonl", start=1, end=0))

    def test_complete_run(self):
        self.assertTrue(verify_run(self.root)["ok"])

    def test_truncated_persistence_is_detected(self):
        self.write_rows("tracks.jsonl", self.rows[:1])
        result = verify_run(self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(any("producer count" in e for e in result["errors"]))
        self.assertTrue(any("checksum" in e for e in result["errors"]))

    def test_duplicate_and_unfinished_run_are_detected(self):
        self.write_rows("tracks.jsonl", [self.rows[0], self.rows[0]])
        self.meta["status"] = "running"
        self.write_meta()
        errors = verify_run(self.root)["errors"]
        self.assertTrue(any("duplicate point" in e for e in errors))
        self.assertTrue(any("not completed" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
