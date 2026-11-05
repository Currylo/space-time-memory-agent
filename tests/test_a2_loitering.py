import math
import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.a2_loitering import (  # noqa: E402
    Trajectory,
    _bbox_area,
    _hull_area,
    _self_intersections,
    extract_features,
)


def make_trajectory(points, label=0, track_id=1):
    points = np.asarray(points, dtype=float)
    n = len(points)
    frames = np.arange(1, n + 1, dtype=int)
    return Trajectory(
        track_id=track_id,
        folder=20200101,
        clip="clip_0_0000",
        label=label,
        frames=frames,
        x=points[:, 0],
        y=points[:, 1],
        width=np.full(n, 10.0),
        height=np.full(n, 20.0),
        occlusion=np.zeros(n),
    )


class A2LoiteringFeatureTests(unittest.TestCase):
    def test_straight_line_has_high_path_efficiency(self):
        points = [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)]
        features = extract_features(make_trajectory(points))
        self.assertAlmostEqual(features["path_efficiency"], 1.0, places=6)
        self.assertEqual(features["hull_area_px2"], 0.0)
        self.assertEqual(features["bbox_area_px2"], 0.0)  # a horizontal line has zero height
        self.assertEqual(features["reversals_120deg"], 0)

    def test_back_and_forth_has_low_path_efficiency(self):
        points = [(0, 0), (20, 0), (40, 0), (20, 0), (0, 0), (20, 0), (40, 0)]
        features = extract_features(make_trajectory(points))
        self.assertLess(features["path_efficiency"], 0.5)
        self.assertGreaterEqual(features["reversals_120deg"], 2)

    def test_stationary_is_no_motion(self):
        points = [(50, 50)] * 70
        features = extract_features(make_trajectory(points))
        self.assertEqual(features["no_motion_long"], 1)
        self.assertEqual(features["no_motion_short_60s"], 1)
        self.assertEqual(features["hull_area_px2"], 0.0)
        self.assertGreater(features["duration_seconds"], 60)

    def test_bbox_and_hull_areas(self):
        square = np.array([(0, 0), (10, 0), (10, 10), (0, 10)], dtype=float)
        self.assertEqual(_bbox_area(square), 100.0)
        self.assertAlmostEqual(_hull_area(square), 100.0, places=6)

    def test_self_intersection_count_on_bowtie(self):
        bowtie = np.array([(0, 0), (10, 10), (10, 0), (0, 10)], dtype=float)
        self.assertEqual(_self_intersections(bowtie), 1)

    def test_features_are_finite_for_degenerate_points(self):
        points = [(5, 5), (5, 5), (5, 5), (5, 5), (5, 5)]
        features = extract_features(make_trajectory(points))
        for key, value in features.items():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value), key)


if __name__ == "__main__":
    unittest.main()
