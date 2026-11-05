"""Pure-stdlib checks for the E01 data foundation.

The module deliberately does not import OpenCV, torch or project ML packages.
It validates provenance, split isolation, zone geometry and annotation schema.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_SPLITS = {"development", "validation", "test"}
ALLOWED_ANNOTATION_STATUS = {"synthetic_example", "model_prediction", "human_gt"}
ALLOWED_REVIEW_STATUS = {
    "not_reviewed_example",
    "machine_only",
    "pending_review",
    "single_reviewer",
    "double_reviewed",
    "adjudicated",
}
MANIFEST_REQUIRED = {
    "video_id",
    "path",
    "source_url",
    "source_kind",
    "source_group",
    "camera_id",
    "camera_label",
    "time_basis",
    "purpose",
    "split",
    "is_test_set",
    "test_role",
    "license_status",
    "sha256",
    "size_bytes",
    "fps",
    "frame_count",
    "width",
    "height",
    "duration_seconds",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, level: str, code: str, message: str, **context: Any) -> None:
        item = {"level": level, "code": code, "message": message}
        if context:
            item["context"] = context
        self.checks.append(item)

    def error(self, code: str, message: str, **context: Any) -> None:
        self.add("error", code, message, **context)

    def warn(self, code: str, message: str, **context: Any) -> None:
        self.add("warning", code, message, **context)

    def info(self, code: str, message: str, **context: Any) -> None:
        self.add("info", code, message, **context)

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [item for item in self.checks if item["level"] == "error"]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [item for item in self.checks if item["level"] == "warning"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: JSON 解析失败: {exc}") from exc
    return rows


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _point_in_frame(point: Any, width: int, height: int) -> bool:
    return (
        isinstance(point, (list, tuple))
        and len(point) == 2
        and all(_is_number(value) for value in point)
        and 0 <= point[0] <= width
        and 0 <= point[1] <= height
    )


def _polygon_area(points: list[list[float]]) -> float:
    area = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return abs(area) / 2.0


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def check_manifest(base_dir: Path, videos: list[dict], report: Report) -> None:
    seen_ids: dict[str, int] = {}
    seen_hashes: dict[str, str] = {}
    seen_paths: dict[str, str] = {}

    for index, video in enumerate(videos):
        label = video.get("video_id") or f"row-{index}"
        missing = sorted(MANIFEST_REQUIRED - set(video))
        if missing:
            report.error("manifest_missing_fields", f"{label} 缺少字段: {', '.join(missing)}")
            continue

        video_id = video["video_id"]
        if video_id in seen_ids:
            report.error("manifest_duplicate_id", f"重复 video_id: {video_id}")
        seen_ids[video_id] = index

        path = Path(video["path"])
        resolved = path if path.is_absolute() else base_dir / path
        if not resolved.is_file():
            report.error("manifest_file_missing", f"{video_id} 视频文件不存在", path=str(resolved))
        else:
            actual_hash = sha256_file(resolved)
            if video["sha256"] in seen_hashes:
                report.error(
                    "manifest_duplicate_sha256",
                    f"{video_id} 与 {seen_hashes[video['sha256']]} 使用相同文件哈希",
                    sha256=video["sha256"],
                )
            seen_hashes[video["sha256"]] = video_id
            if video["path"] in seen_paths:
                report.error(
                    "manifest_duplicate_path",
                    f"{video_id} 与 {seen_paths[video['path']]} 使用相同路径",
                    path=video["path"],
                )
            seen_paths[video["path"]] = video_id
            if actual_hash != video["sha256"]:
                report.error(
                    "manifest_hash_mismatch",
                    f"{video_id} SHA256 不匹配",
                    expected=video["sha256"],
                    actual=actual_hash,
                )
            if video["size_bytes"] != resolved.stat().st_size:
                report.warn(
                    "manifest_size_mismatch",
                    f"{video_id} size_bytes 与磁盘文件不一致",
                    expected=video["size_bytes"],
                    actual=resolved.stat().st_size,
                )

        if not SHA256_RE.match(str(video["sha256"])):
            report.error("manifest_bad_sha256_format", f"{video_id} SHA256 格式无效")
        if not _is_number(video["fps"]) or video["fps"] <= 0:
            report.error("manifest_bad_fps", f"{video_id} fps 无效")
        if not isinstance(video["frame_count"], int) or video["frame_count"] <= 0:
            report.error("manifest_bad_frame_count", f"{video_id} frame_count 无效")
        if not isinstance(video["width"], int) or video["width"] <= 0:
            report.error("manifest_bad_width", f"{video_id} width 无效")
        if not isinstance(video["height"], int) or video["height"] <= 0:
            report.error("manifest_bad_height", f"{video_id} height 无效")
        if not _is_number(video["duration_seconds"]) or video["duration_seconds"] <= 0:
            report.error("manifest_bad_duration", f"{video_id} duration 无效")
        elif _is_number(video["fps"]) and video["fps"] > 0 and video["frame_count"] > 0:
            derived = video["frame_count"] / video["fps"]
            if abs(derived - video["duration_seconds"]) > 0.05:
                report.warn(
                    "manifest_duration_inconsistent",
                    f"{video_id} duration 与 frame_count/fps 不一致",
                    declared=video["duration_seconds"],
                    derived=round(derived, 3),
                )

        if video["split"] not in ALLOWED_SPLITS:
            report.error("manifest_bad_split", f"{video_id} split 无效: {video['split']}")
        if not isinstance(video["is_test_set"], bool):
            report.error("manifest_bad_test_flag", f"{video_id} is_test_set 必须是 bool")
        elif video["is_test_set"] != (video["split"] == "test"):
            report.error(
                "manifest_test_flag_mismatch",
                f"{video_id} 的 is_test_set 与 split 不一致",
                split=video["split"],
                is_test_set=video["is_test_set"],
            )
        if video["split"] != "test" and video.get("test_role") != "not_test":
            report.error("manifest_test_role_mismatch", f"{video_id} 开发视频的 test_role 必须是 not_test")
        if not video["time_basis"]:
            report.error("manifest_missing_time_basis", f"{video_id} 缺 time_basis")
        if not video["source_group"]:
            report.error("manifest_missing_source_group", f"{video_id} 缺 source_group")
        if not video["camera_id"]:
            report.error("manifest_missing_camera", f"{video_id} 缺 camera_id")
        if video.get("absolute_capture_time") is not None:
            report.warn(
                "manifest_absolute_time_unverified",
                f"{video_id} 填了 absolute_capture_time，但来源记录未证明该时间",
                value=video["absolute_capture_time"],
            )
        for window in video.get("used_windows", []):
            if not all(key in window for key in ("start", "end")):
                report.error("manifest_bad_window", f"{video_id} used_window 缺 start/end")
                continue
            start, end = window["start"], window["end"]
            if not _is_number(start) or not _is_number(end) or start < 0 or end <= start:
                report.error("manifest_bad_window", f"{video_id} used_window 无效", window=window)
            elif _is_number(video["duration_seconds"]) and end > video["duration_seconds"] + 0.05:
                report.error(
                    "manifest_window_out_of_range",
                    f"{video_id} used_window 超出视频时长",
                    window=window,
                    duration=video["duration_seconds"],
                )

    report.info("manifest_loaded", f"读取 {len(videos)} 个视频清单", count=len(videos))


def check_splits(videos: list[dict], splits: dict, report: Report) -> None:
    videos_by_id = {video["video_id"]: video for video in videos}
    sets = splits.get("sets", {})
    for name in ALLOWED_SPLITS:
        if name not in sets:
            report.error("split_set_missing", f"缺少集合: {name}")

    assigned: dict[str, str] = {}
    group_sets: dict[str, str] = {}
    for set_name in ALLOWED_SPLITS:
        set_data = sets.get(set_name, {})
        for video_id in set_data.get("video_ids", []):
            if video_id not in videos_by_id:
                report.error("split_unknown_video", f"{set_name} 引用未知视频: {video_id}")
                continue
            if video_id in assigned and assigned[video_id] != set_name:
                report.error(
                    "split_video_in_multiple_sets",
                    f"视频 {video_id} 属于多个集合",
                    sets=[assigned[video_id], set_name],
                )
            if assigned.get(video_id) == set_name:
                report.error("split_duplicate_video", f"{set_name} 重复列出视频: {video_id}")
            assigned[video_id] = set_name
            video = videos_by_id[video_id]
            if video["split"] != set_name:
                report.error(
                    "split_manifest_mismatch",
                    f"视频 {video_id} 的 manifest split 与集合不一致",
                    manifest=video["split"],
                    set_name=set_name,
                )
            group = video["source_group"]
            if group in group_sets and group_sets[group] != set_name:
                report.error(
                    "split_source_group_cross_set",
                    f"同源组 {group} 跨集合",
                    sets=[group_sets[group], set_name],
                )
            group_sets[group] = set_name

    for video in videos:
        if video["video_id"] not in assigned:
            report.error("split_video_unassigned", f"视频未分配到集合: {video['video_id']}")

    required = splits.get("required_development_ids", [])
    if not required:
        report.warn("split_required_missing", "splits.json 未声明 required_development_ids")
    for video_id in required:
        if assigned.get(video_id) != "development":
            report.error(
                "split_required_not_development",
                f"已使用的公开样例必须归开发集: {video_id}",
                assigned=assigned.get(video_id),
            )

    validation = sets.get("validation", {})
    test = sets.get("test", {})
    if validation.get("status") == "missing_data" and not validation.get("video_ids"):
        report.warn("split_validation_empty", "验证集为空：缺少独立现场视频，不能拿开发集代替")
    if test.get("status") == "not_established" and not test.get("video_ids"):
        report.warn("split_test_not_established", "测试集未建立：当前没有未见数据，E01 不能宣告完成")

    segments = splits.get("segments", [])
    seen_segment_ids: set[str] = set()
    segment_by_id: dict[str, dict] = {}
    for segment in segments:
        required_fields = {"segment_id", "video_id", "source_group", "start", "end", "split", "purpose"}
        missing = required_fields - set(segment)
        if missing:
            report.error("split_segment_missing_fields", f"segment 缺字段: {', '.join(sorted(missing))}")
            continue
        segment_id = segment["segment_id"]
        if segment_id in seen_segment_ids:
            report.error("split_duplicate_segment", f"重复 segment_id: {segment_id}")
        seen_segment_ids.add(segment_id)
        video = videos_by_id.get(segment["video_id"])
        if not video:
            report.error("split_segment_unknown_video", f"segment {segment_id} 引用未知视频")
            continue
        segment_by_id[segment_id] = segment
        if segment["split"] not in ALLOWED_SPLITS:
            report.error("split_segment_bad_split", f"segment {segment_id} split 无效")
        if segment["split"] != video["split"]:
            report.error(
                "split_segment_manifest_mismatch",
                f"segment {segment_id} 与视频 split 不一致",
                segment=segment["split"],
                video=video["split"],
            )
        if segment["source_group"] != video["source_group"]:
            report.error("split_segment_group_mismatch", f"segment {segment_id} source_group 不匹配")
        if not _is_number(segment["start"]) or not _is_number(segment["end"]):
            report.error("split_segment_bad_time", f"segment {segment_id} 时间不是数字")
        elif segment["start"] < 0 or segment["end"] <= segment["start"]:
            report.error("split_segment_bad_time", f"segment {segment_id} 起止时间无效")
        elif segment["end"] > video["duration_seconds"] + 0.05:
            report.error("split_segment_out_of_range", f"segment {segment_id} 超出视频时长")
        if segment["split"] in sets and segment["video_id"] not in sets[segment["split"]].get("video_ids", []):
            report.error("split_segment_not_in_set", f"segment {segment_id} 的视频不在对应集合")

    # Cross-set overlap / duplicate checks.
    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1 :]:
            if left.get("video_id") != right.get("video_id"):
                continue
            if not all(_is_number(left.get(key)) for key in ("start", "end")):
                continue
            if not all(_is_number(right.get(key)) for key in ("start", "end")):
                continue
            if left.get("split") == right.get("split"):
                if left["start"] == right["start"] and left["end"] == right["end"]:
                    report.warn(
                        "split_duplicate_window_same_set",
                        "同集合存在完全重复的时间窗口，需确认是否重复计数",
                        left=left.get("segment_id"),
                        right=right.get("segment_id"),
                    )
                elif _overlaps(left["start"], left["end"], right["start"], right["end"]):
                    report.info(
                        "split_overlap_same_set",
                        "同集合存在重叠窗口，允许但统计时不能重复计数",
                        left=left.get("segment_id"),
                        right=right.get("segment_id"),
                    )
            elif _overlaps(left["start"], left["end"], right["start"], right["end"]):
                report.error(
                    "split_overlap_cross_set",
                    "重叠时间窗口跨集合",
                    left=left.get("segment_id"),
                    left_split=left.get("split"),
                    right=right.get("segment_id"),
                    right_split=right.get("split"),
                )

    report.info(
        "splits_loaded",
        "划分检查完成",
        development=len(sets.get("development", {}).get("video_ids", [])),
        validation=len(sets.get("validation", {}).get("video_ids", [])),
        test=len(sets.get("test", {}).get("video_ids", [])),
        segments=len(segments),
    )


def check_zones(videos: list[dict], zones: dict, report: Report) -> None:
    videos_by_id = {video["video_id"]: video for video in videos}
    coordinate_system = zones.get("coordinate_system")
    if not coordinate_system:
        report.error("zones_missing_coordinate_system", "区域配置缺 coordinate_system")
    cameras = zones.get("cameras", [])
    seen_cameras: set[str] = set()
    seen_zones: set[str] = set()
    camera_video: dict[str, str] = {}

    for camera in cameras:
        camera_id = camera.get("camera_id")
        if not camera_id:
            report.error("zones_missing_camera_id", "相机配置缺 camera_id")
            continue
        if camera_id in seen_cameras:
            report.error("zones_duplicate_camera", f"重复 camera_id: {camera_id}")
        seen_cameras.add(camera_id)
        video_id = camera.get("video_id")
        video = videos_by_id.get(video_id)
        if not video:
            report.error("zones_unknown_video", f"相机 {camera_id} 引用未知视频: {video_id}")
            continue
        camera_video[camera_id] = video_id
        if video["camera_id"] != camera_id:
            report.error("zones_camera_mismatch", f"视频 {video_id} 的 camera_id 与区域配置不一致")
        frame_size = camera.get("frame_size")
        if (
            not isinstance(frame_size, (list, tuple))
            or len(frame_size) != 2
            or frame_size[0] != video["width"]
            or frame_size[1] != video["height"]
        ):
            report.error(
                "zones_frame_size_mismatch",
                f"相机 {camera_id} 的 frame_size 与视频不一致",
                frame_size=frame_size,
                video_size=[video["width"], video["height"]],
            )
            width, height = video["width"], video["height"]
        else:
            width, height = frame_size
        if camera.get("is_field_business_zone") is not False:
            report.error("zones_public_claims_business", f"公开视频 {video_id} 不能声明为现场业务区域")
        if not camera.get("version"):
            report.error("zones_missing_version", f"相机 {camera_id} 缺 version")
        if not camera.get("purpose"):
            report.error("zones_missing_purpose", f"相机 {camera_id} 缺 purpose")
        if not camera.get("camera_motion"):
            report.error("zones_missing_motion", f"相机 {camera_id} 缺 camera_motion")

        for zone in camera.get("zones", []):
            zone_id = zone.get("zone_id")
            if not zone_id:
                report.error("zones_missing_zone_id", f"相机 {camera_id} 有区域缺 zone_id")
                continue
            if zone_id in seen_zones:
                report.error("zones_duplicate_zone", f"重复 zone_id: {zone_id}")
            seen_zones.add(zone_id)
            polygon = zone.get("polygon")
            if not isinstance(polygon, list) or len(polygon) < 3:
                report.error("zones_bad_polygon", f"区域 {zone_id} 少于 3 个点")
                continue
            if not all(_point_in_frame(point, width, height) for point in polygon):
                report.error("zones_polygon_out_of_frame", f"区域 {zone_id} 有点超出画面")
            if _polygon_area(polygon) <= 0:
                report.error("zones_zero_area", f"区域 {zone_id} 面积无效")
            if zone.get("business_zone") is not False:
                report.error("zones_public_zone_claims_business", f"公开样例区域 {zone_id} 不能标为业务区域")
            if not zone.get("coordinate_system"):
                report.error("zones_zone_missing_coordinate_system", f"区域 {zone_id} 缺 coordinate_system")
            if not zone.get("purpose"):
                report.error("zones_zone_missing_purpose", f"区域 {zone_id} 缺 purpose")
            if not zone.get("version"):
                report.error("zones_zone_missing_version", f"区域 {zone_id} 缺 version")

    for video in videos:
        if video["camera_id"] not in camera_video:
            report.error("zones_video_without_camera", f"视频 {video['video_id']} 没有相机区域配置")
            continue
        camera = next(item for item in cameras if item.get("camera_id") == video["camera_id"])
        if not camera.get("zones"):
            report.warn(
                "zones_empty_for_public_video",
                f"视频 {video['video_id']} 没有区域；仅用于可运行性检查，不能做区域规则",
            )

    report.info("zones_loaded", "区域配置检查完成", cameras=len(cameras), zones=len(seen_zones))


def _zone_map(zones: dict) -> dict[str, dict]:
    result = {}
    for camera in zones.get("cameras", []):
        for zone in camera.get("zones", []):
            if zone.get("zone_id"):
                result[zone["zone_id"]] = {"camera_id": camera.get("camera_id"), "zone": zone}
    return result


def _check_common_annotation(
    row: dict,
    videos_by_id: dict[str, dict],
    segments_by_id: dict[str, dict],
    zone_map: dict[str, dict],
    report: Report,
    file_label: str,
) -> tuple[dict | None, dict | None]:
    common = {
        "schema_version",
        "annotation_id",
        "video_id",
        "segment_id",
        "camera_id",
        "source_group",
        "split",
        "annotation_status",
        "review_status",
        "is_ground_truth",
    }
    missing = common - set(row)
    if missing:
        report.error(
            "annotation_missing_fields",
            f"{file_label}: 标注缺字段: {', '.join(sorted(missing))}",
        )
        return None, None
    annotation_id = row["annotation_id"]
    video = videos_by_id.get(row["video_id"])
    segment = segments_by_id.get(row["segment_id"])
    if not video:
        report.error("annotation_unknown_video", f"{annotation_id} 引用未知视频")
        return None, None
    if not segment:
        report.error("annotation_unknown_segment", f"{annotation_id} 引用未知片段")
        return None, None
    if row["segment_id"] and segment["video_id"] != row["video_id"]:
        report.error("annotation_segment_video_mismatch", f"{annotation_id} 的 segment 与 video 不一致")
    if row["camera_id"] != video["camera_id"]:
        report.error("annotation_camera_mismatch", f"{annotation_id} 的 camera_id 与视频不一致")
    if row["source_group"] != video["source_group"]:
        report.error("annotation_group_mismatch", f"{annotation_id} 的 source_group 与视频不一致")
    if row["split"] != video["split"]:
        report.error("annotation_split_mismatch", f"{annotation_id} 的 split 与视频不一致")
    if row["annotation_status"] not in ALLOWED_ANNOTATION_STATUS:
        report.error("annotation_bad_status", f"{annotation_id} annotation_status 无效")
    if row["review_status"] not in ALLOWED_REVIEW_STATUS:
        report.error("annotation_bad_review_status", f"{annotation_id} review_status 无效")
    if not isinstance(row["is_ground_truth"], bool):
        report.error("annotation_bad_gt_flag", f"{annotation_id} is_ground_truth 必须是 bool")
    zone_id = row.get("zone_id")
    if zone_id:
        if zone_id not in zone_map:
            report.error("annotation_unknown_zone", f"{annotation_id} 引用未知区域 {zone_id}")
        elif zone_map[zone_id]["camera_id"] != row["camera_id"]:
            report.error("annotation_zone_camera_mismatch", f"{annotation_id} 的区域属于其他相机")
    if row["annotation_status"] == "model_prediction" and row["is_ground_truth"]:
        report.error(
            "annotation_model_as_gt",
            f"{annotation_id} 把模型预测标成人工 GT",
        )
    if row["annotation_status"] == "synthetic_example":
        if row["is_ground_truth"]:
            report.error("annotation_synthetic_as_gt", f"{annotation_id} 把示例标成 GT")
        if row["review_status"] != "not_reviewed_example":
            report.warn("annotation_example_review_status", f"{annotation_id} 示例的 review_status 不是 not_reviewed_example")
    if row["is_ground_truth"]:
        if row["annotation_status"] != "human_gt":
            report.error("annotation_gt_status", f"{annotation_id} GT 必须 annotation_status=human_gt")
        if row["review_status"] not in {"double_reviewed", "adjudicated"}:
            report.error("annotation_gt_not_double_reviewed", f"{annotation_id} GT 未完成双人复核/裁定")
        reviewers = row.get("reviewer_ids")
        if not isinstance(reviewers, list) or not reviewers:
            report.error("annotation_gt_missing_reviewers", f"{annotation_id} GT 缺 reviewer_ids")
        if not row.get("review_notes"):
            report.error("annotation_gt_missing_notes", f"{annotation_id} GT 缺 review_notes")
    return video, segment


def _check_time_in_segment(
    row: dict, segment: dict, report: Report, label: str
) -> None:
    value = row.get("video_time")
    if value is not None:
        if not _is_number(value) or value < 0:
            report.error("annotation_bad_video_time", f"{label} video_time 无效")
        elif not (segment["start"] - 0.02 <= value <= segment["end"] + 0.02):
            report.error("annotation_time_outside_segment", f"{label} video_time 不在 segment 范围")


def check_annotations(
    videos: list[dict],
    segments: list[dict],
    zones: dict,
    annotations: list[dict],
    report: Report,
) -> None:
    videos_by_id = {video["video_id"]: video for video in videos}
    segments_by_id = {segment["segment_id"]: segment for segment in segments}
    zone_map = _zone_map(zones)
    seen_ids: set[str] = set()
    by_schema: dict[str, int] = {}
    by_status: dict[str, int] = {}

    for item in annotations:
        file_label = item.get("__file", "annotations")
        row = item["row"]
        annotation_id = row.get("annotation_id", "<missing>")
        if annotation_id in seen_ids:
            report.error("annotation_duplicate_id", f"重复 annotation_id: {annotation_id}")
        seen_ids.add(annotation_id)
        schema = row.get("schema_version")
        by_schema[schema] = by_schema.get(schema, 0) + 1
        status = row.get("annotation_status", "<missing>")
        by_status[status] = by_status.get(status, 0) + 1

        video, segment = _check_common_annotation(
            row, videos_by_id, segments_by_id, zone_map, report, f"{file_label}:{annotation_id}"
        )
        if video is None or segment is None:
            continue
        frame_index = row.get("frame_index")
        if frame_index is not None:
            if not isinstance(frame_index, int) or isinstance(frame_index, bool):
                report.error("annotation_bad_frame", f"{annotation_id} frame_index 必须是整数")
            elif not 0 <= frame_index < video["frame_count"]:
                report.error("annotation_frame_out_of_range", f"{annotation_id} frame_index 超出视频")

        if schema == "e01-track-v1":
            required = {"gt_track_id", "frame_index", "video_time", "bbox_xyxy", "occlusion", "visibility"}
            missing = required - set(row)
            if missing:
                report.error("track_missing_fields", f"{annotation_id} 缺字段: {', '.join(sorted(missing))}")
            else:
                if not row["gt_track_id"]:
                    report.error("track_missing_gt_id", f"{annotation_id} 缺 gt_track_id")
                _check_time_in_segment(row, segment, report, annotation_id)
                bbox = row["bbox_xyxy"]
                if not (
                    isinstance(bbox, list)
                    and len(bbox) == 4
                    and all(_is_number(value) for value in bbox)
                    and 0 <= bbox[0] < bbox[2] <= video["width"]
                    and 0 <= bbox[1] < bbox[3] <= video["height"]
                ):
                    report.error("track_bad_bbox", f"{annotation_id} bbox_xyxy 无效")
                if row["occlusion"] not in {"none", "partial", "heavy", "unknown"}:
                    report.error("track_bad_occlusion", f"{annotation_id} occlusion 值无效")
                if row["visibility"] not in {"visible", "partial", "out_of_frame", "unknown"}:
                    report.error("track_bad_visibility", f"{annotation_id} visibility 值无效")
        elif schema == "e01-event-v1":
            required = {"event_type", "event_status", "start_time", "end_time", "reality_status", "is_anomaly"}
            missing = required - set(row)
            if missing:
                report.error("event_missing_fields", f"{annotation_id} 缺字段: {', '.join(sorted(missing))}")
            else:
                _check_event_or_negative_times(row, segment, video, report, annotation_id)
                if row["event_status"] not in {"normal", "candidate", "confirmed_event", "unknown"}:
                    report.error("event_bad_status", f"{annotation_id} event_status 无效")
                if row["reality_status"] not in {"real", "drill", "public_demo"}:
                    report.error("event_bad_reality", f"{annotation_id} reality_status 无效")
                if not isinstance(row["is_anomaly"], bool):
                    report.error("event_bad_anomaly", f"{annotation_id} is_anomaly 必须是 bool")
                for frame in row.get("evidence_frame_indices", []):
                    if not isinstance(frame, int) or not 0 <= frame < video["frame_count"]:
                        report.error("event_bad_evidence_frame", f"{annotation_id} evidence frame 越界")
                if not isinstance(row.get("gt_track_ids", []), list):
                    report.error("event_bad_track_ids", f"{annotation_id} gt_track_ids 必须是列表")
        elif schema == "e01-hard-negative-v1":
            required = {"behavior_type", "start_time", "end_time", "normal_reason"}
            missing = required - set(row)
            if missing:
                report.error("hard_negative_missing_fields", f"{annotation_id} 缺字段: {', '.join(sorted(missing))}")
            else:
                _check_event_or_negative_times(row, segment, video, report, annotation_id)
                if not row["behavior_type"]:
                    report.error("hard_negative_bad_type", f"{annotation_id} behavior_type 为空")
                if not row["normal_reason"]:
                    report.error("hard_negative_missing_reason", f"{annotation_id} 缺 normal_reason")
        elif schema == "e01-quality-v1":
            required = {"frame_index", "video_time", "quality_labels", "severity", "judgeability"}
            missing = required - set(row)
            if missing:
                report.error("quality_missing_fields", f"{annotation_id} 缺字段: {', '.join(sorted(missing))}")
            else:
                _check_time_in_segment(row, segment, report, annotation_id)
                labels = row["quality_labels"]
                if not isinstance(labels, list) or not labels:
                    report.error("quality_bad_labels", f"{annotation_id} quality_labels 必须是非空列表")
                if row["severity"] not in {"mild", "moderate", "severe", "unknown"}:
                    report.error("quality_bad_severity", f"{annotation_id} severity 无效")
                if row["judgeability"] not in {"judgeable", "uncertain", "not_judgeable"}:
                    report.error("quality_bad_judgeability", f"{annotation_id} judgeability 无效")
        else:
            report.error("annotation_unknown_schema", f"{annotation_id} 未知 schema: {schema}")

    if not annotations:
        report.warn("annotations_empty", "没有标注文件；E02 定量评价不可开始")
    if by_status.get("human_gt", 0) == 0:
        report.warn(
            "annotations_no_human_gt",
            "没有人工 GT；当前只有示例/模型输出，不能计算 IDF1 或事件指标",
        )
    if by_status.get("model_prediction", 0) > 0:
        report.info(
            "annotations_model_predictions",
            "存在模型预测标注；已阻止其充当人工 GT",
            count=by_status["model_prediction"],
        )
    report.info(
        "annotations_loaded",
        "标注检查完成",
        total=len(annotations),
        schemas=by_schema,
        statuses=by_status,
    )


def _check_event_or_negative_times(row: dict, segment: dict, video: dict, report: Report, label: str) -> None:
    start, end = row.get("start_time"), row.get("end_time")
    if not _is_number(start) or not _is_number(end) or start < 0 or end <= start:
        report.error("annotation_bad_interval", f"{label} 起止时间无效")
        return
    if end > video["duration_seconds"] + 0.05:
        report.error("annotation_interval_out_of_video", f"{label} 起止时间超出视频")
    if start < segment["start"] - 0.02 or end > segment["end"] + 0.02:
        report.error("annotation_interval_outside_segment", f"{label} 起止时间不在 segment 范围")


def run_checks(project_dir: Path) -> dict:
    project_dir = Path(project_dir).resolve()
    e01 = project_dir / "data" / "e01"
    videos_path = e01 / "videos.jsonl"
    splits_path = e01 / "splits.json"
    zones_path = e01 / "zones" / "zones.json"
    annotations_dir = e01 / "annotations"

    report = Report()
    videos: list[dict] = []
    splits: dict = {}
    zones: dict = {}
    annotations: list[dict] = []

    try:
        videos = load_jsonl(videos_path)
    except Exception as exc:
        report.error("load_videos_failed", f"读取视频清单失败: {exc}", path=str(videos_path))
    try:
        splits = load_json(splits_path)
    except Exception as exc:
        report.error("load_splits_failed", f"读取划分失败: {exc}", path=str(splits_path))
    try:
        zones = load_json(zones_path)
    except Exception as exc:
        report.error("load_zones_failed", f"读取区域失败: {exc}", path=str(zones_path))

    if annotations_dir.is_dir():
        for path in sorted(annotations_dir.rglob("*.jsonl")):
            try:
                for row in load_jsonl(path):
                    annotations.append(
                        {"__file": str(path.relative_to(project_dir)), "row": row}
                    )
            except Exception as exc:
                report.error(
                    "load_annotations_failed",
                    f"读取标注失败: {exc}",
                    path=str(path.relative_to(project_dir)),
                )

    if videos:
        check_manifest(project_dir, videos, report)
    if videos and splits:
        check_splits(videos, splits, report)
    if videos and zones:
        check_zones(videos, zones, report)
    if videos and splits and zones:
        check_annotations(
            videos,
            splits.get("segments", []),
            zones,
            annotations,
            report,
        )

    by_status: dict[str, int] = {}
    by_schema: dict[str, int] = {}
    for item in annotations:
        row = item["row"]
        by_status[row.get("annotation_status", "<missing>")] = by_status.get(row.get("annotation_status", "<missing>"), 0) + 1
        by_schema[row.get("schema_version", "<missing>")] = by_schema.get(row.get("schema_version", "<missing>"), 0) + 1

    errors = report.errors
    warnings = report.warnings
    status = "fail" if errors else ("pass_with_warnings" if warnings else "pass")
    result = {
        "schema_version": "e01-check-result-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_dir": str(project_dir),
        "status": status,
        "summary": {
            "checks": len(report.checks),
            "errors": len(errors),
            "warnings": len(warnings),
            "infos": len(report.checks) - len(errors) - len(warnings),
        },
        "counts": {
            "videos": len(videos),
            "development_videos": sum(1 for video in videos if video.get("split") == "development"),
            "validation_videos": sum(1 for video in videos if video.get("split") == "validation"),
            "test_videos": sum(1 for video in videos if video.get("split") == "test"),
            "segments": len(splits.get("segments", [])) if splits else 0,
            "zones": sum(len(camera.get("zones", [])) for camera in zones.get("cameras", [])) if zones else 0,
            "annotations": len(annotations),
            "annotations_by_status": by_status,
            "annotations_by_schema": by_schema,
        },
        "checks": report.checks,
    }
    return result


def print_result(result: dict) -> None:
    print(f"E01 检查状态: {result['status']}")
    print(
        "  视频 {videos}（开发 {development_videos} / 验证 {validation_videos} / 测试 {test_videos}），"
        "片段 {segments}，区域 {zones}，标注 {annotations}".format(**result["counts"])
    )
    print(
        "  检查 {checks} 项：错误 {errors}，警告 {warnings}，信息 {infos}".format(
            **result["summary"]
        )
    )
    for item in result["checks"]:
        if item["level"] in {"error", "warning"}:
            print(f"  [{item['level']}] {item['code']}: {item['message']}")
