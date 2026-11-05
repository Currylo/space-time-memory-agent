#!/usr/bin/env python3
"""A2: save representative MOT17 frames with GT and predictions for error review.

This is a diagnostic visualization, not a metric computation.  One frame per
selected sequence is chosen by the largest number of unmatched pedestrian GT
boxes under a one-to-one IoU 0.5 matching.  TrackEval remains authoritative.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN = PROJECT_DIR / "runs/a2-mot17-7seq-yolov8n-byte-001"
DEFAULT_MANIFEST = PROJECT_DIR / "data/a2/mot17/sequences_manifest.json"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/a2/mot17/cases"
MATCH_IOU = 0.5
DISTRACTOR_CLASSES = {2, 4, 7, 8, 9, 12}


def iou(a, b) -> float:
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
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
        conf, cls = float(row[6]), int(row[7])
        if w <= 0 or h <= 0 or conf <= 0 or cls != 1:
            continue
        data[frame].append({"id": track_id, "box": [x, y, x + w, y + h], "cls": cls})
    return data


def load_predictions(path: Path) -> dict[int, list[dict]]:
    data = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = line.split(",")
        frame, track_id = int(row[0]), int(row[1])
        x, y, w, h = map(float, row[2:6])
        if w > 0 and h > 0:
            data[frame].append({"id": track_id, "box": [x, y, x + w, y + h]})
    return data


def match(gt_rows, pred_rows) -> tuple[int, set[int], set[int]]:
    if not gt_rows or not pred_rows:
        return 0, set(), set(range(len(pred_rows)))
    scores = np.zeros((len(gt_rows), len(pred_rows)))
    for i, gt in enumerate(gt_rows):
        for j, pred in enumerate(pred_rows):
            scores[i, j] = iou(gt["box"], pred["box"])
    rows, cols = linear_sum_assignment(-scores)
    matched_gt = set()
    matched_pred = set()
    for i, j in zip(rows, cols):
        if scores[i, j] >= MATCH_IOU:
            matched_gt.add(i)
            matched_pred.add(j)
    return len(matched_gt), matched_gt, matched_pred


def draw_frame(image_path: Path, gt_rows, pred_rows, matched_gt, matched_pred, sequence, frame, output: Path, scale_x, scale_y):
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"cannot read {image_path}")
    height, width = image.shape[:2]
    for index, gt in enumerate(gt_rows):
        x1, y1, x2, y2 = gt["box"]
        x1, x2 = int(x1 / scale_x), int(x2 / scale_x)
        y1, y2 = int(y1 / scale_y), int(y2 / scale_y)
        color = (0, 200, 0) if index in matched_gt else (0, 0, 255)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 1)
        cv2.putText(image, f"G{gt['id']}", (x1, max(10, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    for index, pred in enumerate(pred_rows):
        x1, y1, x2, y2 = pred["box"]
        x1, x2 = int(x1 / scale_x), int(x2 / scale_x)
        y1, y2 = int(y1 / scale_y), int(y2 / scale_y)
        color = (255, 160, 0) if index in matched_pred else (255, 0, 255)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 1)
        cv2.putText(image, f"P{pred['id']}", (x1, min(height - 3, y2 + 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    fn = len(gt_rows) - len(matched_gt)
    fp = len(pred_rows) - len(matched_pred)
    text = f"{sequence} frame {frame} GT={len(gt_rows)} matched={len(matched_gt)} FN={fn} FP={fp}"
    cv2.rectangle(image, (5, 5), (min(width - 5, 520), 28), (0, 0, 0), -1)
    cv2.putText(image, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), image)
    return {"sequence": sequence, "frame": frame, "gt": len(gt_rows), "matched": len(matched_gt), "fn": fn, "fp": fp, "image": str(output.relative_to(PROJECT_DIR))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Save representative MOT17 error frames")
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sequences", nargs="*", default=["MOT17-13-FRCNN", "MOT17-10-FRCNN", "MOT17-09-FRCNN", "MOT17-05-FRCNN"])
    args = parser.parse_args()
    args.run = args.run.resolve()
    args.manifest = args.manifest.resolve()
    args.output = args.output.resolve()
    run = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    by_name = {item["sequence"]: item for item in manifest["sequences"]}
    results = []
    for sequence in args.sequences:
        sequence_meta = by_name[sequence]
        record = run["sequences"][sequence]
        pred_path = PROJECT_DIR / record["prediction_file"]
        gt_path = PROJECT_DIR / "data/a2/mot17/raw" / sequence / "gt" / "gt.txt"
        image_dir = PROJECT_DIR / "data/a2/mot17/raw" / sequence / "img1"
        gt = load_gt(gt_path)
        pred = load_predictions(pred_path)
        best = None
        for frame in sorted(gt):
            gt_rows = gt[frame]
            pred_rows = pred.get(frame, [])
            matched, matched_gt, matched_pred = match(gt_rows, pred_rows)
            score = (len(gt_rows) - matched, len(pred_rows) - len(matched_pred))
            if best is None or score > best[0]:
                best = (score, frame, gt_rows, pred_rows, matched_gt, matched_pred)
        if best is None:
            continue
        _, frame, gt_rows, pred_rows, matched_gt, matched_pred = best
        image_path = image_dir / f"{frame:06d}.jpg"
        output = args.output / f"{sequence}_frame_{frame:06d}.jpg"
        result = draw_frame(
            image_path, gt_rows, pred_rows, matched_gt, matched_pred, sequence, frame, output,
            sequence_meta["frame_scale_x"], sequence_meta["frame_scale_y"],
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "cases.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
