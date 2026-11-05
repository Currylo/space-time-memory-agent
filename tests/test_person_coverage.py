import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.person_coverage import suppress_duplicates, tile_windows


class PersonCoverageTests(unittest.TestCase):
    def test_tiles_cover_source_and_overlap_without_extending_image(self):
        windows = tile_windows(826, 540)
        self.assertEqual(windows[0][:2], (0, 0))
        self.assertEqual(windows[-1][2:], (826, 540))
        self.assertGreater(windows[0][2], windows[1][0])
        self.assertGreater(windows[0][3], windows[2][1])
        self.assertTrue(all(0 <= a < c <= 826 and 0 <= b < d <= 540 for a,b,c,d in windows))

    def test_global_nms_removes_duplicate_but_keeps_adjacent_person(self):
        boxes = [[10,10,30,80], [11,10,31,80], [27,10,47,80]]
        self.assertEqual(suppress_duplicates(boxes, [.9,.8,.7]), [0,2])

    def test_empty_observation_does_not_create_boxes(self):
        self.assertEqual(suppress_duplicates([], []), [])

    def test_invalid_tile_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            tile_windows(826, 540, 1.)
