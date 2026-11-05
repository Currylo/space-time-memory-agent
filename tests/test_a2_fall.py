import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.a2_fall import (  # noqa: E402
    event_level_metrics,
    match_events,
    parse_fall_intervals,
    scores_to_events,
    temporal_iou,
    video_level_metrics,
)


class A2FallParserTests(unittest.TestCase):
    def test_standard_interval(self):
        intervals, note = parse_fall_intervals("Falling (SW)[3.4 to 6]; Sitting[0 to 3.4]")
        self.assertEqual(intervals, [(3.4, 6.0)])
        self.assertEqual(note, "")

    def test_malformed_interval(self):
        intervals, _ = parse_fall_intervals("Falling (BW)[2.3 6]; Sitting[0 to 2]")
        self.assertEqual(intervals, [(2.3, 6.0)])

    def test_missing_interval_is_flagged(self):
        intervals, note = parse_fall_intervals("Falling (FW); Sitting[0 to 1.7]")
        self.assertEqual(intervals, [])
        self.assertIn("without parseable", note)

    def test_multiple_fall_intervals(self):
        intervals, _ = parse_fall_intervals("Falling (BW)[2 to 4]; Falling (SW)[4 to 11]; Walking[0 to 2]")
        self.assertEqual(intervals, [(2.0, 11.0)])  # adjacent intervals merge

    def test_temporal_iou(self):
        self.assertAlmostEqual(temporal_iou((1, 3), (2, 4)), 1 / 3)

    def test_event_matching(self):
        matches, unmatched_pred, unmatched_gt = match_events(
            [(1.0, 3.0), (10.0, 12.0)],
            [(1.2, 3.2)],
            iou_threshold=0.3,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(unmatched_pred, [1])
        self.assertEqual(unmatched_gt, [])

    def test_hysteresis_event(self):
        times = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
        scores = [0, 0, 1, 1, 1, 0, 0, 0]
        events = scores_to_events(times, scores, high=0.6, low=0.35, min_consecutive=2, min_duration=0.2, max_gap=0.2)
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0][0], 0.2)
        self.assertAlmostEqual(events[0][1], 0.4)

    def test_video_metrics(self):
        result = video_level_metrics([0, 1, 1, 0], [0, 1, 0, 0])
        self.assertEqual((result["tp"], result["fn"], result["tn"], result["fp"]), (1, 1, 2, 0))
        self.assertAlmostEqual(result["f1"], 2 / 3)

    def test_event_metrics(self):
        result = event_level_metrics(
            [
                {"video_id": "a", "predicted": [(1.0, 3.0)], "ground_truth": [(1.2, 3.2)]},
                {"video_id": "b", "predicted": [(5.0, 6.0)], "ground_truth": []},
                {"video_id": "c", "predicted": [], "ground_truth": [(1.0, 2.0)]},
            ],
            iou_threshold=0.3,
        )
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
