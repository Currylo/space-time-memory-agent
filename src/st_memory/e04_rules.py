"""E04 rule baseline: zone observations, observable dwell and reversal candidates.

The module is deliberately small and dependency-free (Python standard library).
It reads the adopted E00-A run and the E03 zone-derived assignments, processes
observations in video-time order, and writes explainable rule candidates.

What is reused from existing components
---------------------------------------
* E03 point-zone derivation (bottom-centre ray casting) is the authoritative
  membership used here, so E03 queries and E04 features refer to the same
  points.
* Supervision 0.25.1 ``PolygonZone`` is used as a separate cross-check by the
  build script.  It is not used as the production membership source because
  its raster mask treats polygon boundaries differently from E03's vector
  ray-casting and because this pinned version has no time-in-zone accounting.

What is new
-----------
* observable dwell accumulation with explicit gap / boundary / track-end rules;
* effective reversal counting with low-displacement and long-gap exclusions;
* rule lifecycle (first trigger, active intervals, end reason) and dedup keys;
* a 60-second occupancy-change rule that reports insufficient observation
  instead of shortening its window.

All distances are image pixels.  There is no camera calibration, so this
module never emits metres, metres/second or field thresholds.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ENGINE_VERSION = "e04-rule-engine-v2"
CANDIDATE_STATUS = "rule_condition_met_pending_review"
CANDIDATE_STATUS_LABEL = "规则条件触发，待人工复核"
ZONE_BOUNDARY_METHOD = "e03_bbox_bottom_center_ray_casting_half_open"
COUNT_DEFINITION = (
    "每帧存在跟踪点的算法观测目标数；同一目标跨帧按局部轨迹编号区分。"
    "不是独立人数、身份统计或区域实际人数。"
)
DWELL_DEFINITION = (
    "帧时间戳取帧起点，单帧覆盖 [t, t+1/fps)；区域内可观测停留时间按有效观测帧累计，"
    "每帧计 1/fps，不插值、不补点。条件满足时刻取触发帧覆盖结束；"
    "证据帧时刻仍记录该帧起点，不以首次至最后观察的跨度代替持续停留。"
)


class E04Error(Exception):
    """Base error for E04 rule construction."""


class E04ValidationError(E04Error):
    """Input data are missing or inconsistent."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_obj(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise E04ValidationError(f"读取 JSON 失败: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise E04ValidationError(f"JSON 顶层必须是对象: {path}")
    return value


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise E04ValidationError(
                        f"{path}:{line_no}: JSON 解析失败: {exc}"
                    ) from exc
                if not isinstance(row, dict):
                    raise E04ValidationError(f"{path}:{line_no}: 行必须是对象")
                rows.append(row)
    except OSError as exc:
        raise E04ValidationError(f"读取 JSONL 失败: {path}: {exc}") from exc
    return rows


def _number(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise E04ValidationError(f"{label} 不是数字: {value!r}") from exc


@dataclass(frozen=True)
class Observation:
    """One tracked point at one video frame."""

    track_id: int
    frame_index: int
    video_time: float
    bbox: tuple[float, float, float, float]
    x: float
    y: float
    zone_ids: tuple[str, ...]
    score: float | None = None

    @property
    def bottom_center(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass
class Zone:
    zone_id: str
    name: str
    polygon: tuple[tuple[float, float], ...]
    version: str
    coordinate_system: str
    purpose: str
    business_zone: bool
    enabled: bool

    def as_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "polygon": [list(point) for point in self.polygon],
            "version": self.version,
            "coordinate_system": self.coordinate_system,
            "purpose": self.purpose,
            "business_zone": self.business_zone,
            "enabled": self.enabled,
        }


@dataclass
class ZoneSegment:
    """A plausible continuous in-zone episode for one track and one zone."""

    segment_id: str
    track_id: int
    zone_id: str
    points: list[Observation]
    fps: float
    entry_observed: bool
    left_censored: bool
    right_censored: bool
    exit_confirmed: bool
    end_reason: str
    boundary_excursions: int
    max_gap_seconds: float
    gap_count: int
    excursion_after_indices: list[int] = field(default_factory=list)

    @property
    def frame_period(self) -> float:
        return 1.0 / self.fps

    @property
    def start_time(self) -> float:
        return self.points[0].video_time

    @property
    def end_time(self) -> float:
        return self.points[-1].video_time

    @property
    def observed_points(self) -> int:
        return len(self.points)

    @property
    def observed_dwell_seconds(self) -> float:
        return len(self.points) / self.fps

    @property
    def coverage_start(self) -> float:
        return self.points[0].video_time

    @property
    def coverage_end(self) -> float:
        return self.points[-1].video_time + self.frame_period

    @property
    def span_seconds(self) -> float:
        """Coverage interval from first frame start to last frame end."""

        return self.coverage_end - self.coverage_start

    @property
    def gap_seconds(self) -> float:
        return max(0.0, self.span_seconds - self.observed_dwell_seconds)

    @property
    def in_zone_ratio(self) -> float:
        span = self.span_seconds
        return (len(self.points) / self.fps / span) if span > 0 else 0.0

    def dwell_at_index(self, index: int) -> float:
        return (index + 1) / self.fps

    def coverage_end_at_index(self, index: int, analysis_end: float | None = None) -> float:
        value = self.points[index].video_time + self.frame_period
        if analysis_end is not None:
            value = min(value, analysis_end)
        return value

    def as_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "track_id": self.track_id,
            "zone_id": self.zone_id,
            "start_time": round(self.start_time, 3),
            "end_time": round(self.end_time, 3),
            "coverage_start": round(self.coverage_start, 3),
            "coverage_end": round(self.coverage_end, 3),
            "observed_points": self.observed_points,
            "observed_dwell_seconds": round(self.observed_dwell_seconds, 3),
            "span_seconds": round(self.span_seconds, 3),
            "gap_seconds": round(self.gap_seconds, 3),
            "in_zone_ratio": round(self.in_zone_ratio, 4),
            "entry_observed": self.entry_observed,
            "left_censored": self.left_censored,
            "right_censored": self.right_censored,
            "exit_confirmed": self.exit_confirmed,
            "end_reason": self.end_reason,
            "boundary_excursions": self.boundary_excursions,
            "max_gap_seconds": round(self.max_gap_seconds, 4),
            "gap_count": self.gap_count,
            "excursion_after_indices": self.excursion_after_indices,
        }


@dataclass
class RunData:
    run_dir: Path
    run: dict
    manifest: dict
    frames: list[dict]
    summaries: list[dict]
    points: list[Observation]
    points_by_track: dict[int, list[Observation]]
    fps: float
    analysis_start: float
    analysis_end: float
    source_video: Path
    hashes: dict[str, str] = field(default_factory=dict)


