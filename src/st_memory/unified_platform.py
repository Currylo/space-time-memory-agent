"""Map one same-source unified run onto a controlled Frigate replay.

The module keeps four clocks/identities separate instead of collapsing them:

* source video time (seconds from the first decoded frame);
* this replay's wall-clock identity (``replay_id`` / ``loop_index``);
* platform event time (Frigate epoch seconds);
* evidence window (rule start -> condition met).

All functions are deliberately pure except the optional OpenCV helpers.  The
clock fit and candidate mapping can therefore be unit-tested without a camera
or a running platform.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import statistics
from typing import Iterable, Sequence

CAMERA_DEFAULT = "a2_unified_mot20_05"
LABEL = "person"
ZONE_ID_DEFAULT = "unified-public-observation-v1"
ZONE_VERSION_DEFAULT = "roi-0.05-0.95-v1"

RULE_IDS = {
    "stay": "region_stay",
    "fall": "fall_posture",
    "crowd": "count_change_60s",
}

KIND_LABELS = {
    "stay": "区域停留",
    "fall": "摔倒提示",
    "crowd": "人数变化",
}


def rule_id_for(kind: str) -> str:
    try:
        return RULE_IDS[kind]
    except KeyError as error:
        raise ValueError(f"unsupported unified rule kind: {kind}") from error


def evidence_window(event: dict) -> tuple[float, float]:
    """Return the adapter-identity evidence window in source-video seconds.

    For stay candidates the frozen adapter identity used
    ``condition_time - observed_dwell``.  When a corrected causal window is
    present in ``features`` (``causal_window_start`` / ``causal_window_end``),
    it is used instead, while the old identity fields in the ledger remain
    untouched.  Crowd evidence is the complete configured count window; the
    2 s sustain gate is evaluated one sampling step later.
    """
    kind = event["kind"]
    condition = float(event["condition_met_video_time"])
    features = event.get("features") or {}
    if kind == "stay":
        start = float(features.get("causal_window_start", condition - float(features["observed_dwell_seconds"])))
        end = float(features.get("causal_window_end", condition))
    elif kind == "crowd":
        # Adapter identity keeps the condition-met end (the value used when
        # these candidates were first persisted).  The exact 60 s rule window
        # is exposed separately by rule_window() so the sustained condition can
        # be checked without including a post-trigger sample.
        start = float(features["window_start"])
        end = condition
    elif kind == "fall":
        start = condition - float(features.get("observed_posture_seconds", 0.0))
        end = condition
    else:
        raise ValueError(f"unsupported unified rule kind: {kind}")
    if not (0.0 <= start <= end):
        raise ValueError(f"invalid evidence window for {event.get('candidate_id')}: {start}..{end}")
    return start, end


def causal_stay_window(
    observations: Sequence[dict],
    track_id: int,
    trigger_time: float,
    *,
    threshold_seconds: float,
    condition_time: float | None = None,
    sample_interval: float = 0.2,
    tolerance: float = 1e-6,
) -> dict:
    """Reconstruct the causal stay evidence from observed samples.

    The rule accumulates ``dt`` only when the target is present and inside;
    missing samples widen the wall-clock span without adding observed seconds.
    Walking backward from the trigger frame's dwell value until the first
    observed sample gives the true start.  The condition-met time is one
    sampling step after the trigger frame and is returned only as post-trigger
    context, never counted in the 10 s basis.
    """
    rows = sorted(observations, key=lambda row: float(row["time"]))
    trigger_index = None
    for index, row in enumerate(rows):
        if abs(float(row["time"]) - trigger_time) <= tolerance:
            trigger_index = index
            break
    if trigger_index is None:
        raise ValueError(f"trigger frame {trigger_time} not found in observations")
    trigger_row = rows[trigger_index]
    trigger_person = next((person for person in trigger_row.get("people", []) if person.get("id") == track_id), None)
    if trigger_person is None:
        raise ValueError(f"track {track_id} absent at trigger frame {trigger_time}")
    if not trigger_person.get("inside"):
        raise ValueError(f"track {track_id} is not inside at trigger frame {trigger_time}")
    trigger_dwell = float(trigger_person.get("dwell", 0.0))
    if trigger_dwell + tolerance < float(threshold_seconds):
        raise ValueError(f"trigger dwell {trigger_dwell} < threshold {threshold_seconds}")

    collected = []
    previous_dwell = None
    index = trigger_index
    while index >= 0:
        row = rows[index]
        person = next((item for item in row.get("people", []) if item.get("id") == track_id), None)
        if person is None:
            index -= 1
            continue
        if not person.get("inside"):
            raise ValueError(f"track {track_id} left the zone before reaching {threshold_seconds}s")
        dwell = float(person.get("dwell", 0.0))
        if previous_dwell is not None:
            difference = previous_dwell - dwell
            if difference < -tolerance:
                raise ValueError(f"non-monotonic dwell for track {track_id} at {row['time']}")
            if difference > sample_interval + tolerance:
                raise ValueError(
                    f"dwell jumped {difference:.3f}s for track {track_id} at {row['time']}; "
                    "the observed run is broken"
                )
        collected.append({"time": float(row["time"]), "dwell": dwell, "inside": True})
        if dwell <= sample_interval + tolerance:
            break
        previous_dwell = dwell
        index -= 1
    if not collected:
        raise ValueError(f"no observed samples for track {track_id} before {trigger_time}")
    collected.sort(key=lambda item: item["time"])
    start = collected[0]["time"]
    first_dwell = collected[0]["dwell"]
    valid_seconds = trigger_dwell
    span_end = float(condition_time) if condition_time is not None else float(trigger_time)
    span = span_end - start
    span_to_trigger = float(trigger_time) - start
    if span + tolerance < valid_seconds:
        raise ValueError(f"wall span {span:.3f}s is shorter than observed dwell {valid_seconds:.3f}s")
    post_trigger = None
    for row in rows:
        if float(row["time"]) > trigger_time + tolerance:
            person = next((item for item in row.get("people", []) if item.get("id") == track_id), None)
            if person is not None:
                post_trigger = {
                    "time": float(row["time"]),
                    "dwell": float(person.get("dwell", 0.0)),
                    "inside": bool(person.get("inside")),
                    "used_in_evidence": False,
                }
            break
    return {
        "track_id": track_id,
        "trigger_frame_video_time": float(trigger_time),
        "condition_met_video_time": None if condition_time is None else round(float(condition_time), 6),
        "causal_window_start": round(start, 6),
        "causal_window_end": round(float(trigger_time), 6),
        "sample_window_start": round(start, 6),
        "sample_window_end": round(float(trigger_time), 6),
        "required_coverage_start": round(start, 6),
        "required_coverage_end": round(span_end, 6),
        "required_coverage_seconds": round(span, 6),
        "observed_coverage_end_video_time": round(span_end, 6),
        "observed_start_video_time": round(start, 6),
        "observed_end_video_time": round(float(trigger_time), 6),
        "observed_span_end_video_time": round(span_end, 6),
        "observed_span_seconds": round(span, 6),
        "observed_span_to_trigger_seconds": round(span_to_trigger, 6),
        "observed_valid_seconds": round(valid_seconds, 6),
        "missing_gap_seconds": round(max(0.0, span - valid_seconds), 6),
        "sample_count": len(collected),
        "valid_sample_count": len(collected),
        "condition_ok": bool(
            valid_seconds + tolerance >= float(threshold_seconds)
            and first_dwell <= sample_interval + tolerance
            and all(item["inside"] for item in collected)
        ),
        "first_dwell_seconds": round(first_dwell, 6),
        "trigger_dwell_seconds": round(trigger_dwell, 6),
        "threshold_seconds": float(threshold_seconds),
        "sample_interval_seconds": float(sample_interval),
        "post_trigger_frame": post_trigger,
        "samples": collected,
        "version": "stay-window-v2-causal",
        "window_version": "stay-window-v2-causal",
    }


def rule_window(event: dict) -> tuple[float, float]:
    """Exact rule basis window, separate from the adapter evidence end.

    For crowd, the exact configured window ends at ``features.window_end`` and
    the condition-met time is one sampling step later.  The adapter identity
    deliberately keeps the original evidence end so existing ledger keys do not
    change within this replay; the exact window is carried as separate fields.
    """
    if event["kind"] == "crowd":
        features = event.get("features") or {}
        return float(features["window_start"]), float(features["window_end"])
    return evidence_window(event)


def event_request_plan(candidate: dict) -> dict:
    """Compute Frigate manual-event pre-capture/post duration.

    Frigate's manual-event API treats ``duration`` as seconds *after* the POST
    time and ``pre_capture`` as seconds before it.  The POST time is the
    condition-met source time.  Two seconds of lead are added before the
    evidence start and three seconds after the condition so that a sub-second
    clock residual cannot truncate the rule evidence.
    """
    condition = float(candidate["condition_met_video_time"])
    start = float(candidate["evidence_start"])
    end = float(candidate["evidence_end"])
    pre_capture = int(math.ceil(condition - start)) + 2
    post_duration = int(math.ceil(max(0.0, end - condition))) + 3
    return {
        "pre_capture": pre_capture,
        "duration": post_duration,
        "expected_total_seconds": pre_capture + post_duration,
        "lead_before_evidence_seconds": pre_capture - (condition - start),
        "post_after_evidence_seconds": post_duration - max(0.0, end - condition),
    }


def to_platform_candidate(
    event: dict,
    config: dict,
    run_id: str,
    *,
    camera: str = CAMERA_DEFAULT,
    replay_id: str,
    loop_index: int,
    zone_version: str = ZONE_VERSION_DEFAULT,
) -> dict:
    """Map a frozen ``UnifiedRules`` candidate to the persistent adapter schema.

    ``score`` is intentionally ``None``.  The unified rules emit boolean rule
    satisfaction, not a probability or an accuracy estimate; writing 1.0 here
    would misrepresent it as an event score.
    """
    start, end = evidence_window(event)
    rule_start, rule_end = rule_window(event)
    condition = float(event["condition_met_video_time"])
    if event.get("source_sha256") != config.get("source_sha256"):
        raise ValueError("candidate source hash does not match the frozen run config")
    if event.get("run_id") != run_id:
        raise ValueError("candidate run_id does not match the requested run")
    features = dict(event.get("features") or {})
    if event["kind"] == "crowd":
        # The frozen candidate retains from_count/to_count but not the
        # configured threshold values.  Keep the threshold explicit so a
        # viewer can check the 60 s condition instead of inferring it from the
        # final count.
        crowd = config.get("crowd") or {}
        features.setdefault("from_below", crowd.get("from_below"))
        features.setdefault("to_at_least", crowd.get("to_at_least"))
        features.setdefault("window_seconds", crowd.get("window_seconds"))
    return {
        "candidate_id": event["candidate_id"],
        "source_video_sha256": event["source_sha256"],
        "camera": camera,
        "label": LABEL,
        "rule_id": rule_id_for(event["kind"]),
        "rule_version": event["rule_version"],
        "zone_id": event["zone_id"],
        "zone_version": zone_version,
        "run_id": run_id,
        "loop_index": int(loop_index),
        "replay_id": replay_id,
        "track_id": event.get("track_id"),
        "source_video_time": round(float(event["evidence_frame_video_time"]), 6),
        "condition_met_video_time": round(float(event["condition_met_video_time"]), 6),
        "evidence_start": round(start, 6),
        "evidence_end": round(end, 6),
        "rule_window_start": round(rule_start, 6),
        "rule_window_end": round(rule_end, 6),
        "comparison_window_start": round(rule_start, 6),
        "comparison_window_end": round(rule_end, 6),
        "comparison_window_seconds": round(rule_end - rule_start, 6),
        "required_coverage_start": round(rule_start, 6),
        "required_coverage_end": round(condition, 6),
        "required_coverage_seconds": round(condition - rule_start, 6),
        "score": None,
        "score_type": "rule_boolean_not_a_probability",
        "rule_satisfied": True,
        "kind": event["kind"],
        "evidence_frame_video_time": round(float(event["evidence_frame_video_time"]), 6),
        "features": features,
        "model_derived": True,
        "ground_truth": False,
        "source_video_name": config.get("source_name", "MOT20-05-raw.mp4"),
    }


@dataclass
class ClockSample:
    source_time: float
    platform_epoch: float
    kind: str = "unknown"
    note: str = ""


@dataclass
class ClockModel:
    t0: float
    drift: float = 1.0
    samples: int = 0
    rmse: float = 0.0
    max_abs_residual: float = 0.0
    residuals: list[dict] = field(default_factory=list)

    def platform_epoch(self, source_time: float) -> float:
        return self.t0 + self.drift * float(source_time)

    def source_time(self, platform_epoch: float) -> float:
        return (float(platform_epoch) - self.t0) / self.drift

    def shifted(self, seconds: float) -> "ClockModel":
        """Return a model whose intercept moves by ``seconds``."""
        return ClockModel(
            t0=self.t0 + seconds,
            drift=self.drift,
            samples=self.samples,
            rmse=self.rmse,
            max_abs_residual=self.max_abs_residual,
            residuals=list(self.residuals),
        )

    def as_dict(self) -> dict:
        return {
            "t0_epoch": self.t0,
            "drift_source_seconds_per_platform_second": self.drift,
            "drift_ppm": (self.drift - 1.0) * 1_000_000.0,
            "samples": self.samples,
            "rmse_seconds": self.rmse,
            "max_abs_residual_seconds": self.max_abs_residual,
            "residuals": self.residuals,
        }


def clock_segment_anchors(segment: dict, fps: float = 25.0) -> dict:
    """Return both endpoint anchors for one Frigate recording segment.

    Frigate's database interval is ``start_time`` (first frame) to
    ``end_time = start_time + duration`` (coverage end).  The last decoded
    frame's PTS is one frame period before the coverage end.  Using
    ``end_time - last_frame_pts`` alone is therefore 40 ms late at 25 FPS.
    """
    period = 1.0 / float(fps)
    start = float(segment["segment_start_epoch"])
    end = float(segment["segment_end_epoch"])
    first = float(segment["first_source_time"])
    last = float(segment["last_source_time"])
    t0_start = start - first
    t0_last_coverage = end - (last + period)
    return {
        "segment_id": segment.get("segment_id"),
        "fps": float(fps),
        "frame_period_seconds": period,
        "frame_count": segment.get("frame_count"),
        "db_duration": segment.get("db_duration"),
        "source_first_frame": first,
        "source_last_frame": last,
        "source_last_coverage_end": last + period,
        "db_start": start,
        "db_end": end,
        "t0_from_start": t0_start,
        "t0_from_last_coverage_end": t0_last_coverage,
        "endpoint_delta_seconds": t0_last_coverage - t0_start,
        "start_anchor": {"source_time": first, "platform_epoch": start},
        "coverage_end_anchor": {"source_time": last + period, "platform_epoch": end},
    }


def fit_clock(samples: Sequence[ClockSample], *, fixed_drift: float | None = None) -> ClockModel:
    """Robust linear fit of platform epoch against source video time.

    A single sample cannot separate intercept from drift, so drift is fixed at
    ``fixed_drift`` (default 1.0) and the model is reported with that caveat.
    With two or more samples, a least-squares slope is used and up to two
    passes of 2.5-sigma clipping remove frame-match outliers.
    """
    if not samples:
        raise ValueError("clock fit needs at least one sample")
    points = [(float(s.source_time), float(s.platform_epoch), s) for s in samples]
    if fixed_drift is not None:
        drift = float(fixed_drift)
        intercepts = [y - drift * x for x, y, _ in points]
        t0 = statistics.median(intercepts)
    elif len(points) == 1:
        drift = 1.0
        t0 = points[0][1] - drift * points[0][0]
    else:
        drift, t0 = _least_squares(points)
        for _ in range(2):
            residuals = [y - (t0 + drift * x) for x, y, _ in points]
            median = statistics.median(residuals)
            mad = statistics.median(abs(r - median) for r in residuals) or 1e-9
            keep = [i for i, r in enumerate(residuals) if abs(r - median) <= 2.5 * 1.4826 * mad]
            if len(keep) < max(2, len(points) // 2) or len(keep) == len(points):
                break
            points = [points[i] for i in keep]
            drift, t0 = _least_squares(points)
    residuals = []
    for x, y, sample in points:
        predicted = t0 + drift * x
        residuals.append(
            {
                "source_time": round(x, 6),
                "platform_epoch": round(y, 6),
                "predicted_epoch": round(predicted, 6),
                "residual_seconds": round(y - predicted, 6),
                "kind": sample.kind,
                "note": sample.note,
            }
        )
    values = [item["residual_seconds"] for item in residuals]
    rmse = math.sqrt(sum(v * v for v in values) / len(values))
    return ClockModel(
        t0=t0,
        drift=drift,
        samples=len(points),
        rmse=rmse,
        max_abs_residual=max(abs(v) for v in values),
        residuals=residuals,
    )


def _least_squares(points: Sequence[tuple[float, float, ClockSample]]) -> tuple[float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 1e-12:
        return 1.0, statistics.median(y - x for x, y in zip(xs, ys))
    drift = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return drift, my - drift * mx


def alignment_status(model: ClockModel, *, max_rmse: float = 0.5, max_residual: float = 1.0, max_drift_ppm: float = 5000.0) -> dict:
    """Classify a clock fit without silently changing the tolerance."""
    reasons = []
    if model.samples < 2:
        reasons.append("fewer_than_two_alignment_points")
    if model.rmse > max_rmse:
        reasons.append(f"rmse_{model.rmse:.3f}s_exceeds_{max_rmse:.3f}s")
    if model.max_abs_residual > max_residual:
        reasons.append(f"max_residual_{model.max_abs_residual:.3f}s_exceeds_{max_residual:.3f}s")
    if abs(model.drift - 1.0) * 1_000_000.0 > max_drift_ppm:
        reasons.append(f"drift_{ (model.drift-1)*1e6:.1f}ppm_exceeds_{max_drift_ppm:.0f}ppm")
    return {
        "status": "verified" if not reasons else "needs_review",
        "reasons": reasons,
        "tolerances": {
            "max_rmse_seconds": max_rmse,
            "max_abs_residual_seconds": max_residual,
            "max_drift_ppm": max_drift_ppm,
        },
    }


def evidence_coverage(
    *,
    evidence_start: float,
    evidence_end: float,
    clip_source_start: float | None,
    clip_source_end: float | None,
    clip_duration: float | None,
    clip_fps: float = 25.0,
    clip_coverage_end: float | None = None,
    comparison_window_seconds: float | None = None,
    alignment_tolerance: float = 0.5,
) -> dict:
    """Check complete coverage of the rule's required interval.

    ``evidence_end`` is the required coverage end (the end of the last
    observation sample's interval), not the last sample PTS.  ``clip_source_end``
    is the last decoded frame PTS; its coverage end is one frame later unless
    ``clip_coverage_end`` is supplied explicitly.  ``alignment_tolerance`` is
    reported separately and never excuses a missing head or tail.
    """
    required = max(0.0, float(evidence_end) - float(evidence_start))
    problems = []
    covered = None
    missing_head = None
    missing_tail = None
    duration_short = None
    clip_end_coverage = None
    if clip_source_start is None or clip_source_end is None:
        problems.append("clip_source_bounds_missing")
    if clip_duration is None:
        problems.append("clip_duration_missing")
    if not problems:
        clip_end_coverage = (
            float(clip_coverage_end)
            if clip_coverage_end is not None
            else float(clip_source_end) + 1.0 / float(clip_fps)
        )
        covered = max(
            0.0,
            min(clip_end_coverage, float(evidence_end))
            - max(float(clip_source_start), float(evidence_start)),
        )
        missing_head = max(0.0, float(clip_source_start) - float(evidence_start))
        missing_tail = max(0.0, float(evidence_end) - clip_end_coverage)
        duration_short = max(0.0, required - float(clip_duration))
        if missing_head > 1e-6:
            problems.append(f"clip_starts_{missing_head:.3f}s_after_evidence_start")
        if missing_tail > 1e-6:
            problems.append(f"clip_ends_{missing_tail:.3f}s_before_coverage_end")
        if duration_short > 1e-6:
            problems.append(f"clip_duration_{clip_duration:.3f}s_shorter_than_{required:.3f}s")
    if not problems:
        status = "verified"
    elif covered is not None and covered > 1e-6:
        status = "partial"
    else:
        status = "failed"
    return {
        "status": status,
        "problems": problems,
        "alignment_tolerance_seconds": alignment_tolerance,
        "required_seconds": round(required, 6),
        "comparison_window_seconds": (
            None if comparison_window_seconds is None else round(float(comparison_window_seconds), 6)
        ),
        "covered_seconds": None if covered is None else round(covered, 6),
        "coverage_ratio": None if covered is None or required <= 0 else round(covered / required, 6),
        "missing_head_seconds": None if missing_head is None else round(missing_head, 6),
        "missing_tail_seconds": None if missing_tail is None else round(missing_tail, 6),
        "duration_short_seconds": None if duration_short is None else round(duration_short, 6),
        "clip_source_end_pts": None if clip_source_end is None else round(float(clip_source_end), 6),
        "clip_coverage_end_seconds": None if clip_end_coverage is None else round(clip_end_coverage, 6),
        "frame_period_seconds": round(1.0 / float(clip_fps), 6),
    }


def annotate_causal_stay_events(events: Sequence[dict], observations: Sequence[dict], config: dict) -> list[dict]:
    """Attach v2 causal stay windows to frozen events before mapping.

    This is used by new runs.  Existing ledger candidates keep their original
    identity fields; the rerun correction script adds the same metadata without
    changing their keys.
    """
    threshold_default = float(config.get("stay_seconds", 10.0))
    annotated = []
    for event in events:
        if event.get("kind") != "stay":
            annotated.append(event)
            continue
        result = causal_stay_window(
            observations,
            event["track_id"],
            event["evidence_frame_video_time"],
            threshold_seconds=float((event.get("features") or {}).get("threshold_seconds", threshold_default)),
            condition_time=event["condition_met_video_time"],
        )
        features = event.setdefault("features", {})
        features.update({
            "causal_window_start": result["causal_window_start"],
            "causal_window_end": result["causal_window_end"],
            "causal_window_version": result["window_version"],
            "observed_span_seconds": result["observed_span_seconds"],
            "observed_valid_seconds": result["observed_valid_seconds"],
            "missing_gap_seconds": result["missing_gap_seconds"],
            "trigger_frame_video_time": result["trigger_frame_video_time"],
            "post_trigger_frame_video_time": result["condition_met_video_time"],
            "condition_in_evidence": False,
        })
        event["causal_window"] = result
        annotated.append(event)
    return annotated


def source_frame_number(source_time: float, fps: float = 25.0) -> int:
    """Burned-in watermark is 1-based; frame 0 of the file shows ``1``."""
    return int(round(source_time * fps)) + 1


# ---------------------------------------------------------------------------
# Optional OpenCV helpers.  Imported lazily so pure mapping tests run anywhere.
# ---------------------------------------------------------------------------


def _cv():
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    return cv2, np


def _dhash(gray) -> int:
    cv2, np = _cv()
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return value


def _thumb(gray):
    cv2, np = _cv()
    return cv2.resize(gray, (64, 42), interpolation=cv2.INTER_AREA)


class SourceIndex:
    """Frame signatures and burned-in frame-number templates for one video."""

    def __init__(self, path, fps: float | None = None):
        self.path = str(path)
        cv2, np = _cv()
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open source video: {self.path}")
        self.fps = float(fps or cap.get(cv2.CAP_PROP_FPS))
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        times = []
        hashes = []
        thumbs = []
        watermarks = []
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            times.append(index / self.fps)
            hashes.append(_dhash(gray))
            thumbs.append(_thumb(gray))
            watermarks.append(index + 1)
            index += 1
        cap.release()
        if not times:
            raise RuntimeError(f"no frames decoded from {self.path}")
        self.times = np.asarray(times, dtype=np.float64)
        self.hashes = np.asarray(hashes, dtype=np.uint64)
        self.thumbs = np.stack(thumbs).astype(np.uint8)
        self.watermarks = np.asarray(watermarks, dtype=np.int32)
        self.templates = self._build_digit_templates()

    def _build_digit_templates(self):
        cv2, np = _cv()
        templates: dict[str, list] = {}
        cap = cv2.VideoCapture(self.path)
        for frame_index in range(10):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            glyphs = self._glyphs(gray)
            label = str(frame_index + 1)
            if len(glyphs) != len(label):
                continue
            for glyph, digit in zip(glyphs, label):
                templates.setdefault(digit, []).append(glyph)
        cap.release()
        return {digit: np.mean(values, axis=0) for digit, values in templates.items() if values}

    @staticmethod
    def _glyphs(gray):
        cv2, np = _cv()
        roi = gray[0:80, 0:300]
        _, binary = cv2.threshold(roi, 200, 255, cv2.THRESH_BINARY)
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        glyphs = []
        for index in range(1, count):
            x, y, w, h, area = stats[index]
            if 20 <= h <= 70 and 4 <= w <= 60 and area >= 60 and y < 60:
                glyph = (binary[y:y + h, x:x + w] > 0).astype(np.float32)
                glyph = cv2.resize(glyph, (16, 32), interpolation=cv2.INTER_AREA)
                glyphs.append((x, glyph))
        glyphs.sort(key=lambda item: item[0])
        return [glyph for _, glyph in glyphs]

    def read_watermark(self, image) -> dict:
        cv2, np = _cv()
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        if gray.shape[1] != self.width or gray.shape[0] != self.height:
            gray = cv2.resize(gray, (self.width, self.height), interpolation=cv2.INTER_AREA)
        glyphs = self._glyphs(gray)
        digits = []
        scores = []
        for glyph in glyphs:
            best = None
            for digit, template in self.templates.items():
                mse = float(np.mean((glyph - template) ** 2))
                if best is None or mse < best[0]:
                    best = (mse, digit)
            if best is None:
                continue
            scores.append(best[0])
            digits.append(best[1])
        text = "".join(digits)
        confidence = max(0.0, 1.0 - (max(scores) if scores else 1.0))
        number = int(text) if text.isdigit() else None
        return {
            "text": text,
            "frame_number": number,
            "digits": len(digits),
            "confidence": round(confidence, 4),
            "max_glyph_mse": round(max(scores), 6) if scores else None,
        }

    def match(self, image, *, top_k: int = 40, method: str = "combined") -> dict:
        cv2, np = _cv()
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        if gray.shape[1] != self.width or gray.shape[0] != self.height:
            gray = cv2.resize(gray, (self.width, self.height), interpolation=cv2.INTER_AREA)
        watermark = self.read_watermark(gray)
        query_hash = _dhash(gray)
        xor = (self.hashes ^ np.uint64(query_hash)).view(np.uint8).reshape(-1, 8)
        distances = np.unpackbits(xor, axis=1).sum(axis=1)
        candidates = np.argsort(distances)[: max(top_k, 1)]
        query_thumb = _thumb(gray).astype(np.float32)
        best = None
        for index in candidates:
            thumb = self.thumbs[int(index)].astype(np.float32)
            mae = float(np.mean(np.abs(thumb - query_thumb)))
            if best is None or mae < best[0]:
                best = (mae, int(index), int(distances[int(index)]))
        if best is None:
            return {"watermark": watermark, "frame_index": None, "source_time": None}
        mae, frame_index, hamming = best
        # Agreement between the independent watermark and image hash is the
        # strongest signal.  A mismatch is retained for the report.
        watermark_index = None
        if watermark["frame_number"] is not None and 1 <= watermark["frame_number"] <= len(self.times):
            watermark_index = watermark["frame_number"] - 1
        return {
            "watermark": watermark,
            "frame_index": frame_index,
            "source_time": float(self.times[frame_index]),
            "image_mae": round(mae, 6),
            "hash_hamming": int(hamming),
            "watermark_index": watermark_index,
            "watermark_source_time": (
                None if watermark_index is None else float(self.times[watermark_index])
            ),
            "methods_agree": (
                None if watermark_index is None else abs(watermark_index - frame_index) <= 1
            ),
            "method": "watermark+image" if watermark_index is not None else "image",
        }


def decode_clip_bounds(path, index: SourceIndex, *, margin_frames: int = 2) -> dict:
    """Decode first/last frames of a clip and return their source times."""
    cv2, np = _cv()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"decoded": False, "error": "cannot_open"}
    fps = float(cap.get(cv2.CAP_PROP_FPS) or index.fps)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if count <= 0:
        cap.release()
        return {"decoded": False, "error": "no_frame_count"}
    samples = []
    positions = sorted(set([0, min(margin_frames, count - 1), max(0, count - 1 - margin_frames), count - 1]))
    for position in positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, position)
        ok, frame = cap.read()
        if not ok:
            samples.append({"position": position, "decoded": False})
            continue
        match = index.match(frame)
        samples.append({"position": position, "decoded": True, **match})
    cap.release()
    first = next((s for s in samples if s.get("decoded")), None)
    last = next((s for s in reversed(samples) if s.get("decoded")), None)

    def pick_source(sample):
        if sample is None:
            return None
        if sample.get("watermark_source_time") is not None:
            return sample["watermark_source_time"]
        return sample.get("source_time")

    first_time = pick_source(first)
    last_time = pick_source(last)
    return {
        "decoded": True,
        "fps": fps,
        "frame_count": count,
        "duration": count / fps if fps else None,
        "samples": samples,
        "source_start": first_time,
        "source_end": last_time,
        "watermark_start": first.get("watermark", {}).get("frame_number") if first else None,
        "watermark_end": last.get("watermark", {}).get("frame_number") if last else None,
        "methods_agree_all": all(s.get("methods_agree") is not False for s in samples if s.get("decoded")),
    }


def summarize_replay(events: Iterable[dict], config: dict, run_id: str, **kwargs) -> list[dict]:
    return [to_platform_candidate(event, config, run_id, **kwargs) for event in events]
