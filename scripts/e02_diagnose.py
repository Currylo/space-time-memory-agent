#!/usr/bin/env python3
"""E02 diagnostic attribution: detection vs tracking coverage on cached outputs.

This is a diagnostic matcher only.  It does not replace TrackEval metrics.
The authoritative IDF1/IDSW/Frag values come from TrackEval.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

PROJECT_DIR = Path(__file__).resolve().parent.parent
HEIGHT_BINS = [
    ("small_lt60", 0.0, 60.0),
    ("medium_60_120", 60.0, 120.0),
    ("large_120_240", 120.0, 240.0),
    ("very_large_ge240", 240.0, math.inf),
]
VISIBILITY_BINS = [
    ("low_lt0.2", 0.0, 0.2),
    ("medium_0.2_0.5", 0.2, 0.5),
    ("high_0.5_0.8", 0.5, 0.8),
    ("very_high_ge0.8", 0.8, 1.0000001),
]
BORDER_FRACTION = 0.10
MATCH_IOU = 0.5
PARTIAL_IOU = 0.1
DISTRACTOR_CLASSES = {2, 7, 8, 12}


def iou_xyxy(a, b) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def load_gt(path: Path) -> dict[int, list[dict]]:
    data = defaultdict(list)
    for row in csv.reader(path.open("r", encoding="utf-8"), delimiter=","):
        if len(row) < 9:
            continue
        frame, track_id = int(row[0]), int(row[1])
        x, y, w, h = map(float, row[2:6])
        conf, cls, visibility = float(row[6]), int(row[7]), float(row[8])
        if w <= 0 or h <= 0:
            continue
        data[frame].append({
            "id": track_id,
            "box": [x, y, x + w, y + h],
            "w": w,
            "h": h,
            "conf": conf,
            "cls": cls,
            "visibility": visibility,
        })
    return data


def load_detections(path: Path) -> dict[int, list[dict]]:
    data = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        frame = int(row["frame_index"]) + 1
        for box, score, cls in zip(row["bbox_xyxy"], row["score"], row["class_id"]):
            if int(cls) == 0 and float(score) >= 0.1:
                data[frame].append({"box": list(map(float, box)), "score": float(score)})
    return data


def load_tracks(path: Path) -> dict[int, list[dict]]:
    data = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = line.split(",")
        frame, track_id = int(row[0]), int(row[1])
        x, y, w, h = map(float, row[2:6])
        if w > 0 and h > 0:
            data[frame].append({"id": track_id, "box": [x, y, x + w, y + h], "score": float(row[6])})
    return data


def preprocess_items(items: list[dict], gt_rows: list[dict]) -> list[dict]:
    """Remove detections/tracks matched to distractor GT, mirroring TrackEval preproc."""
    if not items or not gt_rows:
        return items
    scores = np.zeros((len(items), len(gt_rows)))
    for i, item in enumerate(items):
        for j, gt in enumerate(gt_rows):
            scores[i, j] = iou_xyxy(item["box"], gt["box"])
    rows, cols = linear_sum_assignment(-scores)
    remove = set()
    for i, j in zip(rows, cols):
        if scores[i, j] >= MATCH_IOU and gt_rows[j]["cls"] in DISTRACTOR_CLASSES:
            remove.add(i)
    return [item for i, item in enumerate(items) if i not in remove]


def match_to_gt(pedestrian: list[dict], items: list[dict]) -> tuple[dict[int, dict], dict[int, int]]:
    """One-to-one IoU matching for diagnostic attribution only."""

    if not pedestrian or not items:
        return {}, {}
    scores = np.zeros((len(pedestrian), len(items)))
    for i, gt in enumerate(pedestrian):
        for j, item in enumerate(items):
            scores[i, j] = iou_xyxy(gt["box"], item["box"])
    rows, cols = linear_sum_assignment(-scores)
    gt_to_item = {}
    item_to_gt = {}
    for i, j in zip(rows, cols):
        if scores[i, j] >= MATCH_IOU:
            gt_to_item[pedestrian[i]["id"]] = items[j]
            item_to_gt[j] = pedestrian[i]["id"]
    return gt_to_item, item_to_gt


def score_bin(score: float) -> str:
    for lower, label in (
        (0.7, "ge0.70"),
        (0.5, "0.50_0.70"),
        (0.4, "0.40_0.50"),
        (0.3, "0.30_0.40"),
        (0.25, "0.25_0.30"),
        (0.20, "0.20_0.25"),
        (0.15, "0.15_0.20"),
        (0.10, "0.10_0.15"),
    ):
        if score >= lower:
            return label
    return "lt0.10"


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="E02 detection/tracking coverage diagnosis")
    parser.add_argument("--gt", type=Path, default=PROJECT_DIR / "data/e02/raw/MOT17-02-FRCNN/gt/gt.txt")
    parser.add_argument("--detections", type=Path, default=PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001/detections.jsonl")
    parser.add_argument("--tracks", type=Path, default=PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001/predictions/MOT17-02-FRCNN.txt")
    parser.add_argument("--images", type=Path, default=PROJECT_DIR / "data/e02/raw/MOT17-02-FRCNN/img1")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "reports/e02/diagnosis")
    args = parser.parse_args()

    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    gt_all = load_gt(args.gt)
    detections = load_detections(args.detections)
    tracks = load_tracks(args.tracks)

    gt_boxes = []
    coverage = Counter()
    det_scores_for_gt = defaultdict(list)
    track_scores_for_gt = defaultdict(list)
    best_det_scores = []
    best_track_scores = []
    det_emit_total = Counter()
    det_emit_ok = Counter()
    all_det_scores = []
    matched_det_scores = []
    matched_gt_scores = []
    per_frame_counts = []

    for frame in range(1, 601):
        gt_rows = gt_all.get(frame, [])
        det_rows = preprocess_items(detections.get(frame, []), gt_rows)
        track_rows = preprocess_items(tracks.get(frame, []), gt_rows)
        pedestrian = [row for row in gt_rows if row["cls"] == 1 and row["conf"] > 0]
        det_matches, det_item_to_gt = match_to_gt(pedestrian, det_rows)
        track_matches, track_item_to_gt = match_to_gt(pedestrian, track_rows)
        per_frame_counts.append({
            "frame": frame,
            "gt_pedestrian": len(pedestrian),
            "raw_detections_after_distractor_preproc": len(det_rows),
            "tracker_outputs_after_distractor_preproc": len(track_rows),
        })
        for gt in pedestrian:
            best_det_iou = 0.0
            best_det_item = None
            best_det_index = -1
            for index, item in enumerate(det_rows):
                value = iou_xyxy(item["box"], gt["box"])
                if value > best_det_iou:
                    best_det_iou = value
                    best_det_item = item
                    best_det_index = index
            best_track_iou = 0.0
            best_track_item = None
            best_track_index = -1
            for index, item in enumerate(track_rows):
                value = iou_xyxy(item["box"], gt["box"])
                if value > best_track_iou:
                    best_track_iou = value
                    best_track_item = item
                    best_track_index = index
            det_assigned_item = det_matches.get(gt["id"])
            track_assigned_item = track_matches.get(gt["id"])
            det_assigned = det_assigned_item is not None
            track_assigned = track_assigned_item is not None
            det_overlap = best_det_iou >= MATCH_IOU
            track_overlap = best_track_iou >= MATCH_IOU
            if det_assigned and track_assigned:
                category = "assigned_both"
            elif det_assigned and not track_assigned:
                category = "det_assigned_not_tracked"
            elif track_assigned:
                category = "track_assigned_det_not_matched"
            elif det_overlap:
                category = "det_overlap_not_assigned"
            elif best_det_iou >= PARTIAL_IOU:
                category = "partial_overlap_below_iou"
            else:
                category = "no_detection_overlap"
            coverage[category] += 1
            gt_boxes.append({
                "frame": frame,
                "gt_id": gt["id"],
                "height": gt["h"],
                "width": gt["w"],
                "area": gt["w"] * gt["h"],
                "visibility": gt["visibility"],
                "center_x_ratio": ((gt["box"][0] + gt["box"][2]) / 2.0) / 1920.0,
                "center_y_ratio": ((gt["box"][1] + gt["box"][3]) / 2.0) / 1080.0,
                "best_det_iou": best_det_iou,
                "best_track_iou": best_track_iou,
                "best_det_score": best_det_item["score"] if best_det_item else None,
                "best_track_score": best_track_item["score"] if best_track_item else None,
                "best_track_id": best_track_item["id"] if best_track_item else None,
                "best_det_assigned_gt_id": det_item_to_gt.get(best_det_index),
                "best_track_assigned_gt_id": track_item_to_gt.get(best_track_index),
                "det_assigned": det_assigned,
                "track_assigned": track_assigned,
                "category": category,
            })
            if best_det_item:
                det_scores_for_gt[category].append(best_det_item["score"])
            if best_track_item:
                track_scores_for_gt[category].append(best_track_item["score"])
            best_det_scores.append(best_det_iou)
            best_track_scores.append(best_track_iou)
            if best_det_item and best_det_iou >= PARTIAL_IOU:
                matched_gt_scores.append(best_det_item["score"])

        for item in det_rows:
            all_det_scores.append(item["score"])
            key = score_bin(item["score"])
            det_emit_total[key] += 1
            if any(iou_xyxy(item["box"], track["box"]) >= MATCH_IOU for track in track_rows):
                det_emit_ok[key] += 1
                matched_det_scores.append(item["score"])

    total_gt = len(gt_boxes)
    total_det = len(all_det_scores)
    assigned_both = coverage["assigned_both"]
    det_assigned_not_tracked = coverage["det_assigned_not_tracked"]
    det_overlap_not_assigned = coverage["det_overlap_not_assigned"]
    det_overlap_not_tracked = det_assigned_not_tracked + det_overlap_not_assigned
    det_assigned_total = assigned_both + det_assigned_not_tracked
    det_overlap_total = det_assigned_total + det_overlap_not_assigned
    track_assigned_total = assigned_both + coverage["track_assigned_det_not_matched"]
    coverage_summary = {
        "valid_pedestrian_gt_boxes": total_gt,
        "raw_detections_after_distractor_preproc": total_det,
        "tracker_outputs_after_distractor_preproc": sum(item["tracker_outputs_after_distractor_preproc"] for item in per_frame_counts),
        "assigned_both": assigned_both,
        "det_assigned_not_tracked": det_assigned_not_tracked,
        "det_overlap_not_assigned": det_overlap_not_assigned,
        "track_assigned_det_not_matched": coverage["track_assigned_det_not_matched"],
        "partial_overlap_below_iou": coverage["partial_overlap_below_iou"],
        "no_detection_overlap": coverage["no_detection_overlap"],
        "detection_assigned_coverage_iou_0_5": round(safe_div(det_assigned_total, total_gt), 4),
        "detection_overlap_coverage_iou_0_5": round(safe_div(det_overlap_total, total_gt), 4),
        "tracking_assigned_coverage_iou_0_5": round(safe_div(track_assigned_total, total_gt), 4),
        "det_overlap_not_tracked": det_overlap_not_tracked,
        "det_overlap_not_tracked_rate": round(safe_div(det_overlap_not_tracked, total_gt), 4),
        "det_assigned_not_tracked_rate": round(safe_div(det_assigned_not_tracked, total_gt), 4),
        "no_detection_rate": round(safe_div(coverage["no_detection_overlap"], total_gt), 4),
        "partial_overlap_rate": round(safe_div(coverage["partial_overlap_below_iou"], total_gt), 4),
        "mean_best_det_iou": round(float(np.mean(best_det_scores)), 4),
        "mean_best_track_iou": round(float(np.mean(best_track_scores)), 4),
        "median_best_det_iou": round(float(np.median(best_det_scores)), 4),
        "median_best_track_iou": round(float(np.median(best_track_scores)), 4),
        "matching_note": "assigned = one-to-one IoU>=0.5 diagnostic matching; overlap = any box IoU>=0.5 without assignment. TrackEval remains authoritative.",
    }

    # Detection confidence -> output emission.
    confidence_bins = []
    for key in ("0.10_0.15", "0.15_0.20", "0.20_0.25", "0.25_0.30", "0.30_0.40", "0.40_0.50", "0.50_0.70", "ge0.70"):
        total = det_emit_total[key]
        emitted = det_emit_ok[key]
        confidence_bins.append({
            "score_bin": key,
            "detections": total,
            "emitted_as_tracker_output": emitted,
            "emission_rate": round(safe_div(emitted, total), 4),
        })

    # Stratified tables.
    def summarize_subset(subset: list[dict]) -> dict:
        n = len(subset)
        assigned_both = sum(1 for item in subset if item["category"] == "assigned_both")
        det_assigned_not_tracked = sum(1 for item in subset if item["category"] == "det_assigned_not_tracked")
        det_overlap_not_assigned = sum(1 for item in subset if item["category"] == "det_overlap_not_assigned")
        track_assigned = sum(1 for item in subset if item["track_assigned"])
        det_assigned = sum(1 for item in subset if item["det_assigned"])
        det_overlap = sum(1 for item in subset if item["best_det_iou"] >= MATCH_IOU)
        return {
            "gt_boxes": n,
            "detection_assigned_coverage": round(safe_div(det_assigned, n), 4),
            "detection_overlap_coverage": round(safe_div(det_overlap, n), 4),
            "tracking_assigned_coverage": round(safe_div(track_assigned, n), 4),
            "det_assigned_not_tracked": det_assigned_not_tracked,
            "det_overlap_not_assigned": det_overlap_not_assigned,
            "partial_overlap_below_iou": sum(1 for item in subset if item["category"] == "partial_overlap_below_iou"),
            "no_detection_overlap": sum(1 for item in subset if item["category"] == "no_detection_overlap"),
            "mean_best_det_iou": round(float(np.mean([item["best_det_iou"] for item in subset])) if subset else 0.0, 4),
        }

    def stratify(field: str, bins: list[tuple[str, float, float]]) -> list[dict]:
        rows = []
        for label, lower, upper in bins:
            subset = [item for item in gt_boxes if lower <= item[field] < upper]
            rows.append({"bin": label, **summarize_subset(subset)})
        return rows

    height_rows = stratify("height", HEIGHT_BINS)
    visibility_rows = stratify("visibility", VISIBILITY_BINS)
    horizontal_rows = []
    for label, lower, upper in (("left_third", 0.0, 1 / 3), ("middle_third", 1 / 3, 2 / 3), ("right_third", 2 / 3, 1.0000001)):
        subset = [item for item in gt_boxes if lower <= item["center_x_ratio"] < upper]
        horizontal_rows.append({"bin": label, **summarize_subset(subset)})
    border = [
        item for item in gt_boxes
        if item["center_x_ratio"] < BORDER_FRACTION
        or item["center_x_ratio"] > 1 - BORDER_FRACTION
        or item["center_y_ratio"] < BORDER_FRACTION
        or item["center_y_ratio"] > 1 - BORDER_FRACTION
    ]
    interior = [item for item in gt_boxes if item not in border]
    border_rows = []
    for label, subset in (("border_10pct", border), ("interior_90pct", interior)):
        border_rows.append({"bin": label, **summarize_subset(subset)})

    # Detection-overlap-but-not-tracked by best detection score relative to
    # ByteTrack's 0.25 new-track activation threshold.
    det_not_tracked = [
        item for item in gt_boxes
        if item["best_det_iou"] >= MATCH_IOU and not item["track_assigned"]
    ]
    det_not_tracked_low = [item for item in det_not_tracked if item["best_det_score"] is not None and item["best_det_score"] < 0.25]
    det_not_tracked_high = [item for item in det_not_tracked if item["best_det_score"] is not None and item["best_det_score"] >= 0.25]

    def case_for(gt_id: int, frames: list[int]) -> dict:
        rows = []
        for frame in frames:
            matches = [item for item in gt_boxes if item["gt_id"] == gt_id and item["frame"] == frame]
            rows.append(matches[0] if matches else {"gt_id": gt_id, "frame": frame, "missing": True})
        return {"gt_id": gt_id, "frames": rows}

    cases = {
        "gt3_frame335": case_for(3, [335]),
        "gt33_frames548_549": case_for(33, [548, 549]),
        "gt36_frames329_380_431": case_for(36, [329, 380, 431]),
    }

    diagnosis = {
        "schema": "e02-diagnosis-v1",
        "diagnostic_only": True,
        "metric_source": "TrackEval only; this file uses custom per-frame IoU matching for error attribution, not metric reporting.",
        "matching": {
            "iou_match": MATCH_IOU,
            "partial_iou_lower": PARTIAL_IOU,
            "distractor_classes": sorted(DISTRACTOR_CLASSES),
            "gt_class": 1,
            "gt_conf_positive": True,
        },
        "stratification_boundaries": {
            "height_px": ["<60", "60-120", "120-240", ">=240"],
            "visibility": ["<0.2", "0.2-0.5", "0.5-0.8", ">=0.8"],
            "horizontal": ["left third", "middle third", "right third"],
            "border": "center point within 10% of any image edge; otherwise interior",
        },
        "coverage": coverage_summary,
        "confidence": {
            "raw_detection_count": len(all_det_scores),
            "raw_score_percentiles": {
                "p10": round(float(np.percentile(all_det_scores, 10)), 4),
                "p25": round(float(np.percentile(all_det_scores, 25)), 4),
                "p50": round(float(np.percentile(all_det_scores, 50)), 4),
                "p75": round(float(np.percentile(all_det_scores, 75)), 4),
                "p90": round(float(np.percentile(all_det_scores, 90)), 4),
            },
            "bins": confidence_bins,
            "matched_gt_best_score_percentiles": {
                "p10": round(float(np.percentile(matched_gt_scores, 10)), 4) if matched_gt_scores else None,
                "p50": round(float(np.percentile(matched_gt_scores, 50)), 4) if matched_gt_scores else None,
                "p90": round(float(np.percentile(matched_gt_scores, 90)), 4) if matched_gt_scores else None,
            },
            "detected_not_tracked_best_score": {
                "count": len(det_not_tracked),
                "below_activation_0_25": len(det_not_tracked_low),
                "at_or_above_activation_0_25": len(det_not_tracked_high),
                "score_percentiles": {
                    "p10": round(float(np.percentile([item["best_det_score"] for item in det_not_tracked], 10)), 4) if det_not_tracked else None,
                    "p50": round(float(np.percentile([item["best_det_score"] for item in det_not_tracked], 50)), 4) if det_not_tracked else None,
                    "p90": round(float(np.percentile([item["best_det_score"] for item in det_not_tracked], 90)), 4) if det_not_tracked else None,
                },
            },
        },
        "stratified": {
            "by_height": height_rows,
            "by_visibility": visibility_rows,
            "by_horizontal_region": horizontal_rows,
            "by_border": border_rows,
        },
        "cases": cases,
        "per_frame_counts": per_frame_counts,
    }
    (args.output / "diagnosis.json").write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Render case panels: GT green, raw detections yellow, tracker outputs orange.
    def draw_case(frame: int, gt_id: int, crop: bool = True) -> np.ndarray:
        image = cv2.imread(str(args.images / f"{frame:06d}.jpg"))
        if image is None:
            raise RuntimeError(f"missing image {frame}")
        gt_rows = gt_all.get(frame, [])
        det_rows = preprocess_items(detections.get(frame, []), gt_rows)
        track_rows = preprocess_items(tracks.get(frame, []), gt_rows)
        focus = next((row for row in gt_rows if row["id"] == gt_id and row["cls"] == 1 and row["conf"] > 0), None)
        if focus is None:
            raise RuntimeError(f"GT {gt_id} not found at frame {frame}")
        x1, y1, x2, y2 = focus["box"]
        if crop:
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            width = max(320.0, (x2 - x1) * 1.8)
            height = max(380.0, (y2 - y1) * 1.35)
            left = max(0, int(cx - width / 2))
            right = min(image.shape[1], int(cx + width / 2))
            top = max(0, int(cy - height / 2))
            bottom = min(image.shape[0], int(cy + height / 2))
            image = image[top:bottom, left:right].copy()
            ox, oy = left, top
        else:
            ox, oy = 0, 0
        for row in gt_rows:
            if row["cls"] != 1 or row["conf"] <= 0:
                continue
            box = row["box"]
            a = (int(box[0] - ox), int(box[1] - oy), int(box[2] - ox), int(box[3] - oy))
            color = (0, 255, 0) if row["id"] == gt_id else (0, 120, 0)
            cv2.rectangle(image, a[:2], a[2:], color, 4 if row["id"] == gt_id else 1)
            if row["id"] == gt_id:
                cv2.putText(image, f"GT {gt_id}", (a[0], max(26, a[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        for item in det_rows:
            box = item["box"]
            if iou_xyxy(box, focus["box"]) < 0.05:
                continue
            a = (int(box[0] - ox), int(box[1] - oy), int(box[2] - ox), int(box[3] - oy))
            cv2.rectangle(image, a[:2], a[2:], (0, 255, 255), 2)
            cv2.putText(image, f"D {item['score']:.2f}", (a[0], max(22, a[1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
        for item in track_rows:
            box = item["box"]
            if iou_xyxy(box, focus["box"]) < 0.05:
                continue
            a = (int(box[0] - ox), int(box[1] - oy), int(box[2] - ox), int(box[3] - oy))
            cv2.rectangle(image, a[:2], a[2:], (255, 120, 0), 2)
            cv2.putText(image, f"T {item['id']} {item['score']:.2f}", (a[0], max(22, a[1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 120, 0), 1)
        cv2.putText(image, f"frame {frame}  GT {gt_id}", (10, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        return image

    case_dir = args.output / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    case_files = {}
    for name, gt_id, frames in (
        ("gt3-335", 3, [335]),
        ("gt33-548-549", 33, [548, 549]),
        ("gt36-329-380-431", 36, [329, 380, 431]),
    ):
        panels = [draw_case(frame, gt_id) for frame in frames]
        for index, panel in enumerate(panels):
            path = case_dir / f"{name}-{index + 1}.jpg"
            cv2.imwrite(str(path), panel)
        if len(panels) == 3:
            sheet = cv2.hconcat([cv2.resize(panel, (640, 480)) for panel in panels])
        elif len(panels) == 2:
            sheet = cv2.hconcat([cv2.resize(panel, (720, 540)) for panel in panels])
        else:
            sheet = cv2.resize(panels[0], (960, 720))
        sheet_path = case_dir / f"{name}.jpg"
        cv2.imwrite(str(sheet_path), sheet)
        case_files[name] = str(sheet_path.relative_to(PROJECT_DIR))

    diagnosis["case_files"] = case_files
    (args.output / "diagnosis.json").write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "coverage": coverage_summary,
        "confidence_bins": confidence_bins,
        "height": height_rows,
        "visibility": visibility_rows,
        "border": border_rows,
        "det_not_tracked": diagnosis["confidence"]["detected_not_tracked_best_score"],
        "case_files": case_files,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