def load_run(run_dir: Path) -> RunData:
    run_dir = run_dir.resolve()
    required = [
        "run.json",
        "manifest.json",
        "tracks.jsonl",
        "trace_summaries.jsonl",
        "frames.jsonl",
    ]
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        raise E04ValidationError(
            "运行目录缺少必要文件: " + ", ".join(str(run_dir / name) for name in missing)
        )

    run = load_json(run_dir / "run.json")
    manifest = load_json(run_dir / "manifest.json")
    frames = load_jsonl(run_dir / "frames.jsonl")
    summaries = load_jsonl(run_dir / "trace_summaries.jsonl")
    raw_points = load_jsonl(run_dir / "tracks.jsonl")

    if run.get("status") != "completed":
        raise E04ValidationError(f"运行状态不是 completed: {run.get('status')!r}")
    if not frames:
        raise E04ValidationError("frames.jsonl 为空")
    if not raw_points:
        raise E04ValidationError("tracks.jsonl 为空")

    fps = _number(manifest.get("fps"), "manifest.fps")
    if fps <= 0:
        raise E04ValidationError("fps 必须大于 0")
    analysis_end = _number(run.get("processed_video_seconds"), "run.processed_video_seconds")
    if analysis_end <= 0:
        raise E04ValidationError("processed_video_seconds 必须大于 0")

    source = run.get("source") or {}
    source_video = Path(str(source.get("path") or ""))
    if not source_video.is_file():
        raise E04ValidationError(f"源视频不存在: {source_video}")

    zone_ids_by_point: dict[tuple[int, int], set[str]] = {}
    points: list[Observation] = []
    for row in raw_points:
        try:
            track_id = int(row["track_id"])
            frame_index = int(row["frame_index"])
            video_time = float(row["video_time"])
            bbox_values = row["bbox"]
            if len(bbox_values) != 4:
                raise ValueError("bbox 长度不是 4")
            bbox = tuple(float(value) for value in bbox_values)
            position = row.get("position")
            if position is None or len(position) != 2:
                x = (bbox[0] + bbox[2]) / 2.0
                y = bbox[3]
            else:
                x = float(position[0])
                y = float(position[1])
        except (KeyError, TypeError, ValueError) as exc:
            raise E04ValidationError(f"轨迹点字段无效: {row!r}") from exc
        point = Observation(
            track_id=track_id,
            frame_index=frame_index,
            video_time=video_time,
            bbox=bbox,
            x=x,
            y=y,
            zone_ids=(),
            score=float(row["score"]) if row.get("score") is not None else None,
        )
        points.append(point)
        zone_ids_by_point[(track_id, frame_index)] = set()

    if len(points) != int(run.get("track_point_count", len(points))):
        raise E04ValidationError(
            f"轨迹点数量不一致: {len(points)} != {run.get('track_point_count')}"
        )
    if len(summaries) != int(run.get("unique_track_count", len(summaries))):
        raise E04ValidationError(
            f"轨迹摘要数量不一致: {len(summaries)} != {run.get('unique_track_count')}"
        )
    if len(frames) != int(run.get("processed_frames", len(frames))):
        raise E04ValidationError(
            f"帧记录数量不一致: {len(frames)} != {run.get('processed_frames')}"
        )

    # Keep points in deterministic order; attach zone ids later.
    points.sort(key=lambda item: (item.frame_index, item.track_id))
    points_by_track: dict[int, list[Observation]] = {}
    for point in points:
        points_by_track.setdefault(point.track_id, []).append(point)
    for track_points in points_by_track.values():
        track_points.sort(key=lambda item: item.frame_index)
        times = [point.video_time for point in track_points]
        if times != sorted(times):
            raise E04ValidationError("同一条轨迹的时间不单调")

    frames.sort(key=lambda item: int(item["frame_index"]))
    frame_times = [float(item["video_time"]) for item in frames]
    if frame_times != sorted(frame_times):
        raise E04ValidationError("帧时间不单调")
    frame_index_set = {int(item["frame_index"]) for item in frames}
    missing_frames = sorted(
        {point.frame_index for point in points if point.frame_index not in frame_index_set}
    )
    if missing_frames:
        raise E04ValidationError(
            "轨迹点引用了不存在的帧: " + ", ".join(str(value) for value in missing_frames[:10])
        )

    hashes = {
        "run_json": sha256_file(run_dir / "run.json"),
        "manifest_json": sha256_file(run_dir / "manifest.json"),
        "tracks_jsonl": sha256_file(run_dir / "tracks.jsonl"),
        "trace_summaries_jsonl": sha256_file(run_dir / "trace_summaries.jsonl"),
        "frames_jsonl": sha256_file(run_dir / "frames.jsonl"),
        "source_video": sha256_file(source_video),
    }

    return RunData(
        run_dir=run_dir,
        run=run,
        manifest=manifest,
        frames=frames,
        summaries=summaries,
        points=points,
        points_by_track=points_by_track,
        fps=fps,
        analysis_start=0.0,
        analysis_end=analysis_end,
        source_video=source_video,
        hashes=hashes,
    )


def load_zones(zones_path: Path, camera_id: str) -> tuple[list[Zone], dict]:
    config = load_json(zones_path)
    cameras = config.get("cameras") or []
    camera = next((item for item in cameras if item.get("camera_id") == camera_id), None)
    if camera is None:
        raise E04ValidationError(f"区域配置中没有摄像头 {camera_id}")
    zones = []
    for row in camera.get("zones") or []:
        polygon = row.get("polygon") or []
        if len(polygon) < 3:
            raise E04ValidationError(f"区域 {row.get('zone_id')} 多边形少于 3 点")
        zones.append(
            Zone(
                zone_id=str(row["zone_id"]),
                name=str(row.get("name") or row["zone_id"]),
                polygon=tuple((float(point[0]), float(point[1])) for point in polygon),
                version=str(row.get("version") or camera.get("version") or ""),
                coordinate_system=str(
                    row.get("coordinate_system")
                    or config.get("coordinate_system")
                    or "image_pixels_xy_origin_top_left"
                ),
                purpose=str(row.get("purpose") or ""),
                business_zone=bool(row.get("business_zone")),
                enabled=bool(row.get("enabled", True)),
            )
        )
    return zones, camera


