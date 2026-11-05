#!/usr/bin/env python3
"""Read-only structural checks for the E00-D demo dataset.

Run from the project directory:
    python3 -m unittest demo.tests.test_demo_data -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = DEMO_DIR.parent
DATA_PATH = DEMO_DIR / "data" / "demo.json"
RUN_DIR = PROJECT_DIR / "runs" / "e00-public-demo-005"


@unittest.skipUnless(
    DATA_PATH.is_file(),
    "Generated demo dataset excluded from source import; see docs/SOURCE_IMPORT.md",
)
class DemoDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with DATA_PATH.open("r", encoding="utf-8") as handle:
            cls.data = json.load(handle)
        cls.tracks = cls.data["tracks"]
        cls.by_id = {track["id"]: track for track in cls.tracks}

    def test_counts_match_adopted_run(self) -> None:
        sample = self.data["sample"]
        self.assertEqual(sample["processed_frames"], 300)
        self.assertEqual(sample["track_point_count"], 9599)
        self.assertEqual(sample["track_count"], 82)
        self.assertEqual(len(self.tracks), 82)
        self.assertEqual(sum(track["point_count"] for track in self.tracks), 9599)

    def test_duration_boundary(self) -> None:
        sample = self.data["sample"]
        self.assertEqual(sample["original_duration"], 13.64)
        self.assertEqual(sample["analysis_end"], 12.0)
        self.assertAlmostEqual(sample["tail_seconds"], 1.64, places=2)
        self.assertEqual(self.data["default_track_id"], 7)

    def test_track_points_are_monotonic_and_complete(self) -> None:
        for track in self.tracks:
            points = track["points"]
            self.assertEqual(len(points), track["point_count"])
            self.assertEqual(points[0][0], track["start"])
            self.assertEqual(points[-1][0], track["last"])
            times = [point[0] for point in points]
            self.assertEqual(times, sorted(times))
            for point in points:
                self.assertEqual(len(point), 5)
                self.assertLessEqual(0, point[0])
                self.assertLessEqual(point[0], 12.0)

    def test_no_false_person_claim(self) -> None:
        sample = self.data["sample"]
        self.assertEqual(sample["track_count_label"], "累计轨迹编号")
        self.assertIn("不是独立人数", sample["track_count_note"])
        self.assertNotIn("独立人数", sample["track_count_label"])

    def test_disabled_capabilities_are_explicit(self) -> None:
        labels = {item["label"] for item in self.data["capabilities"]["disabled"]}
        self.assertIn("本页不含规则", labels)
        self.assertIn("视频理解未接入", labels)
        self.assertIn("现场评价未开展", labels)
        self.assertIn("实时与多路未接入", labels)

    def test_rule_demo_entry_is_recorded_without_fake_results(self) -> None:
        rule_demo = self.data["rule_demo"]
        self.assertEqual(rule_demo["url"], "/rules.html")
        self.assertIn("待人工复核", rule_demo["status"])
        self.assertIn("E04", self.data["components"]["event_rules"])
        self.assertEqual(
            self.data["capabilities"]["disabled"][0]["label"], "本页不含规则"
        )

    def test_preview_and_original_are_present(self) -> None:
        self.assertTrue((PROJECT_DIR / "data" / "raw" / "people-walking.mp4").is_file())
        self.assertTrue((RUN_DIR / "preview.jpg").is_file())
        self.assertTrue((DEMO_DIR / "media" / "analysis_replay.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
