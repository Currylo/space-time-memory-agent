import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.a2_adapter import candidate_key  # noqa: E402
from st_memory.unified_platform import (  # noqa: E402
    ClockModel,
    ClockSample,
    alignment_status,
    causal_stay_window,
    clock_segment_anchors,
    evidence_coverage,
    evidence_window,
    event_request_plan,
    fit_clock,
    rule_window,
    source_frame_number,
    to_platform_candidate,
)


CONFIG = {
    "source_sha256": "a" * 64,
    "zone_id": "zone",
    "version": "test-v1",
    "crowd": {"window_seconds": 60.0, "from_below": 3, "to_at_least": 6, "sustain_seconds": 2.0},
    "stay_seconds": 10.0,
    "source_name": "MOT20-05-raw.mp4",
}


def stay():
    return {
        "candidate_id": "stay-1",
        "kind": "stay",
        "track_id": 5,
        "source_sha256": "a" * 64,
        "run_id": "run-1",
        "rule_version": "test-v1",
        "zone_id": "zone",
        "evidence_frame_video_time": 11.2,
        "condition_met_video_time": 11.4,
        "features": {"observed_dwell_seconds": 10.0, "threshold_seconds": 10.0},
    }


def crowd():
    return {
        "candidate_id": "crowd-1",
        "kind": "crowd",
        "track_id": None,
        "source_sha256": "a" * 64,
        "run_id": "run-1",
        "rule_version": "test-v1",
        "zone_id": "zone",
        "evidence_frame_video_time": 93.4,
        "condition_met_video_time": 93.6,
        "features": {"window_start": 33.4, "window_end": 93.4, "from_count": 2, "to_count": 7, "sustain_seconds": 2.0},
    }


