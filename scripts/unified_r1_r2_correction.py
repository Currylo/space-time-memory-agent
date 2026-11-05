#!/usr/bin/env python3
"""Apply R1 (causal stay window) and R2 (clock boundary/precision) corrections.

The 18 platform events and ledger identity fields are preserved.  The script
adds corrected rule windows, recomputes coverage with a strict completeness
check, replaces the 40 ms-late recording intercept with first-frame/coverage-end
anchors, and records holdout precision separately from fit-internal residuals.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_memory.a2_adapter import Ledger, PlatformClient, candidate_key  # noqa: E402
from st_memory.unified_platform import (  # noqa: E402
    ClockModel,
    ClockSample,
    causal_stay_window,
    clock_segment_anchors,
    evidence_coverage,
    fit_clock,
)

RUN_DIR = ROOT / "runs/unified-mot20-05-detect-pose-v2-001"
LEDGER = ROOT / "data/a2/adapter/ledger.sqlite"
SOURCE = ROOT / "data/a2/unified/MOT20-05-raw.mp4"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_window_v2(record: dict, observations: list[dict], output_dir: Path) -> dict:
    import cv2
    import numpy as np

    start = float(record["rule_window_start"])
    end = float(record["rule_window_end"])
    condition = float(record["condition_met_video_time"])
    track_id = record.get("track_id")
    threshold = float(record["features"].get("threshold_seconds", record["features"].get("observed_dwell_seconds", 10.0)))
    sample_interval = 0.2
    points = []
    for row in observations:
        time = float(row["time"])
        if time < start - 1e-6 or time > condition + 1e-6:
            continue
        person = next((item for item in row.get("people", []) if item.get("id") == track_id), None)
        if person is None:
            continue
        points.append({
            "time": time,
            "dwell": float(person.get("dwell", 0.0)),
            "inside": bool(person.get("inside")),
            "box": person.get("box"),
        })
    valid = [p for p in points if p["time"] <= end + 1e-6 and p["inside"]]
    post = [p for p in points if p["time"] > end + 1e-6]
    valid_seconds = round(len(valid) * sample_interval, 6)
    max_dwell = max((p["dwell"] for p in valid), default=0.0)
    first_dwell = valid[0]["dwell"] if valid else None
    condition_ok = (
        len(valid) >= int(round(threshold / sample_interval))
        and first_dwell is not None
        and first_dwell <= sample_interval + 1e-6
        and max_dwell + 1e-6 >= threshold
        and all(p["inside"] for p in valid)
    )
    span = condition - start
    result = {
        "schema": "unified-stay-window-v2",
        "kind": "stay",
        "track_id": track_id,
        "window_version": "stay-window-v2-causal",
        "window_start": start,
        "window_end": end,
        "comparison_window_start": start,
        "comparison_window_end": end,
        "comparison_window_seconds": round(end - start, 6),
        "required_coverage_start": start,
        "required_coverage_end": condition,
        "required_coverage_seconds": round(condition - start, 6),
        "condition_met_video_time": condition,
        "condition_in_evidence": False,
        "threshold_seconds": threshold,
        "sample_interval_seconds": sample_interval,
        "valid_sample_count": len(valid),
        "valid_seconds": valid_seconds,
        "span_seconds": round(span, 6),
        "missing_gap_seconds": round(max(0.0, span - valid_seconds), 6),
        "first_dwell_seconds": first_dwell,
        "max_dwell_seconds": max_dwell,
        "condition_ok": condition_ok,
        "post_trigger_frame": ({**post[0], "used_in_evidence": False} if post else None),
        "points": points,
        "note": "Condition-met next sampling frame is stored as post-trigger context and is not counted in the 10 s basis.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{record['candidate_id']}-window-v2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Chart: evidence range start..trigger, post-trigger next frame shaded.
    width, height = 900, 260
    canvas = np.full((height, width, 3), 245, dtype=np.uint8)
    x0, x1 = 30, 870
    y0, y1 = 220, 40
    xmax = max(condition, end)
    xmin = start
    ymax = max(threshold * 1.25, max((p["dwell"] for p in points), default=threshold))
    def px(t):
        return int(x0 + (t - xmin) / max(1e-9, xmax - xmin) * (x1 - x0))
    def py(v):
        return int(y0 - v / max(1e-9, ymax) * (y0 - y1))
    # shade post-trigger context
    cv2.rectangle(canvas, (px(end), y1), (px(condition), y0), (226, 238, 247), -1)
    cv2.line(canvas, (px(end), y1), (px(end), y0), (120, 150, 180), 2)
    cv2.putText(canvas, "post-trigger next frame (not used)", (max(px(end) + 4, x0), y1 + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (110, 130, 150), 1, cv2.LINE_AA)
    for i in range(1, len(points)):
        if points[i]["time"] <= end + 1e-6:
            color = (40, 130, 150)
        else:
            color = (110, 145, 190)
        cv2.line(canvas, (px(points[i - 1]["time"]), py(points[i - 1]["dwell"])),
                 (px(points[i]["time"]), py(points[i]["dwell"])), color, 2)
    threshold_y = py(threshold)
    cv2.line(canvas, (x0, threshold_y), (x1, threshold_y), (80, 120, 200), 1)
    cv2.putText(canvas, f"track {track_id} valid {valid_seconds:.2f}s / span {span:.2f}s / gap {max(0.0, span-valid_seconds):.2f}s",
                (x0, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 40, 50), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"condition {condition:.2f}s", (px(condition) - 60, 46),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100, 120, 140), 1, cv2.LINE_AA)
    cv2.imwrite(str(output_dir / f"{record['candidate_id']}-window-v2.png"), canvas)
    result["chart"] = str((output_dir / f"{record['candidate_id']}-window-v2.png").relative_to(ROOT))
    return result


def apply_record_correction(record: dict, result: dict, coverage: dict, page: bool = False) -> None:
    result.setdefault("window_version", result.get("version", "stay-window-v2-causal"))
    record["window_version"] = result["window_version"]
    record["corrected_rule_window_start"] = result["causal_window_start"]
    record["corrected_rule_window_end"] = result["causal_window_end"]
    record["rule_window_start"] = result["causal_window_start"]
    record["rule_window_end"] = result["causal_window_end"]
    record["comparison_window_start"] = result["causal_window_start"]
    record["comparison_window_end"] = result["causal_window_end"]
    record["comparison_window_seconds"] = round(result["causal_window_end"] - result["causal_window_start"], 6)
    record["required_coverage_start"] = result["required_coverage_start"]
    record["required_coverage_end"] = result["required_coverage_end"]
    record["required_coverage_seconds"] = result["required_coverage_seconds"]
    record["observed_coverage_end_video_time"] = result["observed_coverage_end_video_time"]
    record["ledger_identity_evidence_start"] = record.get("evidence_start")
    record["ledger_identity_evidence_end"] = record.get("evidence_end")
    for key in (
        "observed_start_video_time", "observed_end_video_time",
        "observed_span_end_video_time", "observed_span_seconds",
        "observed_span_to_trigger_seconds", "observed_valid_seconds",
        "missing_gap_seconds", "sample_count",
        "required_coverage_start", "required_coverage_end",
        "required_coverage_seconds", "observed_coverage_end_video_time",
    ):
        if key in result:
            record[key] = result[key]
    record["trigger_frame_video_time"] = record.get("evidence_frame_video_time")
    record["post_trigger_frame_video_time"] = record.get("condition_met_video_time")
    record["post_trigger_frame"] = result.get("post_trigger_frame")
    features = record.setdefault("features", {})
    features.update({
        "causal_window_start": result["causal_window_start"],
        "causal_window_end": result["causal_window_end"],
        "causal_window_version": result["window_version"],
        "required_coverage_end": result["required_coverage_end"],
        "required_coverage_seconds": result["required_coverage_seconds"],
        "observed_span_seconds": result["observed_span_seconds"],
        "observed_valid_seconds": result["observed_valid_seconds"],
        "missing_gap_seconds": result["missing_gap_seconds"],
        "trigger_frame_video_time": record.get("evidence_frame_video_time"),
        "post_trigger_frame_video_time": record.get("condition_met_video_time"),
        "condition_in_evidence": False,
    })
    if "clip" in record:
        if "coverage_v1" not in record["clip"]:
            record["clip"]["coverage_v1"] = record["clip"].get("coverage")
        record["clip"]["coverage"] = coverage
    if "evidence" in record:
        evidence = record["evidence"]
        if "window_v1" not in evidence:
            evidence["window_v1"] = evidence.get("window")
        evidence["window"] = result
        evidence["status"] = "verified" if coverage.get("status") == "verified" and result.get("condition_ok") else "partial_or_failed"
        evidence["corrected_rule_window_start"] = result["causal_window_start"]
        evidence["corrected_rule_window_end"] = result["causal_window_end"]
    if page:
        record["window_chart_url"] = f"/api/unified/alignment/{record['candidate_id']}-window-v2.png"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    args = parser.parse_args()
    round_dir = args.round_dir.resolve()
    corrections_dir = round_dir / "corrections"
    corrections_dir.mkdir(parents=True, exist_ok=True)
    observations = [json.loads(line) for line in (RUN_DIR / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    mapping = [json.loads(line) for line in (round_dir / "candidate_event_mapping.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    page_path = ROOT / "demo" / "data" / "unified_platform.json"
    page = read_json(page_path)
    evidence_manifest = read_json(round_dir / "evidence_manifest.json")
    clock_alignment = read_json(round_dir / "alignment" / "clock_alignment.json")
    calibration = read_json(round_dir / "platform" / "calibration.json")
    calibration_alignment = read_json(round_dir / "alignment" / "calibration_alignment.json")
    clock_alignment_path = round_dir / "alignment" / "clock_alignment.json"
    calibration_path = round_dir / "platform" / "calibration.json"
    config = read_json(RUN_DIR / "config.json")
    fps = float(config["video_fps"])
    threshold_default = float(config["stay_seconds"])
    for record in mapping:
        record["source_video_sha256"] = config["source_sha256"]
    for record in page.get("candidates", []):
        record["source_video_sha256"] = config["source_sha256"]
    for record in evidence_manifest.get("candidates", []):
        record["source_video_sha256"] = config["source_sha256"]
    expected_starts = {
        "ca4a5694d4b1d9c4": 3.2,
        "bebef518650e604e": 20.6,
        "753173d4ba009bc9": 33.0,
        "bd80e570334280b7": 106.0,
    }

    # ------------------------------------------------------------------
    # R1: causal stay windows
    # ------------------------------------------------------------------
    stay_corrections = []
    for record in mapping:
        if record["kind"] != "stay":
            continue
        result = causal_stay_window(
            observations,
            record["track_id"],
            record["evidence_frame_video_time"],
            threshold_seconds=float(record["features"].get("threshold_seconds", threshold_default)),
            condition_time=record["condition_met_video_time"],
        )
        if record["candidate_id"] in expected_starts:
            if abs(result["causal_window_start"] - expected_starts[record["candidate_id"]]) > 1e-6:
                raise SystemExit(
                    f"R1 audit reproduction mismatch for {record['candidate_id']}: "
                    f"{result['causal_window_start']} != {expected_starts[record['candidate_id']]}"
                )
        result["candidate_id"] = record["candidate_id"]
        result["old_rule_window_start"] = float(record["evidence_start"])
        result["old_rule_window_end"] = float(record["evidence_end"])
        result["old_coverage"] = record.get("clip", {}).get("coverage")
        result["chart"] = None
        # Update mapping record first, then page/evidence records by candidate id.
        coverage = evidence_coverage(
            evidence_start=result["causal_window_start"],
            evidence_end=result["required_coverage_end"],
            clip_source_start=record["clip"].get("clip_source_start"),
            clip_source_end=record["clip"].get("clip_source_end"),
            clip_duration=record["clip"].get("duration"),
            clip_fps=record["clip"].get("fps", 25.0),
            comparison_window_seconds=result["causal_window_end"] - result["causal_window_start"],
        )
        result["coverage_v2"] = coverage
        apply_record_correction(record, result, coverage)
        stay_corrections.append(result)
    mapping_by_id = {record["candidate_id"]: record for record in mapping}
    for record in page.get("candidates", []):
        if record.get("kind") != "stay":
            continue
        result = next(item for item in stay_corrections if item["candidate_id"] == record["candidate_id"])
        # The evidence record is the same candidate but has its own nested data.
        apply_record_correction(record, result, result["coverage_v2"], page=True)
        record["window_chart_url"] = f"/api/unified/alignment/{record['candidate_id']}-window-v2.png"
    for record in evidence_manifest.get("candidates", []):
        if record.get("kind") != "stay":
            continue
        result = next(item for item in stay_corrections if item["candidate_id"] == record["candidate_id"])
        apply_record_correction(record, result, result["coverage_v2"])
    # Generate v2 charts/JSON for each stay after records are updated.
    for result in stay_corrections:
        record = mapping_by_id[result["candidate_id"]]
        window_v2 = build_window_v2(record, observations, round_dir / "alignment")
        result["window_v2"] = {k: window_v2[k] for k in (
            "valid_sample_count", "valid_seconds", "span_seconds", "missing_gap_seconds",
            "first_dwell_seconds", "max_dwell_seconds", "condition_ok", "post_trigger_frame",
        )}
        result["window_v2_path"] = str((round_dir / "alignment" / f"{record['candidate_id']}-window-v2.json").relative_to(ROOT))
        result["chart"] = window_v2["chart"]
        # Replace the page/evidence summary with the exact v2 window (the
        # causal result above is retained in window_v1/summary fields).
        record["evidence"]["window"] = window_v2
        record["evidence"]["window_v2_path"] = result["window_v2_path"]
        page_record = next(item for item in page.get("candidates", []) if item.get("candidate_id") == record["candidate_id"])
        page_record["evidence"]["window"] = window_v2
        page_record["evidence"]["window_v2_path"] = result["window_v2_path"]
        evidence_record = next(item for item in evidence_manifest.get("candidates", []) if item.get("candidate_id") == record["candidate_id"])
        evidence_record["evidence"]["window"] = window_v2
        evidence_record["evidence"]["window_v2_path"] = result["window_v2_path"]

    # Update ledger candidate_json with correction metadata, keeping identity fields.
    ledger = Ledger(LEDGER)
    try:
        for result in stay_corrections:
            record = mapping_by_id[result["candidate_id"]]
            row = ledger.get(record["ledger_key"])
            if row is None:
                raise SystemExit(f"ledger row missing: {record['candidate_id']}")
            candidate = json.loads(row["candidate_json"])
            candidate["correction"] = {
                "version": result["window_version"],
                "corrected_rule_window_start": result["causal_window_start"],
                "corrected_rule_window_end": result["causal_window_end"],
                "observed_span_seconds": result["observed_span_seconds"],
                "observed_valid_seconds": result["observed_valid_seconds"],
                "missing_gap_seconds": result["missing_gap_seconds"],
                "trigger_frame_video_time": record["evidence_frame_video_time"],
                "condition_met_video_time": record["condition_met_video_time"],
                "ledger_identity_evidence_start": record["evidence_start"],
                "ledger_identity_evidence_end": record["evidence_end"],
                "note": "adapter identity fields kept for key stability; corrected causal window used for evidence and review",
            }
            candidate.setdefault("features", {}).update(record["features"])
            ledger.connection.execute("UPDATE candidates SET candidate_json=? WHERE key=?", (
                json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")), record["ledger_key"],
            ))
    finally:
        ledger.close()

    # Append correction to the 8 stay event descriptions (no new event).
    client = PlatformClient("http://127.0.0.1:15000", timeout=20.0)
    description_updates = []
    for result in stay_corrections:
        record = mapping_by_id[result["candidate_id"]]
        status, event = client.get_event(record["event_id"])
        if status != 200:
            description_updates.append({"candidate_id": record["candidate_id"], "status": status, "error": "event not found"})
            continue
        description = (event.get("data") or {}).get("description") or ""
        if "correction=stay-window-v2-causal" in description:
            description_updates.append({"candidate_id": record["candidate_id"], "status": 200, "already_present": True})
            continue
        addition = (
            f"; correction=stay-window-v2-causal; corrected_rule_window={result['causal_window_start']:.3f}-{result['causal_window_end']:.3f}; "
            f"observed_valid={result['observed_valid_seconds']:.3f}; observed_span={result['observed_span_seconds']:.3f}; "
            f"missing_gap={result['missing_gap_seconds']:.3f}; condition_met={record['condition_met_video_time']:.3f}; "
            f"ledger_identity_window={record['evidence_start']:.3f}-{record['evidence_end']:.3f}"
        )
        status, body = client.set_description(record["event_id"], description + addition)
        description_updates.append({"candidate_id": record["candidate_id"], "status": status, "body": body, "addition": addition})

    write_json(corrections_dir / "stay_windows_v2.json", {
        "schema": "unified-stay-window-correction-v1",
        "created_at_utc": utc_now(),
        "reason": "R1: cumulative observed dwell can span more wall time when samples are missing; condition-met next frame is not part of the causal basis.",
        "stay_candidates": stay_corrections,
        "description_updates": description_updates,
        "ledger_identity_preserved": True,
        "event_ids_unchanged": True,
    })

    # Apply strict coverage to crowd candidates as well (no window rewrite).
    crowd_updates = []
    for record in mapping:
        if record["kind"] == "crowd":
            coverage = evidence_coverage(
                evidence_start=record["rule_window_start"],
                evidence_end=record["condition_met_video_time"],
                clip_source_start=record["clip"].get("clip_source_start"),
                clip_source_end=record["clip"].get("clip_source_end"),
                clip_duration=record["clip"].get("duration"),
                clip_fps=record["clip"].get("fps", 25.0),
                comparison_window_seconds=record["rule_window_end"] - record["rule_window_start"],
            )
            record["clip"]["coverage_v1"] = record["clip"].get("coverage_v1", record["clip"].get("coverage"))
            record["clip"]["coverage"] = coverage
            record["evidence"]["status"] = "verified" if coverage.get("status") == "verified" and record["evidence"]["window"].get("condition_ok") else "partial_or_failed"
            record["required_coverage_start"] = record["rule_window_start"]
            record["required_coverage_end"] = record["condition_met_video_time"]
            record["required_coverage_seconds"] = round(record["condition_met_video_time"] - record["rule_window_start"], 6)
            record["comparison_window_start"] = record["rule_window_start"]
            record["comparison_window_end"] = record["rule_window_end"]
            record["comparison_window_seconds"] = round(record["rule_window_end"] - record["rule_window_start"], 6)
            crowd_updates.append({"candidate_id": record["candidate_id"], "coverage": coverage})
    for record in page.get("candidates", []):
        if record.get("kind") == "crowd":
            source = next(item for item in mapping if item["candidate_id"] == record["candidate_id"])
            record["clip"]["coverage_v1"] = record["clip"].get("coverage_v1", record["clip"].get("coverage"))
            record["clip"]["coverage"] = source["clip"]["coverage"]
            record["evidence"]["status"] = source["evidence"]["status"]
            for key in ("required_coverage_start", "required_coverage_end", "required_coverage_seconds", "comparison_window_start", "comparison_window_end", "comparison_window_seconds"):
                record[key] = source[key]
    for record in evidence_manifest.get("candidates", []):
        if record.get("kind") == "crowd":
            source = next(item for item in mapping if item["candidate_id"] == record["candidate_id"])
            record["clip"]["coverage_v1"] = record["clip"].get("coverage_v1", record["clip"].get("coverage"))
            record["clip"]["coverage"] = source["clip"]["coverage"]
            record["evidence"]["status"] = source["evidence"]["status"]
            for key in ("required_coverage_start", "required_coverage_end", "required_coverage_seconds", "comparison_window_start", "comparison_window_end", "comparison_window_seconds"):
                record[key] = source[key]

    # ------------------------------------------------------------------
    # R2: clock endpoint semantics, holdout precision, mapping residuals
    # ------------------------------------------------------------------
    segments = clock_alignment["live_alignment"]["segments"]
    anchors = [clock_segment_anchors(segment, fps=fps) for segment in segments]
    delta_values = [abs(item["endpoint_delta_seconds"]) for item in anchors]
    if max(delta_values, default=0.0) > 1e-6:
        raise SystemExit(f"R2 endpoint anchors disagree: max delta {max(delta_values)}")
    samples = []
    for anchor in anchors:
        samples.append(ClockSample(anchor["start_anchor"]["source_time"], anchor["start_anchor"]["platform_epoch"], "recording_start", anchor["segment_id"] or ""))
        samples.append(ClockSample(anchor["coverage_end_anchor"]["source_time"], anchor["coverage_end_anchor"]["platform_epoch"], "recording_coverage_end", anchor["segment_id"] or ""))
    new_fit = fit_clock(samples)
    old_fit = clock_alignment.get("final_recording_fit_v1") or clock_alignment["final_recording_fit"]
    boundary_correction = new_fit.t0 - old_fit["t0_epoch"]
    # Holdout: first, middle, last segments by segment end.
    ordered = sorted(anchors, key=lambda item: item["db_end"])
    holdout_ids = {ordered[0]["segment_id"], ordered[len(ordered) // 2]["segment_id"], ordered[-1]["segment_id"]}
    fit_anchors = [item for item in anchors if item["segment_id"] not in holdout_ids]
    holdout_samples = []
    for item in fit_anchors:
        holdout_samples.append(ClockSample(item["start_anchor"]["source_time"], item["start_anchor"]["platform_epoch"], "holdout_fit_start", item["segment_id"] or ""))
        holdout_samples.append(ClockSample(item["coverage_end_anchor"]["source_time"], item["coverage_end_anchor"]["platform_epoch"], "holdout_fit_end", item["segment_id"] or ""))
    holdout_model = fit_clock(holdout_samples)
    holdout_points = []
    for item in ordered:
        if item["segment_id"] not in holdout_ids:
            continue
        predicted_start = holdout_model.platform_epoch(item["start_anchor"]["source_time"])
        predicted_end = holdout_model.platform_epoch(item["coverage_end_anchor"]["source_time"])
        holdout_points.append({
            "segment_id": item["segment_id"],
            "sample_role": "holdout",
            "source_start": item["source_first_frame"],
            "source_coverage_end": item["source_last_coverage_end"],
            "db_start": item["db_start"],
            "db_end": item["db_end"],
            "predicted_start_epoch": predicted_start,
            "predicted_end_epoch": predicted_end,
            "start_residual_seconds": item["db_start"] - predicted_start,
            "end_residual_seconds": item["db_end"] - predicted_end,
            "resolution_seconds": 1.0 / fps,
        })
    holdout_max = max((abs(p["start_residual_seconds"]) for p in holdout_points), default=0.0)
    holdout_max = max(holdout_max, max((abs(p["end_residual_seconds"]) for p in holdout_points), default=0.0))
    three_point_v2 = {
        "status": "verified" if holdout_max <= 1.0 / fps + 1e-9 else "needs_review",
        "sample_role": "holdout",
        "method": "fit on 11 segments, predict the first/middle/last held-out segments",
        "points": [
            {
                "label": label,
                "segment_id": point["segment_id"],
                "matched_source_time": point["source_coverage_end"],
                "db_epoch": point["db_end"],
                "predicted_epoch": point["predicted_end_epoch"],
                "residual_seconds": point["end_residual_seconds"],
                "resolution_seconds": point["resolution_seconds"],
                "sample_role": "holdout",
            }
            for label, point in zip(("start", "middle", "end"), holdout_points)
        ],
        "numeric_max_abs_residual_seconds": holdout_max,
        "frame_period_seconds": 1.0 / fps,
        "interpretation": "numeric residual at 25 fps frame resolution; not a measurement of physical wall-clock accuracy or end-to-end latency",
    }

    # Calibration: use the same first-frame/coverage-end anchors.
    cal_segments = calibration_alignment["segments"]
    cal_anchors = [clock_segment_anchors(segment, fps=fps) for segment in cal_segments]
    cal_samples = []
    for item in cal_anchors:
        cal_samples.append(ClockSample(item["start_anchor"]["source_time"], item["start_anchor"]["platform_epoch"], "calibration_start", item["segment_id"] or ""))
        cal_samples.append(ClockSample(item["coverage_end_anchor"]["source_time"], item["coverage_end_anchor"]["platform_epoch"], "calibration_coverage_end", item["segment_id"] or ""))
    cal_new_fit = fit_clock(cal_samples)
    latency_used = float(calibration.get("latency_seconds_used_in_injection", calibration.get("latency_seconds", calibration_alignment.get("latency_seconds", 0.0))))
    latency_corrected = float(calibration["snapshot_model_latency_bearing"]["t0_epoch"]) - cal_new_fit.t0
    calibration_v1 = dict(calibration)
    cal_old_model = calibration.get("recording_model_v1", calibration.get("recording_model"))
    calibration.update({
        "latency_seconds_used_in_injection": latency_used,
        "latency_seconds_corrected": latency_corrected,
        "latency_seconds": latency_corrected,
        "latency_correction_seconds": latency_corrected - latency_used,
        "recording_model_v1": calibration.get("recording_model_v1", calibration.get("recording_model")),
        "recording_model_v2": cal_new_fit.as_dict(),
        "recording_model": cal_new_fit.as_dict(),
        "recording_model_anchors": cal_anchors,
        "boundary_correction_seconds": cal_new_fit.t0 - (cal_old_model or {}).get("t0_epoch", cal_new_fit.t0),
        "frame_period_seconds": 1.0 / fps,
        "measurement_resolution_seconds": 1.0 / fps,
        "precision_note": "0.000 s numeric residual is limited by the 25 fps frame period; it is not microsecond physical-clock accuracy.",
    })
    write_json(calibration_path, calibration)
    calibration_alignment.update({
        "recording_model_v1": cal_old_model,
        "recording_model_v2": cal_new_fit.as_dict(),
        "recording_model": cal_new_fit.as_dict(),
        "segment_anchors": cal_anchors,
        "latency_seconds_used_in_injection": latency_used,
        "latency_seconds_corrected": latency_corrected,
        "latency_correction_seconds": latency_corrected - latency_used,
        "frame_period_seconds": 1.0 / fps,
        "precision_note": "Numeric residual at frame resolution; not a physical clock measurement.",
    })
    write_json(round_dir / "alignment" / "calibration_alignment.json", calibration_alignment)

    new_clock = ClockModel(t0=new_fit.t0, drift=new_fit.drift, samples=new_fit.samples, rmse=new_fit.rmse, max_abs_residual=new_fit.max_abs_residual)
    if "final_recording_fit_v1" not in clock_alignment:
        clock_alignment["final_recording_fit_v1"] = old_fit
    clock_alignment["final_recording_fit"] = new_fit.as_dict()
    clock_alignment["final_recording_fit_status"] = "verified" if max(delta_values, default=0.0) <= 1e-6 else "needs_review"
    if "three_point_check_v1" not in clock_alignment:
        clock_alignment["three_point_check_v1"] = clock_alignment.get("three_point_check")
        clock_alignment["three_point_check_v1"]["note"] = "historical fit-internal points; numeric residual is not independent precision"
    clock_alignment["three_point_check"] = three_point_v2
    clock_alignment["holdout_check"] = {
        "status": three_point_v2["status"],
        "holdout_segment_ids": sorted(holdout_ids),
        "fit_segment_count": len(fit_anchors),
        "points": holdout_points,
        "numeric_max_abs_residual_seconds": holdout_max,
        "frame_period_seconds": 1.0 / fps,
    }
    clock_alignment["clock_anchors"] = anchors
    clock_alignment["boundary_correction_seconds"] = boundary_correction
    clock_alignment["frame_period_seconds"] = 1.0 / fps
    clock_alignment["measurement_resolution_seconds"] = 1.0 / fps
    clock_alignment["precision"] = {
        "fit_internal_numeric_residual_seconds": new_fit.max_abs_residual,
        "holdout_numeric_residual_seconds": holdout_max,
        "frame_period_seconds": 1.0 / fps,
        "physical_wall_clock_accuracy_measured": False,
        "end_to_end_alert_latency_measured": False,
        "note": "The 0.000 s numeric residual is a timestamp-fit result at 25 fps resolution; it does not establish microsecond physical accuracy.",
    }
    clock_alignment["calibration"] = calibration
    write_json(clock_alignment_path, clock_alignment)

    # Update every mapping/page/evidence record with corrected event mapping.
    updated_margins = []
    for record in mapping:
        start_epoch = record["platform"]["event_start_epoch"]
        end_epoch = record["platform"]["event_end_epoch"]
        source_start = new_clock.source_time(start_epoch)
        source_end = new_clock.source_time(end_epoch) if end_epoch is not None else None
        old_event_source_start = record["platform"].get("event_source_start")
        old_start_margin = record["platform"].get("start_margin_seconds")
        old_end_margin = record["platform"].get("end_margin_seconds")
        record["platform"]["event_source_start"] = round(source_start, 6)
        record["platform"]["event_source_end"] = None if source_end is None else round(source_end, 6)
        record["platform"]["start_margin_seconds"] = round(record["rule_window_start"] - source_start, 6)
        record["platform"]["end_margin_seconds"] = None if source_end is None else round(source_end - record["rule_window_end"], 6)
        record["platform"]["clock_semantics_version"] = "clock-endpoints-v2"
        record["clock_model_final"] = {
            "t0_epoch": new_fit.t0,
            "drift_source_seconds_per_platform_second": new_fit.drift,
            "samples": new_fit.samples,
            "rmse_seconds": new_fit.rmse,
            "max_abs_residual_seconds": new_fit.max_abs_residual,
        }
        if "submission" in record:
            submission = record["submission"]
            submission["final_t0_epoch"] = new_fit.t0
            if submission.get("inferred_t0_at_submit_epoch") is not None:
                submission["mapping_residual_at_submit_seconds"] = submission["inferred_t0_at_submit_epoch"] - new_fit.t0
            submission["clock_semantics_correction"] = {
                "version": "clock-endpoints-v2",
                "old_final_t0_epoch": old_fit["t0_epoch"],
                "new_final_t0_epoch": new_fit.t0,
                "boundary_correction_seconds": boundary_correction,
                "note": "old model used end_time - last_frame_pts; new model uses start_time - first_frame_pts and end_time - (last_frame_pts + frame_period)",
            }
        updated_margins.append({
            "candidate_id": record["candidate_id"],
            "old_event_source_start": old_event_source_start,
            "new_event_source_start": round(source_start, 6),
            "old_start_margin_seconds": old_start_margin,
            "new_start_margin_seconds": record["platform"]["start_margin_seconds"],
            "old_end_margin_seconds": old_end_margin,
            "new_end_margin_seconds": record["platform"]["end_margin_seconds"],
        })
    mapping_by_id = {record["candidate_id"]: record for record in mapping}
    for record in page.get("candidates", []):
        source = mapping_by_id.get(record["candidate_id"])
        if source:
            record["platform"] = source["platform"]
            record["clock_model_final"] = source["clock_model_final"]
            if "submission" in record and "submission" in source:
                record["submission"] = source["submission"]
    for record in evidence_manifest.get("candidates", []):
        source = mapping_by_id.get(record["candidate_id"])
        if source:
            record["platform"] = source["platform"]
            record["clock_model_final"] = source["clock_model_final"]
            if "submission" in record and "submission" in source:
                record["submission"] = source["submission"]
    page_clock = {
        "t0_epoch": new_fit.t0,
        "drift_source_seconds_per_platform_second": new_fit.drift,
        "drift_ppm": new_fit.as_dict()["drift_ppm"],
        "samples": new_fit.samples,
        "rmse_seconds": new_fit.rmse,
        "max_abs_residual_seconds": new_fit.max_abs_residual,
        "latency_seconds": latency_corrected,
        "latency_seconds_used_in_injection": latency_used,
        "latency_correction_seconds": latency_corrected - latency_used,
        "boundary_correction_seconds": boundary_correction,
        "frame_period_seconds": 1.0 / fps,
        "measurement_resolution_seconds": 1.0 / fps,
        "status": "verified",
        "three_point_check": three_point_v2,
        "holdout_check": clock_alignment["holdout_check"],
        "precision_note": "Numeric residuals at 25 fps frame resolution; not physical clock accuracy or end-to-end latency.",
    }
    page["clock"] = page_clock
    evidence_manifest["clock"] = page_clock
    evidence_manifest["clock_v1"] = {
        "t0_epoch": old_fit["t0_epoch"],
        "samples": old_fit["samples"],
        "latency_seconds": latency_used,
        "note": "historical build; corrected by clock-endpoints-v2",
    }
    evidence_manifest["clock_semantics_correction"] = {
        "version": "clock-endpoints-v2",
        "old_t0_epoch": old_fit["t0_epoch"],
        "new_t0_epoch": new_fit.t0,
        "boundary_correction_seconds": boundary_correction,
        "frame_period_seconds": 1.0 / fps,
        "holdout_numeric_max_abs_residual_seconds": holdout_max,
        "physical_clock_accuracy_measured": False,
        "note": "first-frame and coverage-end anchors replace end_time - last_frame_pts; numeric zero residual is not microsecond precision",
    }
    for clip_item in evidence_manifest.get("clips", []):
        source = mapping_by_id.get(clip_item.get("candidate_id"))
        if source:
            clip_item["coverage_v1"] = clip_item.get("coverage_v1", clip_item.get("coverage"))
            clip_item["coverage"] = source["clip"]["coverage"]
    # Update descriptions with clock correction, no new events.
    clock_description_updates = []
    for record in mapping:
        status, event = client.get_event(record["event_id"])
        if status != 200:
            clock_description_updates.append({"candidate_id": record["candidate_id"], "status": status, "error": "event not found"})
            continue
        description = (event.get("data") or {}).get("description") or ""
        if "correction=clock-endpoints-v2" in description:
            clock_description_updates.append({"candidate_id": record["candidate_id"], "status": 200, "already_present": True})
            continue
        addition = (
            f"; correction=clock-endpoints-v2; final_t0={new_fit.t0:.6f}; "
            f"boundary_correction={boundary_correction:+.3f}s; frame_period={1.0/fps:.3f}s; "
            "numeric_residual_at_frame_resolution_not_physical_clock_accuracy"
        )
        status, body = client.set_description(record["event_id"], description + addition)
        clock_description_updates.append({"candidate_id": record["candidate_id"], "status": status, "body": body, "addition": addition})

    # Persist mapping/page/evidence, submission log, correction records.
    with (round_dir / "candidate_event_mapping.jsonl").open("w", encoding="utf-8") as handle:
        for record in mapping:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    page_path.write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (round_dir / "platform" / "page_dataset_snapshot.json").write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (round_dir / "platform" / "submission_log.jsonl").open("w", encoding="utf-8") as handle:
        for record in mapping:
            if "submission" in record:
                handle.write(json.dumps({
                    "candidate_id": record["candidate_id"],
                    "kind": record["kind"],
                    "ledger_key": record["ledger_key"],
                    "event_id": record.get("event_id"),
                    **record["submission"],
                }, ensure_ascii=False) + "\n")
    write_json(round_dir / "evidence_manifest.json", evidence_manifest)
    write_json(round_dir / "corrections" / "clock_semantics_v2.json", {
        "schema": "unified-clock-semantics-correction-v1",
        "created_at_utc": utc_now(),
        "reason": "R2: first-frame/coverage-end anchors; fit-internal residual is not independent precision.",
        "frame_period_seconds": 1.0 / fps,
        "old_fit": old_fit,
        "new_fit": new_fit.as_dict(),
        "boundary_correction_seconds": boundary_correction,
        "anchors": anchors,
        "holdout_check": clock_alignment["holdout_check"],
        "three_point_check_v2": three_point_v2,
        "calibration": {
            "latency_seconds_used_in_injection": latency_used,
            "latency_seconds_corrected": latency_corrected,
            "latency_correction_seconds": latency_corrected - latency_used,
            "recording_model_v1": cal_old_model,
            "recording_model_v2": cal_new_fit.as_dict(),
        },
        "candidate_margin_corrections": updated_margins,
        "description_updates": clock_description_updates,
        "event_ids_unchanged": True,
    })
    # Replay manifest correction block.
    manifest_path = round_dir / "replay_manifest.json"
    manifest = read_json(manifest_path)
    manifest["clock_semantics_correction"] = {
        "version": "clock-endpoints-v2",
        "old_t0_epoch": old_fit["t0_epoch"],
        "new_t0_epoch": new_fit.t0,
        "boundary_correction_seconds": boundary_correction,
        "frame_period_seconds": 1.0 / fps,
        "measurement_resolution_seconds": 1.0 / fps,
        "holdout_numeric_max_abs_residual_seconds": holdout_max,
        "physical_clock_accuracy_measured": False,
        "note": "the 0.000 s numeric residual is limited by 25 fps frame resolution; not microsecond physical accuracy",
    }
    write_json(manifest_path, manifest)
    # Summary.
    write_json(corrections_dir / "correction_summary.json", {
        "schema": "unified-platform-r1-r2-correction-summary-v1",
        "created_at_utc": utc_now(),
        "r1_stay_candidates": len(stay_corrections),
        "r1_changed_starts": sum(1 for item in stay_corrections if abs(item["causal_window_start"] - item["old_rule_window_start"]) > 1e-6),
        "r1_changed_ends": sum(1 for item in stay_corrections if abs(item["causal_window_end"] - item["old_rule_window_end"]) > 1e-6),
        "r1_all_condition_ok": all(item["window_v2"]["condition_ok"] for item in stay_corrections),
        "r1_all_coverage_verified": all(item["coverage_v2"]["status"] == "verified" for item in stay_corrections),
        "r2_old_t0": old_fit["t0_epoch"],
        "r2_new_t0": new_fit.t0,
        "r2_boundary_correction_seconds": boundary_correction,
        "r2_final_samples": new_fit.samples,
        "r2_holdout_numeric_max_abs_residual_seconds": holdout_max,
        "r2_frame_period_seconds": 1.0 / fps,
        "r2_physical_clock_accuracy_measured": False,
        "event_ids_unchanged": True,
        "ledger_identity_unchanged": True,
        "page_clock": page_clock,
    })
    print(json.dumps({
        "r1_stay_candidates": len(stay_corrections),
        "r1_changed_starts": sum(1 for item in stay_corrections if abs(item["causal_window_start"] - item["old_rule_window_start"]) > 1e-6),
        "r1_changed_ends": sum(1 for item in stay_corrections if abs(item["causal_window_end"] - item["old_rule_window_end"]) > 1e-6),
        "r1_all_condition_ok": all(item["window_v2"]["condition_ok"] for item in stay_corrections),
        "r1_all_coverage_verified": all(item["coverage_v2"]["status"] == "verified" for item in stay_corrections),
        "r2_old_t0": old_fit["t0_epoch"],
        "r2_new_t0": new_fit.t0,
        "r2_boundary_correction_seconds": boundary_correction,
        "r2_final_samples": new_fit.samples,
        "r2_holdout_max": holdout_max,
        "latency_used": latency_used,
        "latency_corrected": latency_corrected,
        "stay_rows": [
            {
                "candidate_id": item["candidate_id"],
                "track_id": item["track_id"],
                "old_start": item["old_rule_window_start"],
                "new_start": item["causal_window_start"],
                "new_end": item["causal_window_end"],
                "span": item["observed_span_seconds"],
                "valid": item["observed_valid_seconds"],
                "gap": item["missing_gap_seconds"],
            }
            for item in stay_corrections
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
