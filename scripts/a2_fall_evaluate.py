#!/usr/bin/env python3
"""A2/P0: evaluate pose-based fall events on GMDCSA-24.

Two levels are reported:

* video-level: does the video contain a fall event?
* event-level: temporal IoU matching between predicted and CSV intervals,
  plus detection delay and duration error.

The detector is a transparent rule baseline over YOLOv8n-pose features.  It is
not a mature-platform capability test and not a clinical fall detector.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
from st_memory.a2_fall import event_level_metrics, scores_to_events, video_level_metrics  # noqa: E402

DEFAULT_EVENTS = PROJECT_DIR / "data/a2/gmd_csa24/events.csv"
DEFAULT_FEATURES = PROJECT_DIR / "data/a2/gmd_csa24/pose_features.csv"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/a2/gmd"

FIXED_PARAMS = {
    "angle_th": 35.0,
    "aspect_th": 1.10,
    "drop_th": 0.07,
    "wide_aspect_th": 1.40,
    "conf_th": 0.50,
    "min_consecutive": 3,
    "min_duration": 0.5,
    "max_gap": 0.35,
}


def parse_intervals(text: str) -> list[tuple[float, float]]:
    if not isinstance(text, str) or not text.strip():
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [(float(start), float(end)) for start, end in value]


def primary_activity(classes: str) -> str:
    text = str(classes)
    for segment in text.split(";"):
        segment = segment.strip()
        if not segment or "fall" in segment.lower():
            continue
        label = re.split(r"[\[\(]", segment)[0].strip()
        if label:
            return label
    return "unknown"


def fall_type(classes: str) -> str:
    match = re.search(r"(?:falling|fall)\s*\((FW|BW|SW)\)", str(classes), re.IGNORECASE)
    return match.group(1).upper() if match else "unknown"


def detect_video(group: pd.DataFrame, params: dict) -> tuple[list[tuple[float, float]], dict]:
    if group.empty:
        return [], {"baseline_cy": None, "max_drop_ratio": None, "min_angle": None, "max_aspect": None}
    group = group.sort_values("time").reset_index(drop=True)
    baseline_rows = group[group["time"] <= 1.0]
    baseline_cy = float(baseline_rows["center_y"].median()) if len(baseline_rows) else float(group["center_y"].iloc[0])
    frame_height = float(group["frame_height"].median())
    drop_ratio = (group["center_y"] - baseline_cy) / frame_height if frame_height > 0 else group["center_y"] * 0
    posture = (
        (group["torso_angle"] < params["angle_th"])
        & (group["aspect"] > params["aspect_th"])
        & (group["pose_conf"] > params["conf_th"])
    )
    wide = group["aspect"] > params["wide_aspect_th"]
    fall_frame = (posture | wide) & (drop_ratio > params["drop_th"])
    scores = fall_frame.astype(float).tolist()
    events = scores_to_events(
        group["time"].tolist(),
        scores,
        high=0.6,
        low=0.35,
        min_consecutive=int(params["min_consecutive"]),
        min_duration=float(params["min_duration"]),
        max_gap=float(params["max_gap"]),
    )
    diagnostics = {
        "baseline_cy": baseline_cy,
        "max_drop_ratio": float(drop_ratio.max()),
        "min_angle": float(group["torso_angle"].min()),
        "max_aspect": float(group["aspect"].max()),
        "posture_frames": int(posture.sum()),
        "wide_frames": int(wide.sum()),
        "fall_frames": int(fall_frame.sum()),
    }
    return events, diagnostics


def predict_all(events: pd.DataFrame, features: pd.DataFrame, params: dict) -> list[dict]:
    records = []
    for _, event in events.iterrows():
        group = features[features["video_id"] == event["video_id"]]
        predicted, diagnostics = detect_video(group, params)
        ground_truth = parse_intervals(event["fall_intervals"])
        records.append(
            {
                "video_id": event["video_id"],
                "subject": int(event["subject"]),
                "kind": event["kind"],
                "has_fall": bool(event["has_fall"]),
                "fall_intervals": ground_truth,
                "ground_truth": ground_truth,
                "predicted": predicted,
                "predicted_any": int(len(predicted) > 0),
                "activity": primary_activity(event["classes"]),
                "fall_type": fall_type(event["classes"]),
                **diagnostics,
            }
        )
    return records


def evaluate(records: list[dict]) -> dict:
    video = video_level_metrics(
        [int(record["has_fall"]) for record in records],
        [record["predicted_any"] for record in records],
    )
    event_records = []
    excluded = []
    for record in records:
        if record["has_fall"] and not record["fall_intervals"]:
            excluded.append(record["video_id"])
            continue
        event_records.append(record)
    events_03 = event_level_metrics(event_records, iou_threshold=0.3)
    events_05 = event_level_metrics(event_records, iou_threshold=0.5)
    return {
        "video_level": video,
        "event_level": events_03,
        "event_level_iou_0.5": events_05,
        "event_excluded_fall_videos": excluded,
    }


def subset(records: list[dict], subjects: list[int]) -> list[dict]:
    return [record for record in records if record["subject"] in subjects]


def grid_search(events: pd.DataFrame, features: pd.DataFrame, dev_subjects: list[int]) -> list[dict]:
    dev_events = events[events["subject"].isin(dev_subjects)]
    rows = []
    for angle in (25, 30, 35, 40, 45):
        for aspect in (1.0, 1.1, 1.2, 1.3):
            for drop in (0.04, 0.05, 0.06, 0.07, 0.08, 0.10):
                for wide in (1.3, 1.4, 1.5, 1.6):
                    params = {
                        **FIXED_PARAMS,
                        "angle_th": angle,
                        "aspect_th": aspect,
                        "drop_th": drop,
                        "wide_aspect_th": wide,
                    }
                    records = predict_all(dev_events, features, params)
                    metrics = evaluate(records)
                    rows.append(
                        {
                            **params,
                            "video_f1": metrics["video_level"]["f1"],
                            "video_precision": metrics["video_level"]["precision"],
                            "video_recall": metrics["video_level"]["recall"],
                            "event_f1": metrics["event_level"]["f1"],
                            "event_precision": metrics["event_level"]["precision"],
                            "event_recall": metrics["event_level"]["recall"],
                            "event_tp": metrics["event_level"]["tp"],
                            "event_fp": metrics["event_level"]["fp"],
                            "event_fn": metrics["event_level"]["fn"],
                        }
                    )
    rows.sort(key=lambda row: (row["event_f1"], row["video_f1"]), reverse=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate GMDCSA-24 fall baseline")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dev-subjects", nargs="*", type=int, default=[1, 2])
    parser.add_argument("--test-subjects", nargs="*", type=int, default=[3, 4])
    parser.add_argument("--subjects", nargs="*", type=int, default=None, help="Optional subject filter")
    args = parser.parse_args()

    events = pd.read_csv(args.events)
    if args.subjects:
        events = events[events["subject"].isin(args.subjects)].reset_index(drop=True)
    features = pd.read_csv(args.features)
    available_ids = set(features["video_id"].unique())
    missing = sorted(set(events["video_id"]) - available_ids)
    events = events[events["video_id"].isin(available_ids)].reset_index(drop=True)
    if len(events) == 0:
        raise SystemExit("no videos with extracted features; run a2_fall_extract_features.py first")
    args.output.mkdir(parents=True, exist_ok=True)

    print("grid search on development subjects...", flush=True)
    grid = grid_search(events, features, args.dev_subjects)
    pd.DataFrame(grid).to_csv(args.output / "grid.csv", index=False)
    best = grid[0]
    best_params = {key: best[key] for key in FIXED_PARAMS}
    print(f"best dev event F1={best['event_f1']:.3f}, video F1={best['video_f1']:.3f}", flush=True)

    all_records = predict_all(events, features, FIXED_PARAMS)
    fixed_all = evaluate(all_records)
    fixed_dev = evaluate(subset(all_records, args.dev_subjects))
    fixed_test = evaluate(subset(all_records, args.test_subjects))

    best_records = predict_all(events, features, best_params)
    best_all = evaluate(best_records)
    best_dev = evaluate(subset(best_records, args.dev_subjects))
    best_test = evaluate(subset(best_records, args.test_subjects))

    prediction_rows = []
    for record in all_records:
        prediction_rows.append(
            {
                "video_id": record["video_id"],
                "subject": record["subject"],
                "kind": record["kind"],
                "activity": record["activity"],
                "fall_type": record["fall_type"],
                "has_fall": record["has_fall"],
                "predicted_any_fixed": record["predicted_any"],
                "predicted_events_fixed": json.dumps(record["predicted"]),
                "gt_events": json.dumps(record["fall_intervals"]),
                "baseline_cy": record["baseline_cy"],
                "max_drop_ratio": record["max_drop_ratio"],
                "min_angle": record["min_angle"],
                "max_aspect": record["max_aspect"],
            }
        )
    pd.DataFrame(prediction_rows).to_csv(args.output / "predictions_fixed.csv", index=False)

    # Per-subject video-level results for the fixed baseline.
    per_subject = []
    for subject in sorted(events["subject"].unique()):
        subject_records = subset(all_records, [int(subject)])
        metrics = evaluate(subject_records)
        per_subject.append({"subject": int(subject), **metrics["video_level"], "event_f1": metrics["event_level"]["f1"]})

    adl_false_alarms = [
        record for record in all_records if not record["has_fall"] and record["predicted_any"]
    ]
    fall_misses = [
        record for record in all_records if record["has_fall"] and not record["predicted_any"]
    ]

    per_type = []
    for value in sorted({record["fall_type"] for record in all_records if record["has_fall"]}):
        type_records = [record for record in all_records if record["has_fall"] and record["fall_type"] == value]
        type_metrics = evaluate(type_records)
        per_type.append(
            {
                "fall_type": value,
                "videos": len(type_records),
                "video_recall": type_metrics["video_level"]["recall"],
                "video_f1": type_metrics["video_level"]["f1"],
                "event_recall": type_metrics["event_level"]["recall"],
                "event_f1": type_metrics["event_level"]["f1"],
            }
        )

    adl_activity = []
    adl_records = [record for record in all_records if not record["has_fall"]]
    for activity in sorted({record["activity"] for record in adl_records}):
        group = [record for record in adl_records if record["activity"] == activity]
        false_alarms = sum(1 for record in group if record["predicted_any"])
        adl_activity.append(
            {
                "activity": activity,
                "videos": len(group),
                "false_alarms": false_alarms,
                "false_alarm_rate": false_alarms / len(group) if group else 0.0,
            }
        )

    summary = {
        "schema": "a2-gmd-fall-evaluation-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": "GMDCSA-24",
        "events_csv": str(args.events.relative_to(PROJECT_DIR)),
        "features_csv": str(args.features.relative_to(PROJECT_DIR)),
        "videos_evaluated": int(len(events)),
        "videos_missing_features": missing,
        "level_note": "video-level and event-level; event matching uses temporal IoU >= 0.3",
        "fixed_params": FIXED_PARAMS,
        "fixed_all": fixed_all,
        "fixed_dev": fixed_dev,
        "fixed_test": fixed_test,
        "best_dev_params": best_params,
        "best_dev_grid_row": best,
        "best_all": best_all,
        "best_dev": best_dev,
        "best_test": best_test,
        "per_subject_fixed": per_subject,
        "per_fall_type_fixed": per_type,
        "adl_activity_fixed": adl_activity,
        "adl_false_alarm_count": len(adl_false_alarms),
        "adl_false_alarms": [
            {"video_id": item["video_id"], "activity": item["activity"], "max_drop_ratio": item["max_drop_ratio"]}
            for item in adl_false_alarms
        ],
        "fall_miss_count": len(fall_misses),
        "fall_misses": [
            {"video_id": item["video_id"], "max_drop_ratio": item["max_drop_ratio"], "min_angle": item["min_angle"], "max_aspect": item["max_aspect"]}
            for item in fall_misses
        ],
        "limitations": [
            "Self-built pose rule, not a mature platform and not a clinical detector.",
            "Largest-person selection only; multi-person scenes are not handled.",
            "CSV fall intervals are used as event ground truth; malformed intervals are parsed conservatively.",
            "Thresholds selected on subjects 1+2 and evaluated on subjects 3+4; this is a small, subject-wise split.",
            "Fall events on beds and partial-body visibility remain difficult.",
        ],
    }
    (args.output / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "fixed_all_video": fixed_all["video_level"],
        "fixed_all_event": {key: fixed_all["event_level"][key] for key in ("tp", "fp", "fn", "precision", "recall", "f1")},
        "fixed_test_video": fixed_test["video_level"],
        "fixed_test_event": {key: fixed_test["event_level"][key] for key in ("tp", "fp", "fn", "precision", "recall", "f1")},
        "best_params": best_params,
        "best_test_video": best_test["video_level"],
        "best_test_event": {key: best_test["event_level"][key] for key in ("tp", "fp", "fn", "precision", "recall", "f1")},
        "adl_false_alarms": len(adl_false_alarms),
        "fall_misses": len(fall_misses),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
