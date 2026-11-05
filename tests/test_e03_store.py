import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.track_store import (  # noqa: E402
    BOUNDARY_METHOD,
    ConflictError,
    InputChangedError,
    ImportValidationError,
    QueryError,
    import_run,
    query_points,
    query_track_state,
    verify_store,
)

PROJECT = Path(__file__).resolve().parents[1]
RUN = PROJECT / "runs" / "e00-public-demo-005"
VIDEOS = PROJECT / "data" / "e01" / "videos.jsonl"
ZONES = PROJECT / "data" / "e01" / "zones" / "zones.json"
DEMO = PROJECT / "demo" / "data" / "demo.json"


@unittest.skipUnless(
    all(path.is_file() for path in (
        RUN / "run.json", RUN / "manifest.json", RUN / "tracks.jsonl",
        RUN / "frames.jsonl", RUN / "trace_summaries.jsonl", VIDEOS, ZONES, DEMO,
    )),
    "E03 integration assets excluded from source import; see docs/SOURCE_IMPORT.md",
)
class E03StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="e03-test-")
        cls.db = Path(cls.temp.name) / "store.sqlite"
        cls.full = import_run(cls.db, RUN, VIDEOS, ZONES, batch_size=1000, export_zones=None)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_counts_and_integrity(self):
        result = verify_store(self.db, RUN, VIDEOS, ZONES)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["counts"]["points"], 9599)
        self.assertEqual(result["counts"]["frames"], 300)
        self.assertEqual(result["counts"]["tracks"], 82)
        self.assertEqual(result["counts"]["zone_assignments"], 6942)

    def test_reimport_is_idempotent(self):
        result = import_run(self.db, RUN, VIDEOS, ZONES, batch_size=1000, export_zones=None)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["points_inserted_this_run"], 0)
        self.assertEqual(result["points_skipped_this_run"], 9599)
        self.assertEqual(result["points_imported"], 9599)

    def test_track_query_and_evidence(self):
        rows = query_points(
            self.db, run_id="e00-public-demo-005", track_id=7,
            start=5.5, end=5.6, limit=10,
        )
        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["frame_index"], 138)
        self.assertAlmostEqual(first["video_time"], 5.52, places=2)
        self.assertEqual(first["trace_id"], "public_demo/public_walk_01/e00-public-demo-005/7")
        self.assertEqual(first["zone_ids"], ["demo_central_walkway"])
        self.assertEqual(first["zone_versions"], ["e01-public-walk-zones-v1"])
        self.assertEqual(first["boundary_method"], BOUNDARY_METHOD)
        self.assertTrue(first["evidence"]["video_path"].endswith("people-walking.mp4"))
        self.assertEqual(first["evidence"]["frame_index"], 138)
        self.assertAlmostEqual(first["evidence"]["video_time"], 5.52, places=2)

    def test_zone_query(self):
        rows = query_points(
            self.db, run_id="e00-public-demo-005", zone_id="demo_central_walkway", limit=5
        )
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertIn("demo_central_walkway", row["zone_ids"])
            self.assertEqual(row["boundary_method"], BOUNDARY_METHOD)

    def test_empty_and_ambiguous_queries(self):
        self.assertEqual(
            query_points(self.db, run_id="e00-public-demo-005", track_id=999, limit=5), []
        )
        with self.assertRaises(QueryError):
            query_points(self.db, track_id=7)
        with self.assertRaises(QueryError):
            query_points(self.db)
        with self.assertRaises(QueryError):
            query_points(self.db, run_id="e00-public-demo-005", start=2, end=1)

    def test_state_semantics(self):
        state = query_track_state(self.db, "e00-public-demo-005", 7)
        self.assertEqual(state["observed_point_count"], 238)
        self.assertEqual(state["summary_point_count"], 238)
        self.assertAlmostEqual(state["last_observed_time"], 9.48, places=2)
        self.assertEqual(state["exit_status"], "unknown_not_confirmed")
        self.assertEqual(state["window_state"], "closed")
        self.assertEqual(state["activity_state"], "not_observed_before_window_end")
        self.assertIsNone(state["end_time"])
        self.assertGreaterEqual(state["observation_window_end"], 12.0)
        # Track 5 is observed at the end of the processed window.
        state5 = query_track_state(self.db, "e00-public-demo-005", 5)
        self.assertEqual(state5["activity_state"], "observed_near_window_end")

    def test_query_matches_demo_evidence(self):
        demo = json.loads(DEMO.read_text(encoding="utf-8"))
        track = next(item for item in demo["tracks"] if item["id"] == 7)
        point = next(item for item in track["points"] if abs(item[0] - 5.52) < 0.001)
        rows = query_points(
            self.db, run_id="e00-public-demo-005", track_id=7,
            start=5.52, end=5.52, limit=5,
        )
        self.assertEqual(len(rows), 1)
        candidate = rows[0]
        self.assertAlmostEqual(candidate["video_time"], point[0], places=2)
        for actual, expected in zip(candidate["bbox"], point[1:]):
            self.assertAlmostEqual(actual, expected, places=1)
        self.assertEqual(candidate["evidence"]["video_time"], candidate["video_time"])

    def _backup_db(self, name):
        target = Path(self.temp.name) / name
        source_conn = sqlite3.connect(str(self.db))
        target_conn = sqlite3.connect(str(target))
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
        return target

    def test_conflict_is_explicit(self):
        db = self._backup_db("conflict.sqlite")
        conn = sqlite3.connect(str(db))
        conn.execute(
            "UPDATE points SET content_hash='deadbeef' WHERE run_id=? AND track_id=7 AND frame_index=138",
            ("e00-public-demo-005",),
        )
        conn.commit()
        conn.close()
        with self.assertRaises(ConflictError):
            import_run(db, RUN, VIDEOS, ZONES, batch_size=1000, export_zones=None)

    def test_input_change_is_rejected(self):
        db = self._backup_db("changed.sqlite")
        conn = sqlite3.connect(str(db))
        conn.execute(
            "UPDATE runs SET input_hashes_json=? WHERE run_id=?",
            (json.dumps({"tracks.jsonl": "changed"}), "e00-public-demo-005"),
        )
        conn.commit()
        conn.close()
        with self.assertRaises(InputChangedError):
            import_run(db, RUN, VIDEOS, ZONES, batch_size=1000, export_zones=None)

    def test_interrupted_import_resumes(self):
        db = Path(self.temp.name) / "resume.sqlite"
        paused = import_run(
            db, RUN, VIDEOS, ZONES, batch_size=1000, max_batches=2, export_zones=None
        )
        self.assertEqual(paused["status"], "paused")
        self.assertLess(paused["points_imported"], 9599)
        # Queries are allowed against the committed partial state; state must
        # say the window is still open and must not claim a confirmed exit.
        partial_rows = query_points(
            db, run_id="e00-public-demo-005", track_id=1, limit=5
        )
        self.assertGreater(len(partial_rows), 0)
        partial_state = query_track_state(db, "e00-public-demo-005", 1)
        self.assertEqual(partial_state["window_state"], "open")
        self.assertEqual(partial_state["activity_state"], "observed_at_import_cut")
        self.assertEqual(partial_state["exit_status"], "unknown_not_confirmed")
        self.assertLess(partial_state["observed_point_count"], partial_state["summary_point_count"])
        resumed = import_run(db, RUN, VIDEOS, ZONES, batch_size=1000, export_zones=None)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["points_imported"], 9599)
        result = verify_store(db, RUN, VIDEOS, ZONES)
        self.assertTrue(result["ok"], result["errors"])

    def test_truncated_source_is_rejected(self):
        temp = Path(self.temp.name) / "truncated-run"
        temp.mkdir()
        for name in ("run.json", "manifest.json", "trace_summaries.jsonl", "frames.jsonl"):
            shutil.copy2(RUN / name, temp / name)
        lines = (RUN / "tracks.jsonl").read_text(encoding="utf-8").splitlines()
        (temp / "tracks.jsonl").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with self.assertRaises(ImportValidationError):
            import_run(
                Path(self.temp.name) / "truncated.sqlite",
                temp, VIDEOS, ZONES, batch_size=1000, export_zones=None,
            )


if __name__ == "__main__":
    unittest.main()