class UnifiedPlatformTests(unittest.TestCase):
    def test_stay_mapping_keeps_evidence_and_condition_separate(self):
        mapped = to_platform_candidate(stay(), CONFIG, "run-1", replay_id="r1", loop_index=0)
        self.assertEqual(mapped["rule_id"], "region_stay")
        self.assertEqual(mapped["source_video_time"], 11.2)
        self.assertEqual(mapped["condition_met_video_time"], 11.4)
        self.assertEqual(mapped["evidence_start"], 1.4)
        self.assertEqual(mapped["evidence_end"], 11.4)
        self.assertEqual(mapped["rule_window_start"], 1.4)
        self.assertEqual(mapped["rule_window_end"], 11.4)
        self.assertEqual(mapped["required_coverage_start"], 1.4)
        self.assertEqual(mapped["required_coverage_end"], 11.4)
        self.assertAlmostEqual(mapped["required_coverage_seconds"], 10.0)
        self.assertEqual(mapped["track_id"], 5)
        self.assertIsNone(mapped["score"])
        self.assertEqual(mapped["score_type"], "rule_boolean_not_a_probability")

    def test_crowd_mapping_has_no_person_and_exact_60s_window(self):
        mapped = to_platform_candidate(crowd(), CONFIG, "run-1", replay_id="r1", loop_index=0)
        self.assertEqual(mapped["rule_id"], "count_change_60s")
        self.assertIsNone(mapped["track_id"])
        self.assertEqual(mapped["evidence_start"], 33.4)
        self.assertEqual(mapped["evidence_end"], 93.6)  # adapter identity end
        self.assertEqual(mapped["rule_window_start"], 33.4)
        self.assertEqual(mapped["rule_window_end"], 93.4)  # exact rule window
        self.assertEqual(mapped["required_coverage_end"], 93.6)
        self.assertAlmostEqual(mapped["required_coverage_seconds"], 60.2)
        self.assertAlmostEqual(mapped["comparison_window_seconds"], 60.0)
        self.assertEqual(mapped["features"]["to_at_least"], 6)
        self.assertEqual(mapped["features"]["from_below"], 3)

    def test_rule_window_for_stay_is_the_evidence_window(self):
        self.assertEqual(rule_window(stay()), evidence_window(stay()))
        self.assertEqual(rule_window(crowd()), (33.4, 93.4))

    def test_event_request_plan_has_full_margin(self):
        stay_plan = event_request_plan({"condition_met_video_time": 11.4, "evidence_start": 1.4, "evidence_end": 11.4})
        self.assertEqual(stay_plan["pre_capture"], 12)
        self.assertEqual(stay_plan["duration"], 3)
        self.assertGreaterEqual(stay_plan["lead_before_evidence_seconds"], 1.0)
        crowd_plan = event_request_plan({"condition_met_video_time": 93.6, "evidence_start": 33.4, "evidence_end": 93.6})
        self.assertEqual(crowd_plan["pre_capture"], 63)
        self.assertEqual(crowd_plan["duration"], 3)

    def test_clock_fit_recovers_t0_and_drift(self):
        samples = [
            ClockSample(source_time=t, platform_epoch=1000.0 + 1.000003 * t, kind="recording")
            for t in (0.0, 10.0, 60.0, 120.0)
        ]
        model = fit_clock(samples)
        self.assertAlmostEqual(model.t0, 1000.0, places=5)
        self.assertAlmostEqual(model.drift, 1.000003, places=6)
        self.assertLess(model.max_abs_residual, 1e-6)
        self.assertAlmostEqual(model.source_time(model.platform_epoch(42.0)), 42.0, places=6)

    def test_single_snapshot_does_not_invent_drift(self):
        model = fit_clock([ClockSample(source_time=5.0, platform_epoch=100.0)])
        self.assertEqual(model.drift, 1.0)
        self.assertEqual(model.samples, 1)

    def test_alignment_status_is_not_relaxed(self):
        good = ClockModel(t0=1.0, drift=1.0, samples=3, rmse=0.01, max_abs_residual=0.02)
        self.assertEqual(alignment_status(good)["status"], "verified")
        bad = ClockModel(t0=1.0, drift=1.0, samples=3, rmse=2.0, max_abs_residual=3.0)
        self.assertEqual(alignment_status(bad)["status"], "needs_review")

    def test_evidence_coverage_checks_start_end_and_duration(self):
        good = evidence_coverage(evidence_start=10.0, evidence_end=70.0, clip_source_start=9.0, clip_source_end=71.0, clip_duration=62.0)
        self.assertEqual(good["status"], "verified")
        late = evidence_coverage(evidence_start=10.0, evidence_end=70.0, clip_source_start=13.0, clip_source_end=71.0, clip_duration=58.0)
        self.assertEqual(late["status"], "partial")
        self.assertAlmostEqual(late["missing_head_seconds"], 3.0)
        short = evidence_coverage(evidence_start=10.0, evidence_end=70.0, clip_source_start=9.0, clip_source_end=71.0, clip_duration=30.0)
        self.assertEqual(short["status"], "partial")
        self.assertAlmostEqual(short["duration_short_seconds"], 30.0)

    def test_evidence_coverage_does_not_excuse_04s_missing_head(self):
        result = evidence_coverage(
            evidence_start=10.0, evidence_end=70.0,
            clip_source_start=10.4, clip_source_end=70.4, clip_duration=60.0,
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["problems"], ["clip_starts_0.400s_after_evidence_start"])
        self.assertAlmostEqual(result["covered_seconds"], 59.6)
        self.assertAlmostEqual(result["coverage_ratio"], 0.993333)

    def test_causal_stay_window_excludes_condition_next_frame(self):
        rows = []
        time = 1.4
        dwell = 0.2
        for _ in range(50):
            rows.append({"time": round(time, 6), "people": [{"id": 5, "inside": True, "dwell": round(dwell, 6)}]})
            time += 0.2
            dwell += 0.2
        rows.append({"time": 11.4, "people": [{"id": 5, "inside": True, "dwell": 10.2}]})
        result = causal_stay_window(rows, 5, 11.2, threshold_seconds=10.0, condition_time=11.4)
        self.assertEqual(result["causal_window_start"], 1.4)
        self.assertEqual(result["causal_window_end"], 11.2)
        self.assertAlmostEqual(result["observed_span_seconds"], 10.0)
        self.assertAlmostEqual(result["observed_valid_seconds"], 10.0)
        self.assertAlmostEqual(result["missing_gap_seconds"], 0.0)
        self.assertEqual(result["causal_window_end"], 11.2)
        self.assertEqual(result["required_coverage_end"], 11.4)
        self.assertAlmostEqual(result["required_coverage_seconds"], 10.0)
        self.assertEqual(result["sample_count"], 50)
        self.assertEqual(result["post_trigger_frame"]["time"], 11.4)
        self.assertFalse(result["post_trigger_frame"]["used_in_evidence"])
        self.assertNotIn(11.4, [item["time"] for item in result["samples"]])

    def test_causal_stay_window_handles_missing_frames(self):
        # 50 valid samples spread over 10.6 wall seconds: 3 samples are missing.
        rows = []
        time = 20.6
        dwell = 0.2
        valid = 0
        missing_after = {26.6, 28.2, 29.6}
        while valid < 50:
            if time not in missing_after:
                rows.append({"time": round(time, 6), "people": [{"id": 47, "inside": True, "dwell": round(dwell, 6)}]})
                dwell += 0.2
                valid += 1
            time = round(time + 0.2, 6)
        trigger_time = 31.0
        condition_time = 31.2
        rows.append({"time": condition_time, "people": [{"id": 47, "inside": True, "dwell": 10.2}]})
        result = causal_stay_window(rows, 47, trigger_time, threshold_seconds=10.0, condition_time=condition_time)
        self.assertEqual(result["causal_window_start"], 20.6)
        self.assertEqual(result["causal_window_end"], 31.0)
        self.assertAlmostEqual(result["observed_span_seconds"], 10.6)
        self.assertAlmostEqual(result["observed_valid_seconds"], 10.0)
        self.assertAlmostEqual(result["missing_gap_seconds"], 0.6)
        self.assertEqual(result["causal_window_end"], 31.0)
        self.assertEqual(result["required_coverage_end"], 31.2)
        self.assertAlmostEqual(result["required_coverage_seconds"], 10.6)
        self.assertEqual(result["sample_count"], 50)
        self.assertEqual(result["post_trigger_frame"]["time"], 31.2)

    def test_corrected_stay_fields_change_rule_window_without_touching_identity(self):
        event = stay()
        event["evidence_frame_video_time"] = 13.4
        event["condition_met_video_time"] = 13.6
        event["features"] = {
            "observed_dwell_seconds": 10.0,
            "threshold_seconds": 10.0,
            "causal_window_start": 3.2,
            "causal_window_end": 13.4,
        }
        self.assertEqual(rule_window(event), (3.2, 13.4))
        self.assertEqual(evidence_window(event), (3.2, 13.4))

    def test_coverage_tail_missing_012_seconds_is_partial(self):
        result = evidence_coverage(
            evidence_start=1.4, evidence_end=11.4,
            clip_source_start=1.4, clip_source_end=11.24,
            clip_duration=9.88, clip_fps=25.0,
        )
        self.assertEqual(result["status"], "partial")
        self.assertAlmostEqual(result["missing_tail_seconds"], 0.12)
        self.assertAlmostEqual(result["clip_coverage_end_seconds"], 11.28)
        self.assertAlmostEqual(result["required_seconds"], 10.0)
        self.assertIn("clip_ends_0.120s_before_coverage_end", result["problems"])

    def test_coverage_tail_missing_020_seconds_is_partial(self):
        result = evidence_coverage(
            evidence_start=1.4, evidence_end=11.4,
            clip_source_start=1.4, clip_source_end=11.16,
            clip_duration=9.80, clip_fps=25.0,
        )
        self.assertEqual(result["status"], "partial")
        self.assertAlmostEqual(result["missing_tail_seconds"], 0.20)

    def test_coverage_exactly_to_condition_is_verified_without_next_frame(self):
        result = evidence_coverage(
            evidence_start=1.4, evidence_end=11.4,
            clip_source_start=1.4, clip_source_end=11.36,
            clip_duration=10.0, clip_fps=25.0,
        )
        self.assertEqual(result["status"], "verified", result)
        self.assertAlmostEqual(result["clip_coverage_end_seconds"], 11.40)
        self.assertAlmostEqual(result["missing_tail_seconds"], 0.0)

    def test_clock_segment_anchors_use_first_frame_and_coverage_end(self):
        segment = {
            "segment_id": "test-segment",
            "segment_start_epoch": 1793805307.0,
            "segment_end_epoch": 1793805317.0,
            "first_source_time": 0.0,
            "last_source_time": 9.96,
            "frame_count": 250,
            "db_duration": 10.0,
        }
        anchors = clock_segment_anchors(segment, fps=25.0)
        self.assertEqual(anchors["frame_count"], 250)
        self.assertAlmostEqual(anchors["db_duration"], 10.0)
        self.assertAlmostEqual(anchors["frame_period_seconds"], 0.04)
        self.assertAlmostEqual(anchors["frame_count"] * anchors["frame_period_seconds"], 10.0)
        self.assertAlmostEqual(anchors["t0_from_start"], 1793805307.0)
        self.assertAlmostEqual(anchors["t0_from_last_coverage_end"], 1793805307.0)
        self.assertAlmostEqual(anchors["endpoint_delta_seconds"], 0.0)
        self.assertNotAlmostEqual(anchors["t0_from_last_coverage_end"], 1793805307.04)

    def test_clock_segment_anchors_short_tail(self):
        segment = {
            "segment_id": "tail",
            "segment_start_epoch": 1793805437.0,
            "segment_end_epoch": 1793805439.6,
            "first_source_time": 130.0,
            "last_source_time": 132.56,
        }
        anchors = clock_segment_anchors(segment, fps=25.0)
        self.assertAlmostEqual(anchors["t0_from_start"], 1793805307.0)
        self.assertAlmostEqual(anchors["t0_from_last_coverage_end"], 1793805307.0)

    def test_replay_identity_is_part_of_new_candidate_key(self):
        a = to_platform_candidate(stay(), CONFIG, "run-1", replay_id="replay-a", loop_index=0)
        b = dict(a)
        b["replay_id"] = "replay-b"
        self.assertNotEqual(candidate_key(a), candidate_key(b))

    def test_legacy_candidate_without_replay_id_keeps_its_key_shape(self):
        legacy = {
            "candidate_id": "legacy",
            "source_video_sha256": "a" * 64,
            "camera": "cam",
            "label": "person",
            "rule_id": "long_stay",
            "rule_version": "v1",
            "zone_id": "zone",
            "zone_version": "v1",
            "run_id": "run",
            "loop_index": 9,
            "track_id": 1,
            "source_video_time": 1.0,
            "condition_met_video_time": 2.0,
            "evidence_start": 0.0,
            "evidence_end": 2.0,
            "score": None,
        }
        key = candidate_key(legacy)
        self.assertEqual(len(key), 64)

    def test_source_hash_mismatch_is_rejected(self):
        event = stay()
        event["source_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            to_platform_candidate(event, CONFIG, "run-1", replay_id="r1", loop_index=0)

    def test_burned_in_frame_number_is_one_based(self):
        self.assertEqual(source_frame_number(0.0, 25.0), 1)
        self.assertEqual(source_frame_number(11.2, 25.0), 281)


if __name__ == "__main__":
    unittest.main()