def attach_zone_assignments(
    run: RunData, assignments_path: Path, zones: Sequence[Zone]
) -> tuple[dict[tuple[int, int], tuple[str, ...]], dict[str, Any]]:
    rows = load_jsonl(assignments_path)
    zone_ids = {zone.zone_id for zone in zones}
    by_key: dict[tuple[int, int], set[str]] = {}
    unknown_zones: set[str] = set()
    missing_points = 0
    known_points = {(point.track_id, point.frame_index) for point in run.points}
    for row in rows:
        try:
            key = (int(row["track_id"]), int(row["frame_index"]))
            zone_id = str(row["zone_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise E04ValidationError(f"区域派生记录字段无效: {row!r}") from exc
        if key not in known_points:
            missing_points += 1
            continue
        if zone_id not in zone_ids:
            unknown_zones.add(zone_id)
            continue
        by_key.setdefault(key, set()).add(zone_id)
    if unknown_zones:
        raise E04ValidationError("区域派生含未定义区域: " + ", ".join(sorted(unknown_zones)))
    if missing_points:
        raise E04ValidationError(f"区域派生中有 {missing_points} 个点不在轨迹中")

    resolved = {key: tuple(sorted(value)) for key, value in by_key.items()}
    per_zone: dict[str, int] = {zone_id: 0 for zone_id in zone_ids}
    for value in resolved.values():
        for zone_id in value:
            per_zone[zone_id] += 1
    details = {
        "path": str(assignments_path),
        "sha256": sha256_file(assignments_path),
        "assignment_rows": len(rows),
        "points_with_zone": len(resolved),
        "points_without_zone": len(run.points) - len(resolved),
        "per_zone": per_zone,
        "boundary_method": ZONE_BOUNDARY_METHOD,
    }
    return resolved, details


def with_zone_ids(
    run: RunData, assignments: dict[tuple[int, int], tuple[str, ...]]
) -> list[Observation]:
    points = []
    for point in run.points:
        points.append(
            Observation(
                track_id=point.track_id,
                frame_index=point.frame_index,
                video_time=point.video_time,
                bbox=point.bbox,
                x=point.x,
                y=point.y,
                zone_ids=assignments.get((point.track_id, point.frame_index), ()),
                score=point.score,
            )
        )
    return points


def _next_in_zone_index(
    points: Sequence[Observation], start: int, zone_id: str
) -> int | None:
    for index in range(start, len(points)):
        if zone_id in points[index].zone_ids:
            return index
    return None


def derive_zone_segments(
    track_points: Sequence[Observation],
    zone_id: str,
    fps: float,
    max_gap_seconds: float,
    exit_tolerance_seconds: float,
    analysis_end: float,
) -> list[ZoneSegment]:
    """Build continuous in-zone episodes for one track/zone.

    A short outside excursion is tolerated only when it is observed (no long
    gap), and it never adds time to the dwell counter.  A gap longer than
    ``max_gap_seconds`` always ends the episode.
    """

    if not track_points:
        return []
    ordered = list(track_points)
    segments: list[ZoneSegment] = []
    index = 0
    while index < len(ordered):
        point = ordered[index]
        if zone_id not in point.zone_ids:
            index += 1
            continue

        # Start a new episode at this point.
        start_index = index
        selected: list[Observation] = [point]
        boundary_excursions = 0
        excursion_after_indices: list[int] = []
        cursor = index + 1
        last_in_index = index
        end_reason = "analysis_window_end"
        exit_confirmed = False
        while cursor < len(ordered):
            current = ordered[cursor]
            if zone_id in current.zone_ids:
                gap = current.video_time - selected[-1].video_time
                if gap > max_gap_seconds + 1e-9:
                    end_reason = "observation_gap"
                    break
                selected.append(current)
                last_in_index = cursor
                cursor += 1
                continue

            # Current point is outside. Look ahead for a tolerated short exit.
            next_index = _next_in_zone_index(ordered, cursor, zone_id)
            if next_index is None:
                end_reason = "left_zone"
                exit_confirmed = True
                break
            next_point = ordered[next_index]
            outside_duration = next_point.video_time - selected[-1].video_time
            gap_before_next = next_point.video_time - ordered[next_index - 1].video_time
            if (
                outside_duration <= exit_tolerance_seconds + 1e-9
                and gap_before_next <= max_gap_seconds + 1e-9
            ):
                boundary_excursions += 1
                excursion_after_indices.append(len(selected) - 1)
                cursor = next_index
                continue
            end_reason = "left_zone"
            exit_confirmed = True
            break

        last_point = selected[-1]
        # If the episode stopped without an observed exit, distinguish a track
        # that was lost from the end of the analysed window.
        if end_reason == "analysis_window_end":
            following = ordered[last_in_index + 1 :]
            if following:
                first_after = following[0]
                if first_after.video_time - last_point.video_time > max_gap_seconds + 1e-9:
                    end_reason = "observation_gap"
                elif zone_id not in first_after.zone_ids:
                    end_reason = "left_zone"
                    exit_confirmed = True
            elif last_point.video_time < analysis_end - max(0.08, 2.0 / fps):
                end_reason = "track_lost_unconfirmed"
            else:
                end_reason = "analysis_window_end"

        # Entry is observed only when the immediately preceding observation was
        # outside this zone and there was no long gap before entry.
        entry_observed = False
        if start_index > 0:
            previous = ordered[start_index - 1]
            gap = point.video_time - previous.video_time
            entry_observed = zone_id not in previous.zone_ids and gap <= max_gap_seconds + 1e-9

        left_censored = start_index == 0
        right_censored = (
            end_reason == "analysis_window_end"
            or (
                last_point.video_time >= analysis_end - max(0.08, 2.0 / fps)
                and not exit_confirmed
            )
        )

        gaps = [
            selected[i + 1].video_time - selected[i].video_time
            for i in range(len(selected) - 1)
        ]
        segments.append(
            ZoneSegment(
                segment_id=f"{point.track_id}:{zone_id}:{point.frame_index}",
                track_id=point.track_id,
                zone_id=zone_id,
                points=selected,
                fps=fps,
                entry_observed=entry_observed,
                left_censored=left_censored,
                right_censored=right_censored,
                exit_confirmed=exit_confirmed,
                end_reason=end_reason,
                boundary_excursions=boundary_excursions,
                max_gap_seconds=max(gaps) if gaps else 0.0,
                gap_count=sum(1 for gap in gaps if gap > 1.0 / fps + 1e-9),
                excursion_after_indices=excursion_after_indices,
            )
        )
        index = last_in_index + 1
    return segments


def build_zone_segments(
    run: RunData,
    points: Sequence[Observation],
    zones: Sequence[Zone],
    max_gap_seconds: float,
    exit_tolerance_seconds: float,
) -> tuple[dict[int, list[Observation]], dict[str, list[ZoneSegment]]]:
    by_track: dict[int, list[Observation]] = {}
    for point in points:
        by_track.setdefault(point.track_id, []).append(point)
    for track_points in by_track.values():
        track_points.sort(key=lambda item: item.frame_index)

    segments_by_zone: dict[str, list[ZoneSegment]] = {zone.zone_id: [] for zone in zones}
    for track_id in sorted(by_track):
        track_points = by_track[track_id]
        for zone in zones:
            segments_by_zone[zone.zone_id].extend(
                derive_zone_segments(
                    track_points,
                    zone.zone_id,
                    run.fps,
                    max_gap_seconds,
                    exit_tolerance_seconds,
                    run.analysis_end,
                )
            )
    return by_track, segments_by_zone


def build_zone_stats(
    run: RunData,
    zones: Sequence[Zone],
    points: Sequence[Observation],
    segments_by_zone: dict[str, list[ZoneSegment]],
) -> dict:
    """Per-frame zone counts and current continuous dwell maxima."""

    frame_times = [float(row["video_time"]) for row in run.frames]
    frame_indices = [int(row["frame_index"]) for row in run.frames]
    index_by_frame = {frame_index: index for index, frame_index in enumerate(frame_indices)}
    counts: dict[str, list[int]] = {zone.zone_id: [0] * len(run.frames) for zone in zones}
    max_dwell: dict[str, list[float]] = {zone.zone_id: [0.0] * len(run.frames) for zone in zones}

    for point in points:
        index = index_by_frame.get(point.frame_index)
        if index is None:
            continue
        for zone_id in point.zone_ids:
            if zone_id in counts:
                counts[zone_id][index] += 1

    for zone_id, segments in segments_by_zone.items():
        if zone_id not in max_dwell:
            continue
        for segment in segments:
            for offset, point in enumerate(segment.points):
                index = index_by_frame.get(point.frame_index)
                if index is None:
                    continue
                dwell = segment.dwell_at_index(offset)
                if dwell > max_dwell[zone_id][index]:
                    max_dwell[zone_id][index] = dwell

    zone_rows = []
    for zone in zones:
        zone_counts = counts[zone.zone_id]
        peak_count = max(zone_counts) if zone_counts else 0
        peak_index = zone_counts.index(peak_count) if zone_counts else 0
        zone_rows.append(
            {
                **zone.as_dict(),
                "counts": zone_counts,
                "max_continuous_dwell_seconds": [round(value, 3) for value in max_dwell[zone.zone_id]],
                "peak_count": peak_count,
                "peak_time": round(frame_times[peak_index], 3) if frame_times else None,
                "peak_count_note": "算法观测目标数峰值，不是人数峰值。",
            }
        )

    return {
        "schema": "e04-zone-stats-v2",
        "fps": run.fps,
        "frame_indices": frame_indices,
        "times": [round(value, 4) for value in frame_times],
        "zones": zone_rows,
        "count_definition": COUNT_DEFINITION,
        "dwell_definition": DWELL_DEFINITION,
        "valid_target_definition": (
            "该帧存在 ByteTrack 跟踪点的目标；缺帧不补点，不把轨迹首次至最后观察跨度当作同时在场。"
        ),
    }


def _candidate_id(dedup_key: str) -> str:
    return "E04-" + hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()[:12].upper()


def _evidence(run: RunData, point: Observation, zone: Zone) -> dict:
    frame_period = 1.0 / run.fps
    coverage_end = min(run.analysis_end, point.video_time + frame_period)
    return {
        "video_path": str(run.source_video),
        "video_id": run.manifest.get("video_id"),
        "camera_id": run.manifest.get("camera_id"),
        "frame_index": point.frame_index,
        "video_time": round(point.video_time, 3),
        "frame_video_time": round(point.video_time, 3),
        "frame_coverage_start": round(point.video_time, 3),
        "frame_coverage_end": round(coverage_end, 3),
        "bbox": [round(value, 3) for value in point.bbox],
        "position": [round(point.x, 3), round(point.y, 3)],
        "zone_id": zone.zone_id,
        "zone_version": zone.version,
        "evidence_source": "visible_video",
    }


def _lifecycle(
    trigger_time: float,
    last_satisfied_time: float,
    end_time: float | None,
    end_reason: str,
    ongoing_at_analysis_end: bool,
    exit_confirmed: bool,
) -> dict:
    return {
        "first_trigger_video_time": round(trigger_time, 3),
        "active_intervals": [
            {
                "start_video_time": round(trigger_time, 3),
                "end_video_time": round(last_satisfied_time, 3),
                "start_semantics": "首次满足全部条件的帧覆盖结束",
                "end_semantics": "条件持续至该帧覆盖结束；之后按结束原因终止",
            }
        ],
        "last_satisfied_video_time": round(last_satisfied_time, 3),
        "end_video_time": round(end_time, 3) if end_time is not None else None,
        "end_reason": end_reason,
        "ongoing_at_analysis_end": ongoing_at_analysis_end,
        "exit_confirmed": exit_confirmed,
        "dedup_semantics": (
            "同一规则版本、同一运行、同一轨迹、同一区域、同一停留片段只生成一条候选；"
            "持续期间不逐帧重复输出。"
        ),
    }


def evaluate_long_stay(
    run: RunData,
    zones: Sequence[Zone],
    segments_by_zone: dict[str, list[ZoneSegment]],
    config: dict,
    rule_config_hash: str,
    emitted_at_utc: str,
) -> tuple[list[dict], dict]:
    params = config["params"]
    threshold = float(params["min_continuous_observed_dwell_seconds"])
    min_points = int(params["min_observed_points"])
    zone_by_id = {zone.zone_id: zone for zone in zones}
    candidates: list[dict] = []
    evaluated_segments = 0
    below_threshold: list[dict] = []

    for zone_id, segments in segments_by_zone.items():
        zone = zone_by_id[zone_id]
        for segment in segments:
            evaluated_segments += 1
            required_points = max(int(math.ceil(threshold * run.fps - 1e-9)), min_points)
            if segment.observed_points < min_points:
                continue
            if segment.observed_dwell_seconds + 1e-9 < threshold:
                below_threshold.append(
                    {
                        "track_id": segment.track_id,
                        "zone_id": zone_id,
                        "observed_dwell_seconds": round(segment.observed_dwell_seconds, 3),
                        "threshold_seconds": threshold,
                    }
                )
                continue
            trigger_index = min(required_points, segment.observed_points) - 1
            trigger = segment.points[trigger_index]
            frame_period = 1.0 / run.fps
            trigger_coverage_end = min(
                run.analysis_end, trigger.video_time + frame_period
            )
            prefix_points = segment.points[: trigger_index + 1]
            prefix_gaps = [
                prefix_points[index + 1].video_time - prefix_points[index].video_time
                for index in range(len(prefix_points) - 1)
            ]
            prefix_max_gap = max(prefix_gaps) if prefix_gaps else 0.0
            prefix_gap_count = sum(
                1 for gap in prefix_gaps if gap > frame_period + 1e-9
            )
            prefix_boundary_excursions = sum(
                1 for excursion_index in segment.excursion_after_indices
                if excursion_index < trigger_index
            )
            observed_dwell = (trigger_index + 1) / run.fps
            coverage_elapsed = trigger_coverage_end - segment.coverage_start
            coverage_gap = max(0.0, coverage_elapsed - observed_dwell)
            dedup_key = "|".join(
                [
                    "long_stay",
                    str(config["version"]),
                    str(run.run["run_id"]),
                    str(segment.track_id),
                    zone_id,
                    segment.segment_id,
                ]
            )
            features = {
                "dwell_mode": "continuous_observed_in_zone",
                "dwell_observed_seconds": round(observed_dwell, 3),
                "coverage_elapsed_seconds": round(coverage_elapsed, 3),
                "gap_seconds": round(coverage_gap, 3),
                "threshold_seconds": threshold,
                "threshold_margin_seconds": round(observed_dwell - threshold, 3),
                "observed_points": trigger_index + 1,
                "dwell_fraction_of_span": round(
                    observed_dwell / max(1e-9, coverage_elapsed), 4
                ),
                "entry_observed": segment.entry_observed,
                "left_censored": segment.left_censored,
                "boundary_excursions": prefix_boundary_excursions,
                "max_gap_seconds": round(prefix_max_gap, 3),
                "gap_count": prefix_gap_count,
            }
            final_features = {
                "dwell_observed_seconds": round(segment.observed_dwell_seconds, 3),
                "coverage_elapsed_seconds": round(segment.span_seconds, 3),
                "gap_seconds": round(segment.gap_seconds, 3),
                "observed_points": segment.observed_points,
                "in_zone_ratio": round(segment.in_zone_ratio, 4),
                "boundary_excursions": segment.boundary_excursions,
                "max_gap_seconds": round(segment.max_gap_seconds, 3),
                "gap_count": segment.gap_count,
                "end_reason": segment.end_reason,
                "exit_confirmed": segment.exit_confirmed,
                "right_censored": segment.right_censored,
            }
            lifecycle = _lifecycle(
                trigger_time=trigger_coverage_end,
                last_satisfied_time=segment.coverage_end,
                end_time=segment.coverage_end,
                end_reason=segment.end_reason,
                ongoing_at_analysis_end=segment.right_censored
                and segment.end_reason == "analysis_window_end",
                exit_confirmed=segment.exit_confirmed,
            )
            candidates.append(
                {
                    "candidate_id": _candidate_id(dedup_key),
                    "dedup_key": dedup_key,
                    "rule_id": config["rule_id"],
                    "rule_label": config["label"],
                    "rule_version": config["version"],
                    "rule_config_sha256": rule_config_hash,
                    "status": CANDIDATE_STATUS,
                    "status_label": CANDIDATE_STATUS_LABEL,
                    "run_id": run.run["run_id"],
                    "camera_id": run.manifest.get("camera_id"),
                    "zone_id": zone.zone_id,
                    "zone_name": zone.name,
                    "zone_version": zone.version,
                    "track_id": segment.track_id,
                    "trace_id": f"{run.run.get('source', {}).get('site_id', 'public_demo')}/"
                    f"{run.manifest.get('camera_id')}/{run.run['run_id']}/{segment.track_id}",
                    "condition_met_video_time": round(trigger_coverage_end, 3),
                    "condition_met_frame_index": trigger.frame_index,
                    "evidence_frame_video_time": round(trigger.video_time, 3),
                    "frame_coverage_start": round(trigger.video_time, 3),
                    "frame_coverage_end": round(trigger_coverage_end, 3),
                    "simulated_trigger_video_time": round(trigger_coverage_end, 3),
                    "output_video_time": round(trigger_coverage_end, 3),
                    "emitted_at_utc": emitted_at_utc,
                    "output_mode": "offline_sequential_replay_then_batch_write",
                    "latency_note": (
                        "条件满足时刻为视频时间；批处理写盘时间不等于现场告警延迟。"
                    ),
                    "lifecycle": lifecycle,
                    "trigger_features": features,
                    "final_features": final_features,
                    "evidence": _evidence(run, trigger, zone),
                    "evidence_clip": {
                        "start_video_time": round(max(0.0, trigger.video_time - 2.0), 3),
                        "end_video_time": round(
                            min(run.analysis_end, trigger.video_time + 2.0), 3
                        ),
                        "pre_seconds": 2.0,
                        "post_seconds": 2.0,
                    },
                    "thresholds": {
                        "min_continuous_observed_dwell_seconds": threshold,
                        "min_observed_points": min_points,
                        "max_gap_seconds": float(params.get("max_gap_seconds", 0.4)),
                        "zone_exit_tolerance_seconds": float(
                            params.get("zone_exit_tolerance_seconds", 0.24)
                        ),
                        "frame_period_seconds": round(frame_period, 4),
                    },
                    "calibration_status": config.get("calibration_status"),
                    "caveats": [
                        "区域为公开演示区域，不是现场业务区域。",
                        "停留时间是可观测下界，进入前/离开后的时间未知。",
                        "规则条件触发不等于危险、违法或需要处置，必须人工复核。",
                    ],
                }
            )

    status = {
        "rule_id": config["rule_id"],
        "label": config["label"],
        "version": config["version"],
        "enabled": bool(config.get("enabled", True)),
        "evaluation_status": "evaluated" if config.get("enabled", True) else "disabled",
        "candidate_count": len(candidates),
        "evaluated_segments": evaluated_segments,
        "below_threshold_count": len(below_threshold),
        "closest_below_threshold": sorted(
            below_threshold, key=lambda item: item["observed_dwell_seconds"], reverse=True
        )[:3],
        "zero_candidate_reason": None if candidates else "当前片段没有停留片段达到演示阈值。",
        "calibration_status": config.get("calibration_status"),
        "status_text": (
            f"规则已运行：{len(candidates)} 条候选；"
            f"{len(below_threshold)} 个停留片段未达到 {threshold:.1f} 秒演示阈值。"
        ),
    }
    return candidates, status


def _smooth_trailing(
    points: Sequence[Observation], window_points: int
) -> list[tuple[float, float, float]]:
    """Causal trailing mean; future observations never change earlier values."""

    window_points = max(1, int(window_points))
    smoothed: list[tuple[float, float, float]] = []
    for index, point in enumerate(points):
        start = max(0, index - window_points + 1)
        chunk = points[start : index + 1]
        smoothed.append(
            (
                sum(item.x for item in chunk) / len(chunk),
                sum(item.y for item in chunk) / len(chunk),
                point.video_time,
            )
        )
    return smoothed


def _simplify_rdp(
    points: Sequence[tuple[float, float, float]], epsilon: float
) -> list[tuple[float, float, float]]:
    """Ramer-Douglas-Peucker simplification for a causal path prefix."""

    if len(points) <= 2:
        return list(points)

    start = points[0]
    end = points[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    max_distance = -1.0
    split_index = -1
    if denominator <= 1e-9:
        for index in range(1, len(points) - 1):
            distance = math.hypot(points[index][0] - start[0], points[index][1] - start[1])
            if distance > max_distance:
                max_distance = distance
                split_index = index
    else:
        for index in range(1, len(points) - 1):
            projection = (
                (points[index][0] - start[0]) * dx
                + (points[index][1] - start[1]) * dy
            ) / denominator
            projection = max(0.0, min(1.0, projection))
            nearest_x = start[0] + projection * dx
            nearest_y = start[1] + projection * dy
            distance = math.hypot(points[index][0] - nearest_x, points[index][1] - nearest_y)
            if distance > max_distance:
                max_distance = distance
                split_index = index

    if max_distance <= epsilon or split_index < 0:
        return [start, end]
    left = _simplify_rdp(points[: split_index + 1], epsilon)
    right = _simplify_rdp(points[split_index:], epsilon)
    return left[:-1] + right


def _principal_axis_deviation(
    points: Sequence[tuple[float, float, float]],
) -> tuple[tuple[float, float], float]:
    """Return the principal axis and maximum perpendicular deviation."""

    count = len(points)
    if count < 2:
        return (1.0, 0.0), 0.0
    mean_x = sum(point[0] for point in points) / count
    mean_y = sum(point[1] for point in points) / count
    cov_xx = sum((point[0] - mean_x) ** 2 for point in points) / count
    cov_yy = sum((point[1] - mean_y) ** 2 for point in points) / count
    cov_xy = sum(
        (point[0] - mean_x) * (point[1] - mean_y) for point in points
    ) / count
    angle = 0.5 * math.atan2(2.0 * cov_xy, cov_xx - cov_yy)
    axis = (math.cos(angle), math.sin(angle))
    perpendicular = (-axis[1], axis[0])
    max_deviation = 0.0
    for point in points:
        dx = point[0] - mean_x
        dy = point[1] - mean_y
        max_deviation = max(
            max_deviation, abs(dx * perpendicular[0] + dy * perpendicular[1])
        )
    return axis, max_deviation


def _simplify_direction_aware(
    points: Sequence[tuple[float, float, float]],
    min_leg: float,
    collinearity_tolerance: float,
) -> list[tuple[float, float, float]]:
    """Simplify a path while preserving same-line back-and-forth turns.

    Standard 2D RDP collapses a collinear out-and-back path to its endpoints
    because every point lies on the same line.  For nearly collinear paths we
    therefore simplify ``(cumulative arc length, principal-axis projection)``:
    the monotonic arc coordinate keeps the time order, while the projection
    preserves the turning extrema.
    """

    if len(points) <= 2:
        return list(points)
    axis, deviation = _principal_axis_deviation(points)
    epsilon = max(1.0, min_leg * 0.25)
    if deviation <= max(4.0, collinearity_tolerance):
        mean_x = sum(point[0] for point in points) / len(points)
        mean_y = sum(point[1] for point in points) / len(points)
        arc = 0.0
        transformed: list[tuple[float, float, float]] = []
        previous = None
        for index, point in enumerate(points):
            if previous is not None:
                arc += math.hypot(point[0] - previous[0], point[1] - previous[1])
            projection = (
                (point[0] - mean_x) * axis[0] + (point[1] - mean_y) * axis[1]
            )
            transformed.append((arc, projection, float(index)))
            previous = point
        simplified = _simplify_rdp(transformed, epsilon)
        return [points[int(round(item[2]))] for item in simplified]
    return _simplify_rdp(points, epsilon)


def _angle_degrees(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    length_first = math.hypot(*first)
    length_second = math.hypot(*second)
    if length_first <= 0 or length_second <= 0:
        return 0.0
    cosine = (first[0] * second[0] + first[1] * second[1]) / (length_first * length_second)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def _reversal_features(
    points: Sequence[Observation], params: dict
) -> dict:
    """Causal trailing-window reversal features.

    The window is trimmed at the last long gap so a reversal never bridges a
    track break.  Smoothing and simplification are trailing-only, hence
    truncating future frames cannot change an earlier feature value.

    Near-zero net displacement is deliberately allowed: a target that walks
    out and back along the same line has low path efficiency but can still
    contain valid reversals.  Static jitter is excluded by requiring a
    sufficient total length of **valid legs** (each leg must exceed the
    displacement and duration noise thresholds), not by requiring net motion.
    """

    max_gap = float(params["max_gap_seconds"])
    window_seconds = float(params["window_seconds"])
    min_history = float(params["min_history_seconds"])
    min_points = int(params["min_observed_points"])
    smoothing = int(params["smoothing_window_points"])
    min_leg = float(params["min_leg_displacement_px"])
    min_leg_time = float(params["min_leg_duration_seconds"])
    min_turn = float(params["min_turn_angle_degrees"])
    min_path = float(params["min_path_length_px"])
    min_valid_motion = float(params["min_valid_motion_length_px"])

    if not points:
        return {
            "computable": False,
            "reason": "no_observations",
            "effective_reversals": 0,
            "path_efficiency": None,
            "path_length_px": 0.0,
            "net_displacement_px": 0.0,
            "valid_leg_length_px": 0.0,
            "valid_leg_count": 0,
            "stationary": True,
        }

    current_time = points[-1].video_time
    window = [
        point
        for point in points
        if point.video_time >= current_time - window_seconds - 1e-9
    ]
    if len(window) < min_points:
        return {
            "computable": False,
            "reason": "insufficient_observations",
            "effective_reversals": 0,
            "path_efficiency": None,
            "path_length_px": 0.0,
            "net_displacement_px": 0.0,
            "valid_leg_length_px": 0.0,
            "valid_leg_count": 0,
            "stationary": True,
            "observed_seconds": round(len(window) / max(1.0, _fps_hint(points)), 3),
        }
    # Trim at the last long gap (keep the newest continuous observation run).
    run_start = 0
    for index in range(1, len(window)):
        if window[index].video_time - window[index - 1].video_time > max_gap + 1e-9:
            run_start = index
    trimmed = window[run_start:]
    if len(trimmed) < min_points:
        return {
            "computable": False,
            "reason": "insufficient_continuous_observations",
            "effective_reversals": 0,
            "path_efficiency": None,
            "path_length_px": 0.0,
            "net_displacement_px": 0.0,
            "valid_leg_length_px": 0.0,
            "valid_leg_count": 0,
            "stationary": True,
        }

    frame_period = 1.0 / _fps_hint(trimmed)
    observed_seconds = trimmed[-1].video_time - trimmed[0].video_time + frame_period
    if observed_seconds < min_history - 1e-9:
        return {
            "computable": False,
            "reason": "insufficient_history",
            "effective_reversals": 0,
            "path_efficiency": None,
            "path_length_px": 0.0,
            "net_displacement_px": 0.0,
            "valid_leg_length_px": 0.0,
            "valid_leg_count": 0,
            "stationary": True,
            "observed_seconds": round(observed_seconds, 3),
        }

    smoothed = _smooth_trailing(trimmed, smoothing)
    path_length = sum(
        math.hypot(
            smoothed[index][0] - smoothed[index - 1][0],
            smoothed[index][1] - smoothed[index - 1][1],
        )
        for index in range(1, len(smoothed))
    )
    net_displacement = math.hypot(
        smoothed[-1][0] - smoothed[0][0], smoothed[-1][1] - smoothed[0][1]
    )

    # Direction-aware simplification preserves same-line back-and-forth turns;
    # ordinary RDP is used for genuinely 2D paths.
    simplified = _simplify_direction_aware(
        smoothed,
        min_leg,
        float(params.get("collinearity_tolerance_px", 12.0)),
    )
    legs: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for index in range(1, len(simplified)):
        first = simplified[index - 1]
        second = simplified[index]
        length = math.hypot(second[0] - first[0], second[1] - first[1])
        if (
            length >= min_leg - 1e-9
            and second[2] - first[2] >= min_leg_time - 1e-9
        ):
            legs.append((first, second))

    effective_reversals = 0
    turn_times: list[float] = []
    valid_leg_length = 0.0
    valid_motion_seconds = 0.0
    for first, second in legs:
        valid_leg_length += math.hypot(
            second[0] - first[0], second[1] - first[1]
        )
        valid_motion_seconds += max(0.0, second[2] - first[2])
    for index in range(1, len(legs)):
        first = legs[index - 1]
        second = legs[index]
        vector_first = (first[1][0] - first[0][0], first[1][1] - first[0][1])
        vector_second = (second[1][0] - second[0][0], second[1][1] - second[0][1])
        angle = _angle_degrees(vector_first, vector_second)
        if angle >= min_turn - 1e-9:
            effective_reversals += 1
            turn_times.append(second[1][2])

    efficiency_computable = (
        path_length >= min_path - 1e-9
        and valid_leg_length >= min_valid_motion - 1e-9
        and observed_seconds >= min_history - 1e-9
        and len(trimmed) >= min_points
    )
    path_efficiency = (
        net_displacement / path_length
        if efficiency_computable and path_length > 0
        else None
    )
    if efficiency_computable:
        reason = "computable"
    elif path_length < min_path:
        reason = "path_below_noise_threshold"
    elif valid_leg_length < min_valid_motion:
        reason = "valid_motion_below_threshold"
    else:
        reason = "insufficient_history"

    return {
        "computable": efficiency_computable,
        "reason": reason,
        "effective_reversals": effective_reversals,
        "path_efficiency": round(path_efficiency, 4) if path_efficiency is not None else None,
        "path_length_px": round(path_length, 3),
        "net_displacement_px": round(net_displacement, 3),
        "valid_leg_length_px": round(valid_leg_length, 3),
        "valid_leg_count": len(legs),
        "valid_motion_seconds": round(valid_motion_seconds, 3),
        "stationary": not efficiency_computable and valid_leg_length < min_valid_motion,
        "observed_seconds": round(observed_seconds, 3),
        "observed_points": len(trimmed),
        "leg_count": len(legs),
        "turn_times": [round(value, 3) for value in turn_times[-5:]],
        "max_gap_seconds": round(max_gap, 3),
        "smoothing": "causal_trailing_mean",
        "smoothing_window_points": smoothing,
        "simplification": "direction_aware_1d_or_rdp_2d",
    }


def prefix_run(run: RunData, cutoff_seconds: float) -> RunData:
    """Return a causal prefix of a run for truncation/causality checks.

    Only observations and frames at or before ``cutoff_seconds`` are kept.
    Track summaries and provenance hashes are intentionally unchanged because
    they are metadata, not future observations used by the rules.
    """

    cutoff = float(cutoff_seconds)
    points = [point for point in run.points if point.video_time <= cutoff + 1e-9]
    frames = [
        frame for frame in run.frames if float(frame["video_time"]) <= cutoff + 1e-9
    ]
    if not points or not frames:
        raise E04ValidationError(f"截断到 {cutoff:.2f} 秒后没有观测")
    points_by_track: dict[int, list[Observation]] = {}
    for point in points:
        points_by_track.setdefault(point.track_id, []).append(point)
    for track_points in points_by_track.values():
        track_points.sort(key=lambda item: item.frame_index)
    return RunData(
        run_dir=run.run_dir,
        run=run.run,
        manifest=run.manifest,
        frames=frames,
        summaries=run.summaries,
        points=points,
        points_by_track=points_by_track,
        fps=run.fps,
        analysis_start=run.analysis_start,
        analysis_end=cutoff,
        source_video=run.source_video,
        hashes=run.hashes,
    )


def candidate_trigger_signature(candidate: dict) -> dict:
    """Fields that must be stable when future observations are appended."""

    return {
        "candidate_id": candidate.get("candidate_id"),
        "dedup_key": candidate.get("dedup_key"),
        "rule_id": candidate.get("rule_id"),
        "rule_version": candidate.get("rule_version"),
        "track_id": candidate.get("track_id"),
        "zone_id": candidate.get("zone_id"),
        "condition_met_video_time": candidate.get("condition_met_video_time"),
        "condition_met_frame_index": candidate.get("condition_met_frame_index"),
        "evidence_frame_video_time": candidate.get("evidence_frame_video_time"),
        "frame_coverage_start": candidate.get("frame_coverage_start"),
        "frame_coverage_end": candidate.get("frame_coverage_end"),
        "simulated_trigger_video_time": candidate.get("simulated_trigger_video_time"),
        "trigger_features": candidate.get("trigger_features"),
    }


def _fps_hint(points: Sequence[Observation]) -> float:
    """Infer a stable frame period from consecutive observations."""

    diffs = [
        points[index + 1].video_time - points[index].video_time
        for index in range(len(points) - 1)
        if points[index + 1].video_time > points[index].video_time
    ]
    if not diffs:
        return 25.0
    diffs.sort()
    median = diffs[len(diffs) // 2]
    return 1.0 / median if median > 0 else 25.0


def _movement_segments(
    points: Sequence[Observation], max_gap_seconds: float
) -> list[list[Observation]]:
    if not points:
        return []
    segments: list[list[Observation]] = []
    current = [points[0]]
    for point in points[1:]:
        if point.video_time - current[-1].video_time > max_gap_seconds + 1e-9:
            segments.append(current)
            current = []
        current.append(point)
    if current:
        segments.append(current)
    return segments


def evaluate_reversal(
    run: RunData,
    zones: Sequence[Zone],
    by_track: dict[int, list[Observation]],
    config: dict,
    rule_config_hash: str,
    emitted_at_utc: str,
) -> tuple[list[dict], dict]:
    params = config["params"]
    min_reversals = int(params["min_effective_reversals"])
    max_efficiency = float(params["max_path_efficiency"])
    candidates: list[dict] = []
    near_misses: list[dict] = []
    evaluated_segments = 0
    zone_by_id = {zone.zone_id: zone for zone in zones}

    for track_id in sorted(by_track):
        for movement in _movement_segments(by_track[track_id], float(params["max_gap_seconds"])):
            if len(movement) < int(params["min_observed_points"]):
                continue
            evaluated_segments += 1
            active_segment: dict | None = None
            active_intervals: list[dict] = []
            max_reversals = 0
            min_efficiency: float | None = None
            trigger_features: dict | None = None
            trigger_point: Observation | None = None
            best_features: dict | None = None

            for index in range(len(movement)):
                prefix = movement[: index + 1]
                features = _reversal_features(prefix, params)
                max_reversals = max(max_reversals, int(features.get("effective_reversals", 0)))
                efficiency = features.get("path_efficiency")
                if efficiency is not None:
                    min_efficiency = (
                        efficiency
                        if min_efficiency is None
                        else min(min_efficiency, float(efficiency))
                    )
                if (
                    features.get("computable")
                    and int(features.get("effective_reversals", 0)) >= min_reversals
                    and float(features.get("path_efficiency") or 1.0) < max_efficiency
                ):
                    point = movement[index]
                    if active_segment is None:
                        active_segment = {
                            "trigger_index": index,
                            "trigger_point": point,
                            "trigger_features": features,
                            "last_satisfied_index": index,
                        }
                        active_intervals.append(
                            {
                                "start_index": index,
                                "start_video_time": point.video_time,
                            }
                        )
                    else:
                        active_segment["last_satisfied_index"] = index
                        active_intervals[-1]["end_video_time"] = point.video_time
                        active_intervals[-1]["end_index"] = index
                elif active_segment is not None and "end_video_time" not in active_intervals[-1]:
                    active_intervals[-1]["end_video_time"] = movement[index - 1].video_time
                    active_intervals[-1]["end_index"] = index - 1
                    if trigger_features is None:
                        trigger_features = active_segment["trigger_features"]
                        trigger_point = active_segment["trigger_point"]
            if active_segment is not None:
                if "end_video_time" not in active_intervals[-1]:
                    active_intervals[-1]["end_video_time"] = movement[
                        active_segment["last_satisfied_index"]
                    ].video_time
                    active_intervals[-1]["end_index"] = active_segment["last_satisfied_index"]
                trigger_point = active_segment["trigger_point"]
                trigger_features = active_segment["trigger_features"]
                best_features = _reversal_features(movement, params)

            if active_segment is None or trigger_point is None or trigger_features is None:
                near_misses.append(
                    {
                        "track_id": track_id,
                        "max_effective_reversals": max_reversals,
                        "min_path_efficiency": min_efficiency,
                        "required_reversals": min_reversals,
                        "max_path_efficiency": max_efficiency,
                        "reason": "no_interval_met_all_conditions",
                    }
                )
                continue

            # Attach the zone that contains the trigger point, if any.  A
            # reversal may span multiple zones; the trigger zone is only the
            # evidence location, not an additional condition.
            trigger_zone = (
                zone_by_id[trigger_point.zone_ids[0]]
                if trigger_point.zone_ids
                else None
            )
            dedup_key = "|".join(
                [
                    "reversal",
                    str(config["version"]),
                    str(run.run["run_id"]),
                    str(track_id),
                    movement[0].frame_index.__str__(),
                ]
            )
            frame_period = 1.0 / run.fps
            trigger_coverage_end = min(
                run.analysis_end, trigger_point.video_time + frame_period
            )
            movement_end_coverage = min(
                run.analysis_end, movement[-1].video_time + frame_period
            )
            end_reason = (
                "analysis_window_end"
                if movement[-1].video_time >= run.analysis_end - max(0.08, 2.0 / run.fps)
                else "track_lost_unconfirmed"
            )
            final_features = best_features or trigger_features
            candidate = {
                "candidate_id": _candidate_id(dedup_key),
                "dedup_key": dedup_key,
                "rule_id": config["rule_id"],
                "rule_label": config["label"],
                "rule_version": config["version"],
                "rule_config_sha256": rule_config_hash,
                "status": CANDIDATE_STATUS,
                "status_label": CANDIDATE_STATUS_LABEL,
                "run_id": run.run["run_id"],
                "camera_id": run.manifest.get("camera_id"),
                "zone_id": trigger_zone.zone_id if trigger_zone else None,
                "zone_name": trigger_zone.name if trigger_zone else "轨迹跨区域",
                "zone_version": trigger_zone.version if trigger_zone else None,
                "track_id": track_id,
                "trace_id": f"{run.run.get('source', {}).get('site_id', 'public_demo')}/"
                f"{run.manifest.get('camera_id')}/{run.run['run_id']}/{track_id}",
                "condition_met_video_time": round(trigger_coverage_end, 3),
                "condition_met_frame_index": trigger_point.frame_index,
                "evidence_frame_video_time": round(trigger_point.video_time, 3),
                "frame_coverage_start": round(trigger_point.video_time, 3),
                "frame_coverage_end": round(trigger_coverage_end, 3),
                "simulated_trigger_video_time": round(trigger_coverage_end, 3),
                "output_video_time": round(trigger_coverage_end, 3),
                "emitted_at_utc": emitted_at_utc,
                "output_mode": "offline_sequential_replay_then_batch_write",
                "latency_note": (
                    "条件满足时刻为视频时间；批处理写盘时间不等于现场告警延迟。"
                ),
                "lifecycle": {
                    "first_trigger_video_time": round(trigger_coverage_end, 3),
                    "active_intervals": [
                        {
                            "start_video_time": round(
                                min(
                                    run.analysis_end,
                                    interval["start_video_time"] + frame_period,
                                ),
                                3,
                            ),
                            "end_video_time": round(
                                min(
                                    run.analysis_end,
                                    interval["end_video_time"] + frame_period,
                                ),
                                3,
                            ),
                            "start_semantics": "首次满足全部条件",
                            "end_semantics": "条件不再满足或轨迹结束",
                        }
                        for interval in active_intervals
                    ],
                    "last_satisfied_video_time": round(
                        min(
                            run.analysis_end,
                            active_intervals[-1]["end_video_time"] + frame_period,
                        ),
                        3,
                    ),
                    "end_video_time": round(movement_end_coverage, 3),
                    "end_reason": end_reason,
                    "ongoing_at_analysis_end": end_reason == "analysis_window_end",
                    "exit_confirmed": False,
                    "dedup_semantics": (
                        "同一规则版本、同一运行、同一连续轨迹片段只生成一条候选；"
                        "条件持续期间不逐帧重复输出。"
                    ),
                },
                "trigger_features": trigger_features,
                "final_features": final_features,
                "evidence": (
                    _evidence(run, trigger_point, trigger_zone)
                    if trigger_zone
                    else {
                        "video_path": str(run.source_video),
                        "video_id": run.manifest.get("video_id"),
                        "camera_id": run.manifest.get("camera_id"),
                        "frame_index": trigger_point.frame_index,
                        "video_time": round(trigger_point.video_time, 3),
                        "bbox": [round(value, 3) for value in trigger_point.bbox],
                        "position": [round(trigger_point.x, 3), round(trigger_point.y, 3)],
                        "zone_id": None,
                        "zone_version": None,
                        "evidence_source": "visible_video",
                    }
                ),
                "evidence_clip": {
                    "start_video_time": round(max(0.0, trigger_point.video_time - 2.0), 3),
                    "end_video_time": round(
                        min(run.analysis_end, trigger_coverage_end + 2.0), 3
                    ),
                    "pre_seconds": 2.0,
                    "post_seconds": 2.0,
                },
                "thresholds": {
                    "window_seconds": float(params["window_seconds"]),
                    "min_effective_reversals": min_reversals,
                    "max_path_efficiency": max_efficiency,
                    "min_leg_displacement_px": float(params["min_leg_displacement_px"]),
                    "min_turn_angle_degrees": float(params["min_turn_angle_degrees"]),
                    "min_path_length_px": float(params["min_path_length_px"]),
                    "min_valid_motion_length_px": float(
                        params["min_valid_motion_length_px"]
                    ),
                    "min_history_seconds": float(params["min_history_seconds"]),
                    "max_gap_seconds": float(params["max_gap_seconds"]),
                },
                "calibration_status": config.get("calibration_status"),
                "caveats": [
                    "距离单位为像素；没有相机标定，不解释为米或米/秒。",
                    "有效运动段不足时路径效率不可计算，不会判为折返；静止目标改用静止特征。",
                    "规则条件触发不等于危险、违法或需要处置，必须人工复核。",
                ],
            }
            candidates.append(candidate)

    near_misses.sort(
        key=lambda item: (
            -item["max_effective_reversals"],
            item["min_path_efficiency"] if item["min_path_efficiency"] is not None else 9.9,
        )
    )
    status = {
        "rule_id": config["rule_id"],
        "label": config["label"],
        "version": config["version"],
        "enabled": bool(config.get("enabled", True)),
        "evaluation_status": "evaluated" if config.get("enabled", True) else "disabled",
        "candidate_count": len(candidates),
        "evaluated_segments": evaluated_segments,
        "near_misses": near_misses[:5],
        "zero_candidate_reason": (
            None
            if candidates
            else "当前片段没有轨迹同时满足有效反转数与路径效率条件。"
        ),
        "calibration_status": config.get("calibration_status"),
        "status_text": (
            f"规则已运行：{len(candidates)} 条候选；"
            f"最接近的轨迹最多 {near_misses[0]['max_effective_reversals']} 次有效反转。"
            if near_misses
            else f"规则已运行：{len(candidates)} 条候选。"
        ),
    }
    return candidates, status


def evaluate_occupancy_change(
    run: RunData,
    zones: Sequence[Zone],
    zone_stats: dict,
    config: dict,
    rule_config_hash: str,
    emitted_at_utc: str,
    points: Sequence[Observation] | None = None,
) -> tuple[list[dict], dict]:
    params = config["params"]
    window_seconds = float(params["window_seconds"])
    observed_seconds = run.analysis_end - run.analysis_start
    status = {
        "rule_id": config["rule_id"],
        "label": config["label"],
        "version": config["version"],
        "enabled": bool(config.get("enabled", True)),
        "evaluation_status": "insufficient_observation",
        "candidate_count": 0,
        "observed_seconds": round(observed_seconds, 3),
        "required_seconds": window_seconds,
        "shortfall_seconds": round(max(0.0, window_seconds - observed_seconds), 3),
        "zero_candidate_reason": (
            f"观察时长不足：当前 {observed_seconds:.2f} 秒，少于规则原定义 "
            f"{window_seconds:.0f} 秒窗口；没有缩短窗口。"
        ),
        "calibration_status": config.get("calibration_status"),
        "status_text": (
            f"观察时长不足：{observed_seconds:.2f} 秒 < {window_seconds:.0f} 秒；"
            "未评价，不通过缩短窗口冒充原定义。"
        ),
    }
    if observed_seconds + 1e-9 < window_seconds:
        return [], status

    # The branch below is kept for synthetic boundary tests and future field
    # data.  It is never reached by the 12-second public sample.
    times = zone_stats["times"]
    fps = float(zone_stats["fps"])
    frame_period = 1.0 / fps
    window_frames = int(round(window_seconds * fps))
    sustain_frames = max(1, int(round(float(params.get("sustain_seconds", 2.0)) * fps)))
    from_below = int(params["from_below"])
    to_at_least = int(params["to_at_least"])
    candidates: list[dict] = []
    evidence_points = list(points) if points is not None else list(run.points)
    for zone_row in zone_stats["zones"]:
        counts = zone_row["counts"]
        if len(counts) <= window_frames:
            continue
        episodes: list[tuple[int, int]] = []
        episode_start: int | None = None
        for index in range(window_frames, len(counts)):
            base = counts[index - window_frames]
            current = counts[index]
            sustained = all(
                counts[min(len(counts) - 1, index + offset)] >= to_at_least
                for offset in range(sustain_frames)
            )
            condition = base < from_below and current >= to_at_least and sustained
            if condition and episode_start is None:
                episode_start = index
            elif not condition and episode_start is not None:
                episodes.append((episode_start, index - 1))
                episode_start = None
        if episode_start is not None:
            episodes.append((episode_start, len(counts) - 1))

        for start_index, end_index in episodes:
            zone = next(zone for zone in zones if zone.zone_id == zone_row["zone_id"])
            point = next(
                (
                    item
                    for item in evidence_points
                    if item.frame_index == zone_stats["frame_indices"][start_index]
                    and zone.zone_id in item.zone_ids
                ),
                None,
            )
            if point is None:
                continue
            dedup_key = "|".join(
                [
                    "occupancy_change_60s",
                    str(config["version"]),
                    str(run.run["run_id"]),
                    zone.zone_id,
                    str(zone_stats["frame_indices"][start_index]),
                ]
            )
            base_count = counts[max(0, start_index - window_frames)]
            current_count = counts[start_index]
            trigger_frame_time = float(zone_stats["times"][start_index])
            trigger_coverage_end = min(
                run.analysis_end, trigger_frame_time + frame_period
            )
            end_coverage_time = min(
                run.analysis_end,
                float(zone_stats["times"][end_index]) + frame_period,
            )
            candidates.append(
                {
                    "candidate_id": _candidate_id(dedup_key),
                    "dedup_key": dedup_key,
                    "rule_id": config["rule_id"],
                    "rule_label": config["label"],
                    "rule_version": config["version"],
                    "rule_config_sha256": rule_config_hash,
                    "status": CANDIDATE_STATUS,
                    "status_label": CANDIDATE_STATUS_LABEL,
                    "run_id": run.run["run_id"],
                    "camera_id": run.manifest.get("camera_id"),
                    "zone_id": zone.zone_id,
                    "zone_name": zone.name,
                    "zone_version": zone.version,
                    "track_id": None,
                    "trace_id": None,
                    "condition_met_video_time": round(trigger_coverage_end, 3),
                    "condition_met_frame_index": zone_stats["frame_indices"][start_index],
                    "evidence_frame_video_time": round(trigger_frame_time, 3),
                    "frame_coverage_start": round(trigger_frame_time, 3),
                    "frame_coverage_end": round(trigger_coverage_end, 3),
                    "simulated_trigger_video_time": round(trigger_coverage_end, 3),
                    "output_video_time": round(trigger_coverage_end, 3),
                    "emitted_at_utc": emitted_at_utc,
                    "output_mode": "offline_sequential_replay_then_batch_write",
                    "latency_note": "批处理写盘时间不等于现场告警延迟。",
                    "lifecycle": {
                        "first_trigger_video_time": round(trigger_coverage_end, 3),
                        "active_intervals": [
                            {
                                "start_video_time": round(trigger_coverage_end, 3),
                                "end_video_time": round(end_coverage_time, 3),
                                "start_semantics": "首次满足全部条件",
                                "end_semantics": "条件不再满足或观察窗口结束",
                            }
                        ],
                        "last_satisfied_video_time": round(end_coverage_time, 3),
                        "end_video_time": round(end_coverage_time, 3),
                        "end_reason": "condition_no_longer_met",
                        "ongoing_at_analysis_end": end_index >= len(counts) - 1,
                        "exit_confirmed": False,
                        "dedup_semantics": (
                            "同一规则版本、同一运行、同一区域、同一条件片段只生成一条候选；"
                            "持续期间不逐帧重复输出。"
                        ),
                    },
                    "trigger_features": {
                        "window_seconds": window_seconds,
                        "count_before": base_count,
                        "count_at_trigger": current_count,
                        "from_below": from_below,
                        "to_at_least": to_at_least,
                        "sustain_seconds": float(params.get("sustain_seconds", 2.0)),
                    },
                    "evidence": {
                        "video_path": str(run.source_video),
                        "video_id": run.manifest.get("video_id"),
                        "camera_id": run.manifest.get("camera_id"),
                        "frame_index": point.frame_index,
                        "video_time": round(point.video_time, 3),
                        "frame_video_time": round(point.video_time, 3),
                        "frame_coverage_start": round(point.video_time, 3),
                        "frame_coverage_end": round(
                            min(run.analysis_end, point.video_time + frame_period), 3
                        ),
                        "bbox": [round(value, 3) for value in point.bbox],
                        "position": [round(point.x, 3), round(point.y, 3)],
                        "zone_id": zone.zone_id,
                        "zone_version": zone.version,
                        "evidence_source": "visible_video",
                    },
                    "thresholds": params,
                    "calibration_status": config.get("calibration_status"),
                    "caveats": [
                        "区域人数是算法观测数量，不是独立人数。",
                        "规则条件触发不等于危险聚集或需要处置。",
                    ],
                }
            )
    status.update(
        {
            "evaluation_status": "evaluated",
            "candidate_count": len(candidates),
            "zero_candidate_reason": None if candidates else "完整窗口内未出现定义的人数变化。",
            "status_text": f"规则已运行：{len(candidates)} 条候选。",
        }
    )
    return candidates, status


def evaluate_rules(
    run: RunData,
    zones: Sequence[Zone],
    assignments: dict[tuple[int, int], tuple[str, ...]],
    config: dict,
    input_hashes: dict[str, str] | None = None,
) -> dict:
    config_hash = hash_obj(config)
    emitted_at_utc = now_utc()
    points = with_zone_ids(run, assignments)
    max_gap = float(config["global"]["max_gap_seconds"])
    exit_tolerance = float(config["global"]["zone_exit_tolerance_seconds"])
    by_track, segments_by_zone = build_zone_segments(
        run, points, zones, max_gap, exit_tolerance
    )
    zone_stats = build_zone_stats(run, zones, points, segments_by_zone)

    rule_by_id = {rule["rule_id"]: rule for rule in config["rules"]}
    all_candidates: list[dict] = []
    rule_statuses: list[dict] = []

    long_stay = rule_by_id["long_stay"]
    if long_stay.get("enabled", True):
        candidates, status = evaluate_long_stay(
            run, zones, segments_by_zone, long_stay, config_hash, emitted_at_utc
        )
    else:
        candidates, status = [], {
            "rule_id": long_stay["rule_id"],
            "label": long_stay["label"],
            "version": long_stay["version"],
            "enabled": False,
            "evaluation_status": "disabled",
            "candidate_count": 0,
            "zero_candidate_reason": "规则已关闭，未运行。",
            "status_text": "规则已关闭，未运行。",
        }
    all_candidates.extend(candidates)
    rule_statuses.append(status)

    reversal = rule_by_id["reversal"]
    if reversal.get("enabled", True):
        candidates, status = evaluate_reversal(
            run, zones, by_track, reversal, config_hash, emitted_at_utc
        )
    else:
        candidates, status = [], {
            "rule_id": reversal["rule_id"],
            "label": reversal["label"],
            "version": reversal["version"],
            "enabled": False,
            "evaluation_status": "disabled",
            "candidate_count": 0,
            "zero_candidate_reason": "规则已关闭，未运行。",
            "status_text": "规则已关闭，未运行。",
        }
    all_candidates.extend(candidates)
    rule_statuses.append(status)

    occupancy = rule_by_id["occupancy_change_60s"]
    if occupancy.get("enabled", True):
        candidates, status = evaluate_occupancy_change(
            run, zones, zone_stats, occupancy, config_hash, emitted_at_utc, points=points
        )
    else:
        candidates, status = [], {
            "rule_id": occupancy["rule_id"],
            "label": occupancy["label"],
            "version": occupancy["version"],
            "enabled": False,
            "evaluation_status": "disabled",
            "candidate_count": 0,
            "zero_candidate_reason": "规则已关闭，未运行。",
            "status_text": "规则已关闭，未运行。",
        }
    all_candidates.extend(candidates)
    rule_statuses.append(status)

    all_candidates.sort(
        key=lambda item: (
            float(item.get("condition_met_video_time") or 0.0),
            str(item.get("rule_id")),
            str(item.get("candidate_id")),
        )
    )
    dedup_keys = [item["dedup_key"] for item in all_candidates]
    if len(dedup_keys) != len(set(dedup_keys)):
        raise E04ValidationError("候选去重键重复")

    for status in rule_statuses:
        rule = rule_by_id.get(status.get("rule_id"))
        if rule:
            status["definition"] = rule.get("definition")
            status["params"] = rule.get("params")
            status["notes"] = rule.get("notes", [])
            status.setdefault("calibration_status", rule.get("calibration_status"))
        status["candidate_status_label"] = CANDIDATE_STATUS_LABEL

    segment_rows = [
        {
            **segment.as_dict(),
            "camera_id": run.manifest.get("camera_id"),
            "run_id": run.run["run_id"],
        }
        for zone_id in sorted(segments_by_zone)
        for segment in segments_by_zone[zone_id]
    ]
    segment_rows.sort(
        key=lambda item: (float(item["start_time"]), str(item["zone_id"]), int(item["track_id"]))
    )

    return {
        "schema": "e04-rule-run-v2",
        "engine_version": ENGINE_VERSION,
        "generated_at_utc": emitted_at_utc,
        "run": {
            "run_id": run.run["run_id"],
            "experiment": run.run.get("experiment"),
            "camera_id": run.manifest.get("camera_id"),
            "video_id": run.manifest.get("video_id"),
            "source_video": str(run.source_video),
            "analysis_start": round(run.analysis_start, 3),
            "analysis_end": round(run.analysis_end, 3),
            "fps": run.fps,
            "processed_frames": len(run.frames),
            "track_points": len(run.points),
            "track_count": len(by_track),
        },
        "definitions": {
            "count": COUNT_DEFINITION,
            "dwell": DWELL_DEFINITION,
            "zone_boundary": ZONE_BOUNDARY_METHOD,
            "distance_unit": "image_pixels",
            "calibration": "no_camera_calibration_no_metric_claims",
            "candidate_status_label": CANDIDATE_STATUS_LABEL,
            "frame_timestamp": "frame_start",
            "frame_coverage": "left_closed_right_open_[t,t+1/fps)",
            "evidence_frame_video_time": "frame_start",
            "condition_met_video_time": "trigger_frame_coverage_end",
            "emitted_at_utc": "batch_write_wall_clock_not_field_latency",
        },
        "inputs": {
            **(input_hashes or {}),
            "rule_config_sha256": config_hash,
            "rule_config": {
                "rule_set_version": config.get("rule_set_version"),
                "calibration_status": config.get("calibration_status"),
            },
        },
        "zones": [zone.as_dict() for zone in zones],
        "rules": rule_statuses,
        "zone_stats": zone_stats,
        "segments": segment_rows,
        "candidates": all_candidates,
        "candidate_count": len(all_candidates),
    }
