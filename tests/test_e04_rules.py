#!/usr/bin/env python3
"""E04 rule tests: real-sample regression plus synthetic boundary cases.

Run from the project directory:
    python3 -m unittest tests.test_e04_rules -v

The synthetic tracks are used only to test rule boundaries (threshold, gap,
stationary, reversal and causality). They are never presented as real events.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.e04_rules import (  # noqa: E402
    Observation,
    RunData,
    Zone,
    attach_zone_assignments,
    build_zone_segments,
    build_zone_stats,
    candidate_trigger_signature,
    evaluate_occupancy_change,
    evaluate_rules,
    load_json,
    load_run,
    load_zones,
    prefix_run,
    with_zone_ids,
)

RUN_DIR = PROJECT_DIR / "runs" / "e00-public-demo-005"
ZONES_PATH = PROJECT_DIR / "data" / "e01" / "zones" / "zones.json"
ASSIGNMENTS_PATH = PROJECT_DIR / "data" / "e03" / "zone_assignments.jsonl"
CONFIG_PATH = PROJECT_DIR / "configs" / "e04_demo_rules.json"


def base_config() -> dict:
    return copy.deepcopy(load_json(CONFIG_PATH))


def synthetic_zone(
    zone_id: str = "zone_a",
    polygon: tuple[tuple[float, float], ...] | None = None,
) -> Zone:
    return Zone(
        zone_id=zone_id,
        name="合成测试区域",
        polygon=polygon or ((0, 0), (1000, 0), (1000, 1000), (0, 1000)),
        version="synthetic-zone-v1",
        coordinate_system="image_pixels_xy_origin_top_left",
        purpose="rule_boundary_test_only",
        business_zone=False,
        enabled=True,
    )


def make_observation(
    track_id: int, frame_index: int, x: float, y: float, video_time: float
) -> Observation:
    return Observation(
        track_id=track_id,
        frame_index=frame_index,
        video_time=video_time,
        bbox=(x - 12.0, y - 60.0, x + 12.0, y),
        x=x,
        y=y,
        zone_ids=(),
        score=0.9,
    )


def make_run(
    tracks: dict[int, list[tuple[float, float, float]]],
    fps: float = 25.0,
    analysis_end: float | None = None,
    source_video: Path | None = None,
) -> RunData:
    """Build a synthetic RunData.

    ``tracks`` maps track_id to a list of (video_time, x, y); frame indices are
    assigned per track in order, matching the real run's per-track frame grid.
    """

    points: list[Observation] = []
    for track_id, rows in tracks.items():
        for frame_index, (video_time, x, y) in enumerate(rows):
            points.append(
                make_observation(track_id, frame_index, x, y, video_time)
            )
    times = sorted({point.video_time for point in points})
    frames = [
        {
            "frame_index": index,
            "video_time": time,
            "detected_count": 1,
            "tracked_count": 1,
        }
        for index, time in enumerate(times)
    ]
    points.sort(key=lambda item: (item.frame_index, item.track_id))
    points_by_track: dict[int, list[Observation]] = {}
    for point in points:
        points_by_track.setdefault(point.track_id, []).append(point)
    for track_points in points_by_track.values():
        track_points.sort(key=lambda item: item.frame_index)
    end = analysis_end if analysis_end is not None else max(times)
    return RunData(
        run_dir=Path("/tmp/synthetic-e04"),
        run={
            "run_id": "synthetic-e04-test",
            "status": "completed",
            "processed_video_seconds": end,
            "source": {"site_id": "synthetic", "path": str(source_video or "/tmp/x.mp4")},
        },
        manifest={
            "video_id": "synthetic",
            "camera_id": "synthetic_cam",
            "fps": fps,
            "duration_seconds": end,
            "width": 1000,
            "height": 1000,
        },
        frames=frames,
        summaries=[],
        points=points,
        points_by_track=points_by_track,
        fps=fps,
        analysis_start=0.0,
        analysis_end=end,
        source_video=source_video or Path("/tmp/x.mp4"),
    )


def line_track(
    start_x: float, end_x: float, y: float, start_time: float, seconds: float, fps: float = 25.0
) -> list[tuple[float, float, float]]:
    count = int(round(seconds * fps))
    rows = []
    for index in range(count):
        ratio = index / max(1, count - 1)
        rows.append(
            (
                round(start_time + index / fps, 4),
                start_x + (end_x - start_x) * ratio,
                y,
            )
        )
    return rows


def same_line_legs(
    leg_lengths: list[float],
    leg_seconds: float = 1.6,
    start_x: float = 100.0,
    y: float = 500.0,
) -> list[tuple[float, float, float]]:
    """Build a constant-speed same-line path with a turn at every leg end."""

    rows: list[tuple[float, float, float]] = []
    x = start_x
    direction = 1
    for index, length in enumerate(leg_lengths):
        segment = line_track(
            x, x + direction * length, y, index * leg_seconds, leg_seconds
        )
        rows.extend(segment[:-1] if index < len(leg_lengths) - 1 else segment)
        x += direction * length
        direction *= -1
    return [
        (round(index / 25.0, 4), row[1], row[2]) for index, row in enumerate(rows)
    ]


def assignments_for(
    run: RunData, zone_id: str, predicate=None
) -> dict[tuple[int, int], tuple[str, ...]]:
    result = {}
    for point in run.points:
        if predicate is None or predicate(point):
            result[(point.track_id, point.frame_index)] = (zone_id,)
    return result


def candidate_set_signatures(
    candidates: list[dict], cutoff: float | None = None
) -> list[dict]:
    """All candidate trigger signatures up to a cutoff, including absence.

    Comparing this full set catches both changed trigger features and missing /
    extra candidates; comparing only candidates that happened to be present in
    both runs would hide truncation errors.
    """

    selected = [
        candidate
        for candidate in candidates
        if cutoff is None
        or float(candidate["condition_met_video_time"]) <= cutoff + 1e-9
    ]
    return sorted(
        (candidate_trigger_signature(candidate) for candidate in selected),
        key=lambda item: item["candidate_id"],
    )


@unittest.skipUnless(
    all(path.is_file() for path in (
        RUN_DIR / "run.json", RUN_DIR / "manifest.json", RUN_DIR / "tracks.jsonl",
        RUN_DIR / "frames.jsonl", RUN_DIR / "trace_summaries.jsonl",
        ZONES_PATH, ASSIGNMENTS_PATH,
    )),
    "E04 integration assets excluded from source import; see docs/SOURCE_IMPORT.md",
)
class RealSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.run_data = load_run(RUN_DIR)
        cls.zones, _ = load_zones(ZONES_PATH, cls.run_data.manifest["camera_id"])
        cls.assignments, cls.assignment_details = attach_zone_assignments(
            cls.run_data, ASSIGNMENTS_PATH, cls.zones
        )
        cls.config = base_config()
        cls.result = evaluate_rules(
            cls.run_data, cls.zones, cls.assignments, cls.config
        )
        cls.candidates = cls.result["candidates"]

    def test_real_input_counts(self) -> None:
        self.assertEqual(len(self.run_data.points), 9599)
        self.assertEqual(len(self.run_data.frames), 300)
        self.assertEqual(len(self.run_data.points_by_track), 82)
        self.assertEqual(self.assignment_details["assignment_rows"], 6942)
        self.assertEqual(self.assignment_details["points_with_zone"], 6942)
        self.assertEqual(
            self.assignment_details["per_zone"],
            {
                "demo_central_walkway": 3524,
                "demo_left_walkway": 1302,
                "demo_right_walkway": 2116,
            },
        )

    def test_zone_stats_have_one_value_per_frame(self) -> None:
        stats = self.result["zone_stats"]
        self.assertEqual(len(stats["times"]), 300)
        for zone in stats["zones"]:
            self.assertEqual(len(zone["counts"]), 300)
            self.assertEqual(len(zone["max_continuous_dwell_seconds"]), 300)
            self.assertEqual(zone["peak_count"], max(zone["counts"]))

    def test_real_long_stay_candidates_are_deduplicated(self) -> None:
        # Do not treat the old "6 candidates" as the target.  Assert internal
        # consistency; the actual v2 count and times are recorded in the report.
        long_stay_status = next(
            item for item in self.result["rules"] if item["rule_id"] == "long_stay"
        )
        long_stay = [item for item in self.candidates if item["rule_id"] == "long_stay"]
        self.assertEqual(len(long_stay), long_stay_status["candidate_count"])
        self.assertEqual(self.result["candidate_count"], len(self.candidates))
        keys = [item["dedup_key"] for item in self.candidates]
        self.assertEqual(len(keys), len(set(keys)))
        for item in long_stay:
            self.assertGreaterEqual(
                item["trigger_features"]["dwell_observed_seconds"],
                item["thresholds"]["min_continuous_observed_dwell_seconds"] - 1e-9,
            )
            self.assertEqual(item["status_label"], "规则条件触发，待人工复核")
            self.assertGreaterEqual(
                item["condition_met_video_time"],
                item["evidence_frame_video_time"],
            )

    def test_trigger_is_first_frame_at_or_above_threshold(self) -> None:
        track_points = {point.frame_index: point for point in self.run_data.points if point.track_id == 12}
        candidate = next(
            item
            for item in self.candidates
            if item["rule_id"] == "long_stay" and item["track_id"] == 12
        )
        trigger_frame = candidate["condition_met_frame_index"]
        before = track_points.get(trigger_frame - 1)
        self.assertIsNotNone(before)
        # 250 observed frames at 25 fps = 10.00 s, and the previous frame is 9.96 s.
        self.assertEqual(candidate["trigger_features"]["observed_points"], 250)
        self.assertAlmostEqual(candidate["trigger_features"]["dwell_observed_seconds"], 10.0)
        self.assertAlmostEqual(candidate["evidence_frame_video_time"], 9.96)
        self.assertAlmostEqual(candidate["condition_met_video_time"], 10.0)

    def test_real_reversal_zero_is_honest(self) -> None:
        reversal = next(item for item in self.result["rules"] if item["rule_id"] == "reversal")
        self.assertEqual(reversal["evaluation_status"], "evaluated")
        self.assertEqual(reversal["candidate_count"], 0)
        self.assertEqual(self.candidates, [item for item in self.candidates if item["rule_id"] != "reversal"])
        self.assertTrue(reversal["near_misses"])
        self.assertEqual(reversal["near_misses"][0]["track_id"], 77)
        self.assertEqual(reversal["near_misses"][0]["max_effective_reversals"], 1)

    def test_real_occupancy_change_is_not_evaluated(self) -> None:
        occupancy = next(
            item for item in self.result["rules"] if item["rule_id"] == "occupancy_change_60s"
        )
        self.assertEqual(occupancy["evaluation_status"], "insufficient_observation")
        self.assertEqual(occupancy["candidate_count"], 0)
        self.assertEqual(occupancy["required_seconds"], 60.0)
        self.assertAlmostEqual(occupancy["observed_seconds"], 12.0)
        self.assertIn("没有缩短窗口", occupancy["zero_candidate_reason"])

    def test_candidate_evidence_matches_source_point(self) -> None:
        point_by_key = {
            (point.track_id, point.frame_index): point for point in self.run_data.points
        }
        for candidate in self.candidates:
            if candidate["track_id"] is None:
                continue
            key = (candidate["track_id"], candidate["condition_met_frame_index"])
            point = point_by_key[key]
            self.assertAlmostEqual(candidate["evidence"]["video_time"], point.video_time, places=3)
            for actual, expected in zip(candidate["evidence"]["bbox"], point.bbox):
                self.assertAlmostEqual(actual, expected, places=3)
            self.assertEqual(candidate["evidence"]["video_path"], str(self.run_data.source_video))

    def test_rule_config_hash_is_recorded(self) -> None:
        hashes = {item["rule_config_sha256"] for item in self.candidates}
        self.assertEqual(len(hashes), 1)
        self.assertEqual(hashes.pop(), self.result["inputs"]["rule_config_sha256"])

    def test_prefix_truncation_keeps_the_full_past_candidate_set(self) -> None:
        for cutoff in (6.0, 8.0, 9.0, 10.0, 10.12, 10.5, 11.0, 11.28, 11.5):
            prefix = prefix_run(self.run_data, cutoff)
            result = evaluate_rules(prefix, self.zones, self.assignments, self.config)
            self.assertEqual(
                candidate_set_signatures(result["candidates"]),
                candidate_set_signatures(self.candidates, cutoff=cutoff),
                f"cutoff={cutoff} 的过去候选集合不一致",
            )
            # A truncated input cannot contain a trigger in the future.
            for candidate in result["candidates"]:
                self.assertLessEqual(
                    candidate["condition_met_video_time"], cutoff + 1e-9
                )

    def test_rule_toggles_are_independent(self) -> None:
        disabled_long = base_config()
        next(
            item for item in disabled_long["rules"] if item["rule_id"] == "long_stay"
        )["enabled"] = False
        result_long = evaluate_rules(
            self.run_data, self.zones, self.assignments, disabled_long
        )
        long_status = next(item for item in result_long["rules"] if item["rule_id"] == "long_stay")
        self.assertEqual(long_status["evaluation_status"], "disabled")
        self.assertEqual(long_status["candidate_count"], 0)
        self.assertEqual(
            len([item for item in result_long["candidates"] if item["rule_id"] == "long_stay"]),
            0,
        )
        # Reversal remains evaluated and still yields zero; occupancy still reports insufficient data.
        reversal_status = next(
            item for item in result_long["rules"] if item["rule_id"] == "reversal"
        )
        occupancy_status = next(
            item for item in result_long["rules"] if item["rule_id"] == "occupancy_change_60s"
        )
        self.assertEqual(reversal_status["evaluation_status"], "evaluated")
        self.assertEqual(occupancy_status["evaluation_status"], "insufficient_observation")

        disabled_reversal = base_config()
        next(
            item for item in disabled_reversal["rules"] if item["rule_id"] == "reversal"
        )["enabled"] = False
        result_reversal = evaluate_rules(
            self.run_data, self.zones, self.assignments, disabled_reversal
        )
        reversal_status = next(
            item for item in result_reversal["rules"] if item["rule_id"] == "reversal"
        )
        self.assertEqual(reversal_status["evaluation_status"], "disabled")
        self.assertEqual(len(result_reversal["candidates"]), len(self.candidates))


class SyntheticDwellTests(unittest.TestCase):
    def config_for_threshold(self, threshold: float) -> dict:
        config = base_config()
        rule = next(item for item in config["rules"] if item["rule_id"] == "long_stay")
        rule["params"]["min_continuous_observed_dwell_seconds"] = threshold
        rule["params"]["min_observed_points"] = 10
        return config

    def evaluate_single_track(self, rows, threshold: float, zone: Zone | None = None):
        zone = zone or synthetic_zone()
        run = make_run({1: rows}, analysis_end=rows[-1][0] + 0.04)
        assignments = assignments_for(run, zone.zone_id)
        return evaluate_rules(run, [zone], assignments, self.config_for_threshold(threshold))

    def test_exact_threshold_triggers_and_previous_frame_does_not(self) -> None:
        rows = line_track(100, 900, 500, 0.0, 10.0)  # exactly 250 frames = 10.0 s
        result = self.evaluate_single_track(rows, 10.0)
        long_stay = [item for item in result["candidates"] if item["rule_id"] == "long_stay"]
        self.assertEqual(len(long_stay), 1)
        candidate = long_stay[0]
        self.assertEqual(candidate["trigger_features"]["observed_points"], 250)
        self.assertAlmostEqual(candidate["evidence_frame_video_time"], 9.96)
        self.assertAlmostEqual(candidate["condition_met_video_time"], 10.0)
        self.assertAlmostEqual(candidate["frame_coverage_end"], 10.0)
        self.assertEqual(candidate["condition_met_frame_index"], 249)

        shorter = line_track(100, 900, 500, 0.0, 9.96)  # 249 frames
        result_short = self.evaluate_single_track(shorter, 10.0)
        self.assertEqual(
            len([item for item in result_short["candidates"] if item["rule_id"] == "long_stay"]),
            0,
        )

    def test_nonzero_start_uses_frame_coverage_end(self) -> None:
        rows = line_track(100, 900, 500, 1.0, 10.0)
        result = self.evaluate_single_track(rows, 10.0)
        candidate = next(
            item for item in result["candidates"] if item["rule_id"] == "long_stay"
        )
        self.assertAlmostEqual(candidate["evidence_frame_video_time"], 10.96)
        self.assertAlmostEqual(candidate["frame_coverage_start"], 10.96)
        self.assertAlmostEqual(candidate["frame_coverage_end"], 11.0)
        self.assertAlmostEqual(candidate["condition_met_video_time"], 11.0)

    def test_missing_frames_accumulate_observed_time_not_elapsed_time(self) -> None:
        first = line_track(100, 900, 500, 0.0, 5.0)
        second = line_track(100, 900, 500, 5.2, 5.0)
        rows = first + second
        result = self.evaluate_single_track(rows, 10.0)
        candidate = next(
            item for item in result["candidates"] if item["rule_id"] == "long_stay"
        )
        features = candidate["trigger_features"]
        self.assertEqual(features["observed_points"], 250)
        self.assertAlmostEqual(features["dwell_observed_seconds"], 10.0)
        self.assertAlmostEqual(features["coverage_elapsed_seconds"], 10.2)
        self.assertAlmostEqual(features["gap_seconds"], 0.2)
        self.assertAlmostEqual(features["max_gap_seconds"], 0.24)
        self.assertEqual(features["gap_count"], 1)
        self.assertAlmostEqual(candidate["evidence_frame_video_time"], 10.16)
        self.assertAlmostEqual(candidate["condition_met_video_time"], 10.2)

    def test_window_end_and_short_window_boundary(self) -> None:
        rows = line_track(100, 900, 500, 0.0, 10.0)
        zone = synthetic_zone()
        run = make_run({1: rows}, analysis_end=10.0)
        result = evaluate_rules(
            run,
            [zone],
            assignments_for(run, zone.zone_id),
            self.config_for_threshold(10.0),
        )
        candidate = next(
            item for item in result["candidates"] if item["rule_id"] == "long_stay"
        )
        self.assertAlmostEqual(candidate["condition_met_video_time"], 10.0)
        self.assertAlmostEqual(candidate["frame_coverage_end"], 10.0)

        short_rows = line_track(100, 900, 500, 0.0, 9.96)
        short_run = make_run({1: short_rows}, analysis_end=10.0)
        short_result = evaluate_rules(
            short_run,
            [zone],
            assignments_for(short_run, zone.zone_id),
            self.config_for_threshold(10.0),
        )
        self.assertEqual(
            [
                item
                for item in short_result["candidates"]
                if item["rule_id"] == "long_stay"
            ],
            [],
        )

    def test_trigger_then_exit_does_not_change_trigger_features(self) -> None:
        zone = synthetic_zone()
        in_zone = line_track(100, 900, 500, 0.0, 10.0)
        outside = line_track(100, 900, 500, 10.04, 0.4)
        rows = in_zone + outside
        run = make_run({1: rows}, analysis_end=10.5)
        assignments = {
            (point.track_id, point.frame_index): (zone.zone_id,)
            for point in run.points
            if point.video_time <= 9.96 + 1e-9
        }
        config = self.config_for_threshold(10.0)
        full = evaluate_rules(run, [zone], assignments, config)
        cutoff = 10.0
        prefix = prefix_run(run, cutoff)
        truncated = evaluate_rules(prefix, [zone], assignments, config)
        full_candidate = next(
            item for item in full["candidates"] if item["rule_id"] == "long_stay"
        )
        prefix_candidate = next(
            item for item in truncated["candidates"] if item["rule_id"] == "long_stay"
        )
        self.assertEqual(
            candidate_set_signatures(truncated["candidates"]),
            candidate_set_signatures(full["candidates"], cutoff=cutoff),
        )
        self.assertEqual(
            full_candidate["trigger_features"], prefix_candidate["trigger_features"]
        )
        self.assertEqual(full_candidate["lifecycle"]["end_reason"], "left_zone")
        self.assertEqual(prefix_candidate["lifecycle"]["end_reason"], "analysis_window_end")
        self.assertIn("end_reason", full_candidate["final_features"])
        self.assertEqual(full_candidate["final_features"]["end_reason"], "left_zone")

    def test_trigger_then_gap_or_boundary_excursion_stays_out_of_trigger_features(self) -> None:
        zone = synthetic_zone()
        in_zone = line_track(100, 900, 500, 0.0, 10.0)
        post_trigger = line_track(100, 900, 500, 10.0, 0.2)
        outside = line_track(100, 900, 500, 10.2, 0.12)
        after = line_track(100, 900, 500, 10.32, 0.4)
        rows = in_zone + post_trigger + outside + after
        run = make_run({1: rows}, analysis_end=11.0)
        assignments = {
            (point.track_id, point.frame_index): (zone.zone_id,)
            for point in run.points
            if point.video_time <= 9.96 + 1e-9
            or (10.0 - 1e-9 <= point.video_time <= 10.16 + 1e-9)
            or point.video_time >= 10.32 - 1e-9
        }
        config = self.config_for_threshold(10.0)
        full = evaluate_rules(run, [zone], assignments, config)
        cutoff = 10.0
        prefix = prefix_run(run, cutoff)
        truncated = evaluate_rules(prefix, [zone], assignments, config)
        full_candidate = next(
            item for item in full["candidates"] if item["rule_id"] == "long_stay"
        )
        prefix_candidate = next(
            item for item in truncated["candidates"] if item["rule_id"] == "long_stay"
        )
        self.assertEqual(
            candidate_set_signatures(truncated["candidates"]),
            candidate_set_signatures(full["candidates"], cutoff=cutoff),
        )
        self.assertEqual(
            full_candidate["trigger_features"], prefix_candidate["trigger_features"]
        )
        self.assertEqual(full_candidate["trigger_features"]["boundary_excursions"], 0)
        self.assertEqual(full_candidate["final_features"]["boundary_excursions"], 1)
        self.assertGreaterEqual(full_candidate["final_features"]["gap_count"], 0)
        self.assertIn(
            full_candidate["lifecycle"]["end_reason"],
            {"observation_gap", "track_lost_unconfirmed", "analysis_window_end"},
        )

    def test_long_gap_splits_dwell_and_never_bridges(self) -> None:
        first = line_track(100, 500, 500, 0.0, 6.0)
        second = line_track(100, 500, 500, 6.6, 6.0)
        rows = first + second
        result = self.evaluate_single_track(rows, 10.0)
        self.assertEqual(
            len([item for item in result["candidates"] if item["rule_id"] == "long_stay"]),
            0,
        )
        segments = [item for item in result["segments"] if item["zone_id"] == "zone_a"]
        self.assertEqual(len(segments), 2)
        self.assertTrue(all(item["max_gap_seconds"] <= 0.4 + 1e-9 for item in segments))

    def test_short_boundary_excursion_is_tolerated(self) -> None:
        zone = synthetic_zone()
        rows = line_track(100, 900, 500, 0.0, 8.0)
        run = make_run({1: rows}, analysis_end=8.0)
        # Remove three consecutive frames from the zone (0.12 s), then continue.
        assignments = {}
        for point in run.points:
            if 100 <= point.frame_index < 103:
                continue
            assignments[(point.track_id, point.frame_index)] = (zone.zone_id,)
        result = evaluate_rules(
            run, [zone], assignments, self.config_for_threshold(7.0)
        )
        long_stay = [item for item in result["candidates"] if item["rule_id"] == "long_stay"]
        self.assertEqual(len(long_stay), 1)
        self.assertGreaterEqual(
            long_stay[0]["trigger_features"]["dwell_observed_seconds"], 7.0 - 1e-9
        )
        segments = [item for item in result["segments"] if item["zone_id"] == zone.zone_id]
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["boundary_excursions"], 1)

    def test_long_outside_run_splits_the_episode(self) -> None:
        zone = synthetic_zone()
        rows = line_track(100, 900, 500, 0.0, 8.0)
        run = make_run({1: rows}, analysis_end=8.0)
        assignments = {}
        for point in run.points:
            if 100 <= point.frame_index < 115:  # 0.6 s outside > 0.24 s tolerance
                continue
            assignments[(point.track_id, point.frame_index)] = (zone.zone_id,)
        result = evaluate_rules(
            run, [zone], assignments, self.config_for_threshold(5.0)
        )
        long_stay = [item for item in result["candidates"] if item["rule_id"] == "long_stay"]
        self.assertEqual(long_stay, [])
        segments = [item for item in result["segments"] if item["zone_id"] == zone.zone_id]
        self.assertEqual(len(segments), 2)

    def test_future_track_does_not_change_past_candidate(self) -> None:
        zone = synthetic_zone()
        # Track 1 satisfies a 2-second dwell very early.
        early_rows = line_track(100, 300, 500, 0.0, 3.0)
        early_run = make_run({1: early_rows}, analysis_end=3.0)
        early_assignments = assignments_for(early_run, zone.zone_id)
        config = self.config_for_threshold(2.0)
        early_result = evaluate_rules(early_run, [zone], early_assignments, config)
        early_candidates = [
            item for item in early_result["candidates"] if item["rule_id"] == "long_stay"
        ]
        self.assertEqual(len(early_candidates), 1)
        signature = candidate_trigger_signature(early_candidates[0])

        # Append a future track that is also in the zone.  It must not alter the
        # earlier trigger or create a past trigger retroactively.
        future_rows = line_track(500, 900, 500, 3.0, 5.0)
        combined_run = make_run({1: early_rows, 2: future_rows}, analysis_end=8.0)
        combined_assignments = assignments_for(combined_run, zone.zone_id)
        combined_result = evaluate_rules(
            combined_run, [zone], combined_assignments, config
        )
        combined_candidates = [
            item for item in combined_result["candidates"] if item["rule_id"] == "long_stay"
        ]
        self.assertEqual(len(combined_candidates), 2)
        first = next(item for item in combined_candidates if item["track_id"] == 1)
        self.assertEqual(candidate_trigger_signature(first), signature)
        second = next(item for item in combined_candidates if item["track_id"] == 2)
        self.assertGreaterEqual(second["condition_met_video_time"], 3.0)


class SyntheticReversalTests(unittest.TestCase):
    def reversal_config(self) -> dict:
        config = base_config()
        rule = next(item for item in config["rules"] if item["rule_id"] == "reversal")
        rule["params"].update(
            {
                "window_seconds": 8.0,
                "min_history_seconds": 4.0,
                "min_observed_points": 20,
                "min_leg_displacement_px": 80.0,
                "min_leg_duration_seconds": 0.3,
                "min_turn_angle_degrees": 120.0,
                "min_effective_reversals": 3,
                "max_path_efficiency": 0.3,
                "min_path_length_px": 160.0,
                "min_valid_motion_length_px": 160.0,
                "collinearity_tolerance_px": 12.0,
            }
        )
        return config

    def test_three_effective_reversals_trigger(self) -> None:
        # Five 1.6-second legs: right, left, right, left, right.
        rows = []
        x = 100.0
        direction = 1
        for leg in range(5):
            segment = line_track(x, x + direction * 100, 500, leg * 1.6, 1.6)
            rows.extend(segment[:-1] if leg < 4 else segment)
            x += direction * 100
            direction *= -1
        # Normalize times after dropping duplicated endpoints.
        rows = [(round(index / 25.0, 4), row[1], row[2]) for index, row in enumerate(rows)]
        run = make_run({1: rows}, analysis_end=rows[-1][0] + 0.04)
        zone = synthetic_zone()
        assignments = assignments_for(run, zone.zone_id)
        result = evaluate_rules(run, [zone], assignments, self.reversal_config())
        reversal = [item for item in result["candidates"] if item["rule_id"] == "reversal"]
        self.assertEqual(len(reversal), 1, reversal)
        features = reversal[0]["trigger_features"]
        self.assertGreaterEqual(features["effective_reversals"], 3)
        self.assertLess(features["path_efficiency"], 0.3)
        self.assertEqual(reversal[0]["status_label"], "规则条件触发，待人工复核")

    def test_return_to_start_and_same_line_multiple_trips(self) -> None:
        zone = synthetic_zone()
        config = self.reversal_config()
        for lengths in ([100.0, 100.0, 100.0, 100.0], [100.0] * 7):
            rows = same_line_legs(lengths)
            run = make_run({1: rows}, analysis_end=rows[-1][0] + 0.04)
            result = evaluate_rules(
                run, [zone], assignments_for(run, zone.zone_id), config
            )
            candidates = [
                item for item in result["candidates"] if item["rule_id"] == "reversal"
            ]
            self.assertEqual(len(candidates), 1, lengths)
            features = candidates[0]["trigger_features"]
            self.assertGreaterEqual(features["effective_reversals"], 3)
            self.assertLess(features["path_efficiency"], 0.3)
            self.assertGreaterEqual(features["valid_leg_length_px"], 160.0)
            self.assertGreaterEqual(features["path_length_px"], 160.0)

    def test_short_legs_do_not_trigger_and_efficiency_is_uncomputable(self) -> None:
        rows = same_line_legs([30.0] * 12, leg_seconds=1.6)
        run = make_run({1: rows}, analysis_end=rows[-1][0] + 0.04)
        zone = synthetic_zone()
        result = evaluate_rules(
            run,
            [zone],
            assignments_for(run, zone.zone_id),
            self.reversal_config(),
        )
        reversal = next(item for item in result["rules"] if item["rule_id"] == "reversal")
        self.assertEqual(reversal["candidate_count"], 0)
        near = next(item for item in reversal["near_misses"] if item["track_id"] == 1)
        self.assertEqual(near["max_effective_reversals"], 0)
        self.assertIsNone(near["min_path_efficiency"])

    def test_broken_track_does_not_accumulate_reversals_across_gap(self) -> None:
        first = same_line_legs([100.0, 100.0, 100.0])
        second = [
            (round(time + 5.6, 4), x, y)
            for time, x, y in same_line_legs([100.0, 100.0, 100.0])
        ]
        rows = first + second
        run = make_run({1: rows}, analysis_end=rows[-1][0] + 0.04)
        zone = synthetic_zone()
        result = evaluate_rules(
            run,
            [zone],
            assignments_for(run, zone.zone_id),
            self.reversal_config(),
        )
        reversal = next(item for item in result["rules"] if item["rule_id"] == "reversal")
        self.assertEqual(reversal["candidate_count"], 0)
        near = next(item for item in reversal["near_misses"] if item["track_id"] == 1)
        self.assertLess(near["max_effective_reversals"], 3)

    def test_stationary_jitter_has_uncomputable_efficiency_and_no_reversal(self) -> None:
        rows = []
        for index in range(300):
            x = 500 + (2.0 if index % 2 == 0 else -2.0)
            y = 500 + (1.5 if index % 3 == 0 else -1.5)
            rows.append((round(index / 25.0, 4), x, y))
        run = make_run({1: rows}, analysis_end=12.0)
        zone = synthetic_zone()
        assignments = assignments_for(run, zone.zone_id)
        result = evaluate_rules(run, [zone], assignments, self.reversal_config())
        reversal = next(item for item in result["rules"] if item["rule_id"] == "reversal")
        self.assertEqual(reversal["candidate_count"], 0)
        near = next(item for item in reversal["near_misses"] if item["track_id"] == 1)
        self.assertEqual(near["max_effective_reversals"], 0)
        self.assertIsNone(near["min_path_efficiency"])

    def test_low_displacement_zigzag_is_not_a_reversal(self) -> None:
        rows = []
        for index in range(300):
            x = 500 + 20 * math.sin(index / 4.0)
            y = 500 + 10 * math.cos(index / 5.0)
            rows.append((round(index / 25.0, 4), x, y))
        run = make_run({1: rows}, analysis_end=12.0)
        zone = synthetic_zone()
        result = evaluate_rules(
            run,
            [zone],
            assignments_for(run, zone.zone_id),
            self.reversal_config(),
        )
        self.assertEqual(
            len([item for item in result["candidates"] if item["rule_id"] == "reversal"]),
            0,
        )

    def test_long_gap_splits_movement_segments(self) -> None:
        first = line_track(100, 500, 500, 0.0, 4.0)
        second = line_track(100, 500, 500, 5.0, 4.0)
        rows = first + second
        run = make_run({1: rows}, analysis_end=9.0)
        zone = synthetic_zone()
        result = evaluate_rules(
            run,
            [zone],
            assignments_for(run, zone.zone_id),
            self.reversal_config(),
        )
        self.assertEqual(
            len([item for item in result["candidates"] if item["rule_id"] == "reversal"]),
            0,
        )
        near = next(item for item in result["rules"] if item["rule_id"] == "reversal")[
            "near_misses"
        ]
        # The two 4-second movement segments each have fewer than 3 reversals.
        self.assertTrue(all(item["max_effective_reversals"] < 3 for item in near))


class SyntheticOccupancyTests(unittest.TestCase):
    def test_short_observation_is_not_evaluated(self) -> None:
        rows = line_track(100, 900, 500, 0.0, 12.0)
        run = make_run({1: rows}, analysis_end=12.0)
        zone = synthetic_zone()
        assignments = assignments_for(run, zone.zone_id)
        result = evaluate_rules(run, [zone], assignments, base_config())
        occupancy = next(
            item for item in result["rules"] if item["rule_id"] == "occupancy_change_60s"
        )
        self.assertEqual(occupancy["evaluation_status"], "insufficient_observation")
        self.assertEqual(occupancy["candidate_count"], 0)

    def test_sixty_second_window_can_be_evaluated_with_synthetic_counts(self) -> None:
        # Build a synthetic zone_stats sequence directly; this tests the rule
        # branch that is unreachable on the 12-second public sample.
        fps = 1.0
        times = [float(index) for index in range(70)]
        frame_indices = list(range(70))
        counts = [2] * 30 + [3] * 30 + [6] * 10
        zone = synthetic_zone()
        zone_stats = {
            "fps": fps,
            "times": times,
            "frame_indices": frame_indices,
            "zones": [
                {
                    "zone_id": zone.zone_id,
                    "counts": counts,
                }
            ],
        }
        # Provide a real observation at frame 60 so the evidence lookup succeeds.
        run = make_run({1: line_track(100, 900, 500, 0.0, 61.0, fps=1.0)}, fps=fps, analysis_end=70.0)
        config = base_config()
        rule = next(
            item for item in config["rules"] if item["rule_id"] == "occupancy_change_60s"
        )
        rule["params"].update(
            {"window_seconds": 60.0, "sustain_seconds": 1.0, "from_below": 3, "to_at_least": 6}
        )
        candidates, status = evaluate_occupancy_change(
            run,
            [zone],
            zone_stats,
            rule,
            "hash",
            "2026-11-04T00:00:00+00:00",
            points=with_zone_ids(run, assignments_for(run, zone.zone_id)),
        )
        self.assertEqual(status["evaluation_status"], "evaluated")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["trigger_features"]["count_before"], 2)
        self.assertEqual(candidates[0]["trigger_features"]["count_at_trigger"], 6)


if __name__ == "__main__":
    unittest.main()
