#!/usr/bin/env python3
"""Select and render representative E02 tracking error cases.

This is a diagnostic matching helper only.  The authoritative metrics are
produced by TrackEval (IDF1, IDSW, Frag, etc.).  Here we use IoU >= 0.5
Hungarian matching solely to find representative ID-switch, miss and fragment
frames for visual review.  No metric is recomputed or reported from this file.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

PROJECT_DIR = Path(__file__).resolve().parent.parent


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


def load_gt(path: Path) -> dict[int, list[tuple[int, list[float]]]]:
    data: dict[int, list[tuple[int, list[float]]]] = defaultdict(list)
    for row in csv.reader(path.open("r", encoding="utf-8"), delimiter=","):
        if len(row) < 9:
            continue
        frame, track_id = int(row[0]), int(row[1])
        x, y, w, h = map(float, row[2:6])
        conf, cls, visibility = float(row[6]), int(row[7]), float(row[8])
        if cls == 1 and conf > 0 and w > 0 and h > 0:
            data[frame].append((track_id, [x, y, x + w, y + h]))
    return data


def load_predictions(path: Path) -> dict[int, list[tuple[int, list[float], float]]]:
    data: dict[int, list[tuple[int, list[float], float]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = line.split(",")
        frame, track_id = int(row[0]), int(row[1])
        x, y, w, h = map(float, row[2:6])
        score = float(row[6])
        if w > 0 and h > 0:
            data[frame].append((track_id, [x, y, x + w, y + h], score))
    return data


def load_raw_detections(path: Path) -> dict[int, list[tuple[list[float], float]]]:
    data: dict[int, list[tuple[list[float], float]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        frame = int(row["frame_index"]) + 1
        for box, score, cls in zip(row["bbox_xyxy"], row["score"], row["class_id"]):
            if int(cls) == 0 and float(score) >= 0.1:
                data[frame].append((list(map(float, box)), float(score)))
    return data


def match_frames(gt, predictions, threshold: float) -> dict[int, dict[int, int]]:
    matched: dict[int, dict[int, int]] = defaultdict(dict)
    for frame in sorted(set(gt) | set(predictions)):
        gt_rows = gt.get(frame, [])
        pred_rows = predictions.get(frame, [])
        if not gt_rows or not pred_rows:
            continue
        scores = np.zeros((len(gt_rows), len(pred_rows)))
        for i, (_, gt_box) in enumerate(gt_rows):
            for j, (_, pred_box, _) in enumerate(pred_rows):
                scores[i, j] = iou_xyxy(gt_box, pred_box)
        rows, cols = linear_sum_assignment(-scores)
        for i, j in zip(rows, cols):
            if scores[i, j] >= threshold:
                matched[gt_rows[i][0]][frame] = pred_rows[j][0]
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description="Render E02 MOT17 tracking error cases")
    parser.add_argument("--gt", type=Path, default=PROJECT_DIR / "data/e02/raw/MOT17-02-FRCNN/gt/gt.txt")
    parser.add_argument("--predictions", type=Path, default=PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001/predictions/MOT17-02-FRCNN.txt")
    parser.add_argument("--detections", type=Path, default=PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001/detections.jsonl")
    parser.add_argument("--images", type=Path, default=PROJECT_DIR / "data/e02/raw/MOT17-02-FRCNN/img1")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "reports/e02/cases")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    gt = load_gt(args.gt)
    predictions = load_predictions(args.predictions)
    detections = load_raw_detections(args.detections)
    matched = match_frames(gt, predictions, args.threshold)

    gt_ids = sorted({track_id for rows in gt.values() for track_id, _ in rows})
    gt_box_stats = {}
    for track_id in gt_ids:
        boxes = [box for rows in gt.values() for gid, box in rows if gid == track_id]
        areas = [(box[2] - box[0]) * (box[3] - box[1]) for box in boxes]
        gt_box_stats[track_id] = {
            "mean_area": sum(areas) / len(areas),
            "max_area": max(areas),
            "visible_frames": len(boxes),
        }

    track_match_counts = defaultdict(int)
    for track_id, frame_map in matched.items():
        for pred_id in frame_map.values():
            track_match_counts[pred_id] += 1

    switches = []
    misses = []
    fragments = []
    for gt_id in gt_ids:
        frames = sorted(frame for frame in gt if any(gid == gt_id for gid, _ in gt[frame]))
        gt_match = {frame: matched[gt_id][frame] for frame in frames if frame in matched.get(gt_id, {})}
        matched_frames = sorted(gt_match)
        for before, after in zip(matched_frames, matched_frames[1:]):
            if after - before <= 5 and gt_match[before] != gt_match[after]:
                old_id, new_id = gt_match[before], gt_match[after]
                switches.append({
                    "gt_id": gt_id,
                    "before_frame": before,
                    "after_frame": after,
                    "old_tracker_id": old_id,
                    "new_tracker_id": new_id,
                    "gap_frames": after - before - 1,
                    "score": min(track_match_counts[old_id], track_match_counts[new_id]),
                })

        def best_det_iou(frame: int) -> float:
            rows = gt[frame]
            gt_box = next(box for gid, box in rows if gid == gt_id)
            return max((iou_xyxy(box, gt_box) for box, _ in detections.get(frame, [])), default=0.0)

        run = []
        for frame in frames + [None]:
            if frame is not None and frame not in gt_match and best_det_iou(frame) < 0.5:
                run.append(frame)
            else:
                if len(run) >= 10:
                    midpoint = run[len(run) // 2]
                    misses.append({
                        "gt_id": gt_id,
                        "start_frame": run[0],
                        "end_frame": run[-1],
                        "length": len(run),
                        "best_det_iou_mid": round(best_det_iou(midpoint), 4),
                        "score": len(run) * math.sqrt(gt_box_stats[gt_id]["max_area"]),
                    })
                run = []

        segments = []
        current = []
        for frame in frames:
            if frame in gt_match:
                if current and frame - current[-1] > 1:
                    if frame - current[-1] - 1 >= 5:
                        segments.append(current)
                        current = []
                current.append(frame)
        if current:
            segments.append(current)
        for before, after in zip(segments, segments[1:]):
            gap = after[0] - before[-1] - 1
            if gap >= 5:
                fragments.append({
                    "gt_id": gt_id,
                    "match_before_frame": before[-1],
                    "match_after_frame": after[0],
                    "gap_frames": gap,
                    "score": gap * math.sqrt(gt_box_stats[gt_id]["max_area"]),
                })

    switches.sort(key=lambda item: (item["score"], -item["gap_frames"]), reverse=True)
    misses.sort(key=lambda item: item["score"], reverse=True)
    fragments.sort(key=lambda item: item["score"], reverse=True)
    selected_switch = switches[0]
    selected_miss = misses[0]
    # Prefer a fragment case from a different GT track than the selected miss,
    # so the report shows two independent error mechanisms rather than the
    # same long gap twice.
    # Reviewed selection: keep GT36's 329→431 interruption as the fragment
    # example because the requested case review uses that exact interval.
    preferred_fragment = next(
        (
            item for item in fragments
            if item["gt_id"] == 36 and item["match_before_frame"] == 329 and item["match_after_frame"] == 431
        ),
        None,
    )
    independent_fragments = [item for item in fragments if item["gt_id"] != selected_miss["gt_id"]]
    selected_fragment = preferred_fragment or (independent_fragments or fragments)[0]

    def draw(
        frame: int,
        gt_id: int,
        crop: bool = True,
        focus_pred_ids: list[int] | None = None,
    ) -> np.ndarray:
        image = cv2.imread(str(args.images / f"{frame:06d}.jpg"))
        if image is None:
            raise RuntimeError(f"missing image {frame}")
        focus_box = next(box for gid, box in gt[frame] if gid == gt_id)
        x1, y1, x2, y2 = focus_box
        if crop:
            center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
            width = max(300.0, (x2 - x1) * 1.8)
            height = max(360.0, (y2 - y1) * 1.3)
            left = max(0, int(center_x - width / 2))
            right = min(image.shape[1], int(center_x + width / 2))
            top = max(0, int(center_y - height / 2))
            bottom = min(image.shape[0], int(center_y + height / 2))
            image = image[top:bottom, left:right].copy()
            offset_x, offset_y = left, top
        else:
            offset_x, offset_y = 0, 0
        for gid, box in gt[frame]:
            if box[2] < offset_x or box[0] > offset_x + image.shape[1] or box[3] < offset_y or box[1] > offset_y + image.shape[0]:
                continue
            a = (int(box[0] - offset_x), int(box[1] - offset_y), int(box[2] - offset_x), int(box[3] - offset_y))
            color = (0, 255, 0) if gid == gt_id else (0, 110, 0)
            cv2.rectangle(image, a[:2], a[2:], color, 4 if gid == gt_id else 1)
            if gid == gt_id:
                cv2.putText(image, f"GT {gid}", (a[0], max(26, a[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
        for box, score in detections.get(frame, []):
            a = (int(box[0] - offset_x), int(box[1] - offset_y), int(box[2] - offset_x), int(box[3] - offset_y))
            cv2.rectangle(image, a[:2], a[2:], (0, 255, 255), 1)
            cv2.putText(image, f"D {score:.2f}", (a[0], max(20, a[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        for pid, box, score in predictions.get(frame, []):
            if focus_pred_ids is not None and pid not in focus_pred_ids:
                continue
            if box[2] < offset_x or box[0] > offset_x + image.shape[1] or box[3] < offset_y or box[1] > offset_y + image.shape[0]:
                continue
            a = (int(box[0] - offset_x), int(box[1] - offset_y), int(box[2] - offset_x), int(box[3] - offset_y))
            color = (255, 120, 0)
            if focus_pred_ids:
                color = (0, 255, 255) if pid == focus_pred_ids[0] else (0, 0, 255)
            cv2.rectangle(image, a[:2], a[2:], color, 3 if focus_pred_ids else 2)
            cv2.putText(image, f"P {pid}", (a[0], max(24, a[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.75 if focus_pred_ids else 0.65, color, 2 if focus_pred_ids else 1)
        cv2.putText(image, f"frame {frame}  GT {gt_id}", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        return image

    def save_case(name: str, panels: list[np.ndarray]) -> list[str]:
        paths = []
        for index, panel in enumerate(panels):
            path = args.output / f"{name}-{index + 1}.jpg"
            cv2.imwrite(str(path), panel)
            paths.append(str(path.relative_to(PROJECT_DIR)))
        resized = [cv2.resize(panel, (640, 480)) for panel in panels]
        if len(resized) == 3:
            sheet = cv2.hconcat(resized)
        else:
            if len(resized) % 2:
                resized.append(np.zeros_like(resized[0]))
            rows = [cv2.hconcat(resized[index : index + 2]) for index in range(0, len(resized), 2)]
            sheet = cv2.vconcat(rows)
        sheet_path = args.output / f"{name}.jpg"
        cv2.imwrite(str(sheet_path), sheet)
        return paths + [str(sheet_path.relative_to(PROJECT_DIR))]

    switch_focus = [selected_switch["old_tracker_id"], selected_switch["new_tracker_id"]]
    switch_panels = [
        draw(selected_switch["before_frame"], selected_switch["gt_id"], focus_pred_ids=switch_focus),
        draw(selected_switch["after_frame"], selected_switch["gt_id"], focus_pred_ids=switch_focus),
    ]
    miss_frame = (selected_miss["start_frame"] + selected_miss["end_frame"]) // 2
    miss_panels = [
        draw(miss_frame, selected_miss["gt_id"], crop=False),
        draw(miss_frame, selected_miss["gt_id"], crop=True),
    ]
    fragment_panels = [
        draw(selected_fragment["match_before_frame"], selected_fragment["gt_id"]),
        draw((selected_fragment["match_before_frame"] + selected_fragment["match_after_frame"]) // 2, selected_fragment["gt_id"]),
        draw(selected_fragment["match_after_frame"], selected_fragment["gt_id"]),
    ]
    files = {}
    files["id_switch"] = save_case("01-id-switch", switch_panels)
    files["miss"] = save_case("02-miss", miss_panels)
    files["fragment"] = save_case("03-fragment", fragment_panels)

    result = {
        "schema": "e02-error-cases-v1",
        "diagnostic_only": True,
        "metric_source": "TrackEval only; this diagnostic IoU matching is for case selection and does not replace IDF1/IDSW/Frag",
        "matching_threshold_iou": args.threshold,
        "selected": {
            "id_switch": {**selected_switch, "frame": selected_switch["before_frame"], "files": files["id_switch"]},
            "miss": {**selected_miss, "frame": miss_frame, "files": files["miss"]},
            "fragment": {**selected_fragment, "frame_mid": (selected_fragment["match_before_frame"] + selected_fragment["match_after_frame"]) // 2, "files": files["fragment"]},
        },
        "counts": {
            "gt_ids": len(gt_ids),
            "id_switch_events_consecutive_matched": len(switches),
            "miss_runs_ge_10_frames": len(misses),
            "fragment_gaps_ge_5_frames": len(fragments),
        },
        "all_id_switch_events": switches[:20],
        "all_miss_runs": misses[:20],
        "all_fragment_gaps": fragments[:20],
        "note": "GT is drawn green for review only; raw detections are yellow; tracker outputs are orange. GT boxes are not system predictions. Miss cases require no raw detection overlap >=0.5 at the selected run.",
    }
    output_json = args.output / "error_cases.json"
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["selected"], ensure_ascii=False, indent=2))
    print("counts:", result["counts"])
    print("output:", output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
