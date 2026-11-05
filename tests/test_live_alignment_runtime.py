import json
import sys
import tempfile
import unittest
import copy
from pathlib import Path
from types import SimpleNamespace

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from unified_replay_round import LiveAligner, three_point_check  # noqa: E402


class LiveAlignmentRuntimeTests(unittest.TestCase):
    def _aligner(self, segments):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        aligner = LiveAligner(Path(tmp.name), "test-camera", SimpleNamespace(fps=25.0), latency=0.0, bias=0.0)
        aligner.segment_samples = segments
        return aligner

    def test_recording_model_uses_first_frame_and_coverage_end_on_fixture_segments(self):
        path = PROJECT_DIR / "tests/fixtures/clock_alignment.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        segments = data["live_alignment"]["segments"]
        aligner = self._aligner(segments)
        model = aligner.recording_model()
        self.assertIsNotNone(model)
        self.assertEqual(model.samples, 28)
        self.assertAlmostEqual(model.t0, 1793805307.0, places=6)
        self.assertAlmostEqual(model.drift, 1.0, places=8)
        self.assertAlmostEqual(model.max_abs_residual, 0.0, places=8)
        old_formula = segments[0]["segment_end_epoch"] - segments[0]["last_source_time"]
        self.assertAlmostEqual(old_formula, 1793805307.04, places=6)
        self.assertNotAlmostEqual(model.t0, old_formula, places=6)

    def test_recording_model_short_tail_and_250_frame_segment(self):
        segments = [
            {
                "segment_id": "full-250",
                "segment_start_epoch": 1000.0,
                "segment_end_epoch": 1010.0,
                "first_source_time": 0.0,
                "last_source_time": 9.96,
            },
            {
                "segment_id": "tail-65",
                "segment_start_epoch": 1010.0,
                "segment_end_epoch": 1012.6,
                "first_source_time": 10.0,
                "last_source_time": 12.56,
            },
        ]
        aligner = self._aligner(segments)
        model = aligner.recording_model()
        self.assertIsNotNone(model)
        self.assertEqual(model.samples, 4)
        self.assertAlmostEqual(model.t0, 1000.0, places=6)
        self.assertAlmostEqual(model.max_abs_residual, 0.0, places=8)

    def test_three_point_check_rejects_bad_endpoint_even_when_robust_fit_is_clean(self):
        path = PROJECT_DIR / "tests/fixtures/clock_alignment.json"
        segments = copy.deepcopy(json.loads(path.read_text())["live_alignment"]["segments"])
        for row in segments:
            row.pop("segment_anchors", None)
        segments[0]["segment_start_epoch"] += 2
        segments[0]["segment_end_epoch"] += 2
        aligner=self._aligner(segments)
        result=three_point_check(aligner,{"video_duration":132.6})
        self.assertEqual(result["status"],"needs_review")
        self.assertGreater(result["max_abs_residual_seconds"],1.)


if __name__ == "__main__":
    unittest.main()
