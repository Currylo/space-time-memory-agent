"""A2 fall validation: GMDCSA-24 annotation parsing and event-level evaluation.

The release provides one CSV per subject/activity folder.  Each row has a
`Classes` field such as `Falling (SW)[3.4 to 6]; Sitting[0 to 3.4]`, i.e.
seconds relative to the video start.  This module parses those intervals,
builds video-level labels and evaluates predicted fall events against them.

It deliberately does not treat a folder name as a label: the parsed `Classes`
field is authoritative, and mismatches are reported.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NUMBER_PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*(?:to|-)?\s*(\d+(?:\.\d+)?)")


@dataclass
class GmdEvent:
    subject: int
    kind: str
    file_name: str
    video_path: Path
    duration_csv: float
    description: str
    classes: str
    fall_intervals: list[tuple[float, float]] = field(default_factory=list)
    has_fall: bool = False
    parse_note: str = ""

    @property
    def video_id(self) -> str:
        return f"S{self.subject}_{self.kind}_{Path(self.file_name).stem}"


def parse_fall_intervals(classes: str) -> tuple[list[tuple[float, float]], str]:
    """Parse fall intervals from a Classes string.

    The release contains several malformed strings, e.g. `Falling (BW[5.9 to
    10]` and `Falling (BW)[2.3 6]`.  Segments containing `fall` are parsed for
    numeric pairs; non-fall segments are ignored.  Returns (intervals, note).
    """

    intervals = []
    note = ""
    for segment in str(classes).split(";"):
        if "fall" not in segment.lower():
            continue
        pairs = NUMBER_PAIR.findall(segment)
        if not pairs:
            note = "fall label without parseable interval"
            continue
        for start_text, end_text in pairs:
            start, end = float(start_text), float(end_text)
            if end < start:
                start, end = end, start
            if end - start <= 1e-6:
                note = "zero-length fall interval"
                continue
            intervals.append((start, end))
    # Merge overlapping intervals caused by malformed strings.
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged, note


def load_gmd_events(dataset_dir: Path) -> list[GmdEvent]:
    events = []
    for subject in (1, 2, 3, 4):
        for kind in ("ADL", "Fall"):
            csv_path = dataset_dir / f"Subject {subject}" / f"{kind}.csv"
            if not csv_path.is_file():
                continue
            with csv_path.open("r", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    row = {key.strip(): (value or "").strip() for key, value in row.items()}
                    file_name = row["File Name"]
                    classes = row.get("Classes", "")
                    intervals, note = parse_fall_intervals(classes)
                    video_path = dataset_dir / f"Subject {subject}" / kind / file_name
                    events.append(
                        GmdEvent(
                            subject=subject,
                            kind=kind,
                            file_name=file_name,
                            video_path=video_path,
                            duration_csv=float(row.get("Length (seconds)", 0) or 0),
                            description=row.get("Description", ""),
                            classes=classes,
                            fall_intervals=intervals,
                            has_fall="fall" in classes.lower(),
                            parse_note=note,
                        )
                    )
    return events


def temporal_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def match_events(
    predicted: list[tuple[float, float]],
    ground_truth: list[tuple[float, float]],
    iou_threshold: float = 0.3,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Greedy one-to-one temporal matching by IoU."""

    candidates = []
    for pi, pred in enumerate(predicted):
        for gi, gt in enumerate(ground_truth):
            score = temporal_iou(pred, gt)
            if score >= iou_threshold:
                candidates.append((score, pi, gi))
    candidates.sort(reverse=True)
    used_pred = set()
    used_gt = set()
    matches = []
    for score, pi, gi in candidates:
        if pi in used_pred or gi in used_gt:
            continue
        used_pred.add(pi)
        used_gt.add(gi)
        matches.append((pi, gi, score))
    unmatched_pred = [index for index in range(len(predicted)) if index not in used_pred]
    unmatched_gt = [index for index in range(len(ground_truth)) if index not in used_gt]
    return matches, unmatched_pred, unmatched_gt


def video_level_metrics(y_true: list[int], y_pred: list[int]) -> dict:
    from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "n": int(len(y_true)),
        "positives": int(y_true.sum()),
        "negatives": int((y_true == 0).sum()),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "specificity": float(tn / (tn + fp)) if (tn + fp) else 0.0,
    }


def event_level_metrics(
    records: list[dict],
    iou_threshold: float = 0.3,
) -> dict:
    """Aggregate event-level TP/FP/FN and delay/duration errors.

    Each record has `predicted` and `ground_truth` event lists in seconds.
    Video-level false alarms on ADL are counted as event FPs.
    """

    tp = fp = fn = 0
    delays = []
    duration_errors = []
    matched_rows = []
    for record in records:
        predicted = record["predicted"]
        ground_truth = record["ground_truth"]
        matches, unmatched_pred, unmatched_gt = match_events(predicted, ground_truth, iou_threshold)
        tp += len(matches)
        fp += len(unmatched_pred)
        fn += len(unmatched_gt)
        for pi, gi, score in matches:
            pred = predicted[pi]
            gt = ground_truth[gi]
            # Delay: first overlap time minus GT start; negative means early.
            overlap_start = max(pred[0], gt[0])
            delays.append(overlap_start - gt[0])
            duration_errors.append((pred[1] - pred[0]) - (gt[1] - gt[0]))
            matched_rows.append(
                {
                    "video_id": record["video_id"],
                    "pred_start": pred[0],
                    "pred_end": pred[1],
                    "gt_start": gt[0],
                    "gt_end": gt[1],
                    "temporal_iou": score,
                    "delay_seconds": overlap_start - gt[0],
                    "duration_error_seconds": (pred[1] - pred[0]) - (gt[1] - gt[0]),
                }
            )
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0,
        "matched_events": matched_rows,
        "delay_seconds_mean": float(np.mean(delays)) if delays else None,
        "delay_seconds_median": float(np.median(delays)) if delays else None,
        "duration_error_seconds_mean": float(np.mean(duration_errors)) if duration_errors else None,
    }


def scores_to_events(
    times: list[float],
    scores: list[float],
    high: float = 0.60,
    low: float = 0.35,
    min_consecutive: int = 3,
    min_duration: float = 0.5,
    max_gap: float = 0.35,
) -> list[tuple[float, float]]:
    """Hysteresis event extraction from per-frame fall scores."""

    if not scores:
        return []
    scores = np.asarray(scores, dtype=float)
    times = np.asarray(times, dtype=float)
    # Median filter over 5 samples (approximately 5 * sampling interval).
    if len(scores) >= 5:
        padded = np.pad(scores, 2, mode="edge")
        scores = np.array([np.median(padded[i : i + 5]) for i in range(len(scores))])

    events = []
    active = False
    start_time = None
    last_high = None
    high_run = 0
    for index, (time_value, score) in enumerate(zip(times, scores)):
        if not active:
            if score >= high:
                high_run += 1
                if high_run >= min_consecutive:
                    active = True
                    start_time = times[index - min_consecutive + 1]
                    last_high = time_value
            else:
                high_run = 0
        else:
            if score >= low:
                last_high = time_value
            elif time_value - (last_high if last_high is not None else time_value) > max_gap:
                end_time = float(last_high if last_high is not None else times[index - 1])
                if end_time - start_time >= min_duration:
                    events.append((float(start_time), end_time))
                active = False
                start_time = None
                high_run = 0
    if active and start_time is not None and last_high is not None:
        if last_high - start_time >= min_duration:
            events.append((float(start_time), float(last_high)))
    return events
