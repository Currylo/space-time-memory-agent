import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from st_memory.e01_checks import (  # noqa: E402
    Report,
    check_annotations,
    check_splits,
    check_zones,
)


def video(video_id="v1", split="development", group="g1"):
    return {
        "video_id": video_id,
        "camera_id": "c1" if video_id == "v1" else "c1",
        "source_group": group,
        "split": split,
        "duration_seconds": 10.0,
        "frame_count": 100,
        "fps": 10.0,
        "width": 100,
        "height": 100,
        "is_test_set": split == "test",
        "test_role": "not_test" if split != "test" else "unseen_test",
    }


def splits_config(video_ids=None, segments=None, required=None):
    video_ids = ["v1"] if video_ids is None else video_ids
    return {
        "sets": {
            "development": {"status": "available", "video_ids": video_ids},
            "validation": {"status": "missing_data", "video_ids": []},
            "test": {"status": "not_established", "video_ids": []},
        },
        "required_development_ids": ["v1"] if required is None else required,
        "segments": segments
        or [
            {
                "segment_id": "s1",
                "video_id": "v1",
                "source_group": "g1",
                "start": 0.0,
                "end": 10.0,
                "split": "development",
                "purpose": "test",
            }
        ],
    }


def zones_config(polygon=None):
    return {
        "coordinate_system": "image_pixels_xy_origin_top_left",
        "cameras": [
            {
                "camera_id": "c1",
                "video_id": "v1",
                "frame_size": [100, 100],
                "version": "z1",
                "purpose": "public_demo_only",
                "is_field_business_zone": False,
                "camera_motion": "assumed_static_unverified",
                "zones": [
                    {
                        "zone_id": "z1",
                        "polygon": polygon or [[0, 0], [90, 0], [90, 90], [0, 90]],
                        "coordinate_system": "image_pixels_xy_origin_top_left",
                        "purpose": "public_demo_overlay_only",
                        "business_zone": False,
                        "version": "z1",
                    }
                ],
            }
        ],
    }


def annotation(status="synthetic_example", is_gt=False, review="not_reviewed_example"):
    return {
        "__file": "examples/test.jsonl",
        "row": {
            "schema_version": "e01-track-v1",
            "annotation_id": "A1",
            "video_id": "v1",
            "segment_id": "s1",
            "camera_id": "c1",
            "source_group": "g1",
            "split": "development",
            "gt_track_id": "GT1",
            "frame_index": 1,
            "video_time": 0.1,
            "bbox_xyxy": [1, 1, 10, 10],
            "occlusion": "none",
            "visibility": "visible",
            "annotation_status": status,
            "review_status": review,
            "is_ground_truth": is_gt,
            "reviewer_ids": ["r1", "r2"] if is_gt else [],
            "review_notes": "reviewed" if is_gt else "",
        },
    }


class E01ChecksTest(unittest.TestCase):
    def codes(self, report):
        return {item["code"] for item in report.checks}

    def test_source_group_cross_set_is_error(self):
        videos = [video("v1", "development", "g1"), video("v2", "test", "g1")]
        splits = splits_config(video_ids=["v1"], required=["v1"])
        splits["sets"]["test"] = {"status": "available", "video_ids": ["v2"]}
        report = Report()
        check_splits(videos, splits, report)
        self.assertIn("split_source_group_cross_set", self.codes(report))
        self.assertTrue(report.errors)

    def test_overlapping_window_cross_set_is_error(self):
        videos = [video("v1", "development", "g1")]
        segments = [
            {"segment_id": "a", "video_id": "v1", "source_group": "g1", "start": 0, "end": 6, "split": "development", "purpose": "dev"},
            {"segment_id": "b", "video_id": "v1", "source_group": "g1", "start": 5, "end": 9, "split": "test", "purpose": "test"},
        ]
        splits = splits_config(segments=segments)
        report = Report()
        check_splits(videos, splits, report)
        self.assertIn("split_overlap_cross_set", self.codes(report))
        self.assertIn("split_segment_manifest_mismatch", self.codes(report))

    def test_duplicate_window_same_set_warns(self):
        videos = [video("v1")]
        segments = [
            {"segment_id": "a", "video_id": "v1", "source_group": "g1", "start": 0, "end": 5, "split": "development", "purpose": "dev"},
            {"segment_id": "b", "video_id": "v1", "source_group": "g1", "start": 0, "end": 5, "split": "development", "purpose": "dev"},
        ]
        report = Report()
        check_splits(videos, splits_config(segments=segments), report)
        self.assertIn("split_duplicate_window_same_set", self.codes(report))

    def test_required_public_sample_must_be_development(self):
        videos = [video("v1", "validation", "g1")]
        splits = splits_config(video_ids=[], required=["v1"])
        report = Report()
        check_splits(videos, splits, report)
        self.assertIn("split_required_not_development", self.codes(report))

    def test_model_prediction_cannot_be_ground_truth(self):
        report = Report()
        check_annotations(
            [video("v1")],
            splits_config()["segments"],
            zones_config(),
            [annotation(status="model_prediction", is_gt=True, review="machine_only")],
            report,
        )
        self.assertIn("annotation_model_as_gt", self.codes(report))
        self.assertIn("annotation_gt_status", self.codes(report))

    def test_synthetic_example_is_not_gt_and_is_allowed(self):
        report = Report()
        check_annotations(
            [video("v1")],
            splits_config()["segments"],
            zones_config(),
            [annotation()],
            report,
        )
        self.assertNotIn("annotation_synthetic_as_gt", self.codes(report))
        self.assertFalse(report.errors)

    def test_zone_out_of_frame_is_error(self):
        report = Report()
        check_zones(
            [video("v1")],
            zones_config(polygon=[[0, 0], [120, 0], [120, 90], [0, 90]]),
            report,
        )
        self.assertIn("zones_polygon_out_of_frame", self.codes(report))


if __name__ == "__main__":
    unittest.main()
