#!/usr/bin/env python3
"""Build E04 rule outputs and the browser demo dataset from existing results.

The script never re-runs detection or tracking. It reads the adopted E00-A run,
the E01 public demo zones and the E03 point-zone assignments.

Typical use (Supervision cross-check needs the GPU environment because the
pinned camera/analysis environment already contains NumPy and Supervision):

    .venv-gpu/bin/python scripts/e04_build.py --crosscheck-supervision

For a pure standard-library build:

    python3 scripts/e04_build.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.e04_rules import (  # noqa: E402
    E04Error,
    attach_zone_assignments,
    evaluate_rules,
    load_json,
    load_run,
    load_zones,
    now_utc,
    sha256_file,
)


def write_json(path: Path, value: dict, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        if compact:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def distance_to_polygon_edge(x: float, y: float, polygon: list[list[float]]) -> float:
    best = math.inf
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        dx, dy = x2 - x1, y2 - y1
        denominator = dx * dx + dy * dy
        if denominator <= 0:
            projection = 0.0
        else:
            projection = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denominator))
        best = min(best, math.hypot(x - (x1 + projection * dx), y - (y1 + projection * dy)))
    return best


def supervision_crosscheck(run, zones, assignments) -> dict:
    """Compare E03 membership with Supervision PolygonZone on the same points.

    Supervision 0.25.1 derives its mask from the polygon bounding box and clips
    boxes to that box, which is wrong for points outside it.  We therefore
    replace the mask/resolution with the full frame to make the comparison
    fair.  Remaining differences are boundary semantics (raster fill versus
    E03 vector ray casting).
    """

    try:
        import numpy as np
        import supervision as sv
    except ImportError as exc:  # pragma: no cover - environment dependent
        return {
            "status": "unavailable",
            "reason": f"无法导入 NumPy/Supervision: {exc}",
        }

    width = int(run.manifest["width"])
    height = int(run.manifest["height"])
    zone_objects = {}
    for zone in zones:
        polygon = np.array(zone.polygon, dtype=np.int64)
        detector = sv.PolygonZone(polygon=polygon)
        # Supervision clips to resolution_wh, then indexes the mask.  Use
        # (w-1, h-1) so a bottom-row point at y=height-1 remains addressable.
        detector.frame_resolution_wh = (width - 1, height - 1)
        detector.mask = sv.polygon_to_mask(polygon, (width, height))
        zone_objects[zone.zone_id] = detector

    by_frame: dict[int, list] = {}
    for point in run.points:
        by_frame.setdefault(point.frame_index, []).append(point)

    checked = 0
    matches = 0
    mismatches = []
    for frame_index in sorted(by_frame):
        points = by_frame[frame_index]
        detections = sv.Detections(
            xyxy=np.array([point.bbox for point in points], dtype=float)
        )
        for zone_id, detector in zone_objects.items():
            flags = detector.trigger(detections)
            for point, flag in zip(points, flags):
                expected = zone_id in assignments.get(
                    (point.track_id, point.frame_index), ()
                )
                checked += 1
                if bool(flag) == expected:
                    matches += 1
                else:
                    polygon = next(
                        list(zone.polygon) for zone in zones if zone.zone_id == zone_id
                    )
                    mismatches.append(
                        {
                            "frame_index": frame_index,
                            "track_id": point.track_id,
                            "zone_id": zone_id,
                            "supervision_inside": bool(flag),
                            "e03_inside": expected,
                            "distance_to_polygon_edge_px": round(
                                distance_to_polygon_edge(point.x, point.y, polygon), 3
                            ),
                        }
                    )

    max_edge_distance = max(
        (item["distance_to_polygon_edge_px"] for item in mismatches), default=0.0
    )
    return {
        "status": "completed",
        "library": f"supervision {sv.__version__} PolygonZone",
        "checked_point_zone_pairs": checked,
        "matches": matches,
        "mismatches": len(mismatches),
        "max_mismatch_distance_to_boundary_px": round(max_edge_distance, 3),
        "boundary_only": bool(mismatches) and max_edge_distance <= 1.0,
        "note": (
            "E03 向量射线法（边界半开）是输出口径；Supervision 0.25.1 的栅格掩膜"
            "在边界处包含性不同。差异均位于多边形边界 1 px 内，因此不改变区域统计口径。"
            if mismatches and max_edge_distance <= 1.0
            else "存在非边界差异，需要人工检查。"
        ),
        "sample_mismatches": mismatches[:10],
    }


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path.resolve())


def compact_demo(
    run,
    zones,
    result: dict,
    config_path: Path,
    zones_path: Path,
    assignments_path: Path,
) -> dict:
    manifest = run.manifest
    source = run.run.get("source") or {}
    sample = {
        "id": manifest.get("video_id"),
        "display_name": "行人通行 · 公开样例 01",
        "camera_id": manifest.get("camera_id"),
        "fps": run.fps,
        "width": int(manifest.get("width", 0)),
        "height": int(manifest.get("height", 0)),
        "original_duration": float(manifest.get("duration_seconds", 0.0)),
        "analysis_start": run.analysis_start,
        "analysis_end": run.analysis_end,
        "tail_seconds": round(
            max(0.0, float(manifest.get("duration_seconds", 0.0)) - run.analysis_end), 2
        ),
        "processed_frames": len(run.frames),
        "track_point_count": len(run.points),
        "track_count": len(run.points_by_track),
        "track_count_label": "累计轨迹编号",
        "track_count_note": "局部轨迹 ID 累计数量，不是独立人数，也不代表身份确认。",
    }
    files = {
        "original_video": "/media/original.mp4",
        "analysis_video": "/media/analysis_replay.mp4",
        "preview_image": "/media/preview.jpg",
        "tracks_data": "/data/demo.json",
        "rules_data": "/data/rules.json",
    }
    expected_paths = {
        "original_video": "st_memory_experiments/data/raw/people-walking.mp4",
        "analysis_video": "st_memory_experiments/demo/media/analysis_replay.mp4",
        "preview_image": "st_memory_experiments/runs/e00-public-demo-005/preview.jpg",
        "tracks_data": "st_memory_experiments/demo/data/demo.json",
        "rules_data": "st_memory_experiments/demo/data/rules.json",
    }
    candidates = []
    for candidate in result["candidates"]:
        candidates.append(
            {
                **candidate,
                "display": {
                    "title": f"{candidate['zone_name']} · 目标 {candidate['track_id']:03d}"
                    if candidate.get("track_id") is not None
                    else candidate["zone_name"],
                    "time": f"{candidate['condition_met_video_time']:.2f} 秒",
                    "rule": candidate["rule_label"],
                    "status": candidate["status_label"],
                },
            }
        )

    return {
        "schema": "e04-rule-demo-v2",
        "generated_at_utc": now_utc(),
        "project": {
            "name": "时空记忆智能体",
            "subtitle": "公开样例 · 区域统计与规则证据",
            "experiment": "E04",
            "demo_version": "1.1.0",
        },
        "sample": sample,
        "files": files,
        "expected_paths": expected_paths,
        "run": {
            "run_id": run.run["run_id"],
            "experiment": run.run.get("experiment"),
            "status": run.run.get("status"),
            "result_dir": f"st_memory_experiments/runs/{run.run['run_id']}",
            "source_url": manifest.get("source_url"),
            "source_sha256": manifest.get("sha256"),
            "timestamp_basis": manifest.get("timestamp_basis"),
            "usage_scope": manifest.get("usage_scope"),
        },
        "zones": [
            {
                **zone.as_dict(),
                "counts": next(
                    item["counts"]
                    for item in result["zone_stats"]["zones"]
                    if item["zone_id"] == zone.zone_id
                ),
                "max_continuous_dwell_seconds": next(
                    item["max_continuous_dwell_seconds"]
                    for item in result["zone_stats"]["zones"]
                    if item["zone_id"] == zone.zone_id
                ),
                "peak_count": next(
                    item["peak_count"]
                    for item in result["zone_stats"]["zones"]
                    if item["zone_id"] == zone.zone_id
                ),
                "peak_time": next(
                    item["peak_time"]
                    for item in result["zone_stats"]["zones"]
                    if item["zone_id"] == zone.zone_id
                ),
            }
            for zone in zones
        ],
        "zone_stats": {
            "fps": result["zone_stats"]["fps"],
            "times": result["zone_stats"]["times"],
            "frame_indices": result["zone_stats"]["frame_indices"],
            "count_definition": result["zone_stats"]["count_definition"],
            "dwell_definition": result["zone_stats"]["dwell_definition"],
            "valid_target_definition": result["zone_stats"]["valid_target_definition"],
        },
        "rules": result["rules"],
        "candidates": candidates,
        "definitions": result["definitions"],
        "provenance": {
            "rule_set_version": result["inputs"]["rule_config"].get("rule_set_version"),
            "rule_config_sha256": result["inputs"]["rule_config_sha256"],
            "rules_path": relpath(config_path),
            "zone_config": relpath(zones_path),
            "zone_config_sha256": result["inputs"].get("zone_config"),
            "zone_assignments": relpath(assignments_path),
            "zone_assignments_sha256": result["inputs"].get("zone_assignments"),
            "tracks_jsonl_sha256": result["inputs"].get("tracks_jsonl"),
            "frames_jsonl_sha256": result["inputs"].get("frames_jsonl"),
            "source_video_sha256": result["inputs"].get("source_video"),
            "engine_version": result["engine_version"],
            "supervision_crosscheck": result.get("supervision_crosscheck"),
            "notes": [
                "只读取已有 E00-A 真实轨迹和 E03 区域派生结果，不重新推理。",
                "区域是公开演示区域，不是现场业务区域；距离单位为像素，没有相机标定。",
                "规则阈值未经现场校准；候选统一为“规则条件触发，待人工复核”。",
                "60 秒人数变化规则因观察时长不足未评价，没有缩短窗口。",
                "公开片段没有人工 GT，不计算检出率、误报率或现场准确率。",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 E04 规则输出与演示数据")
    parser.add_argument("--run", type=Path, default=Path("runs/e00-public-demo-005"))
    parser.add_argument(
        "--zones", type=Path, default=Path("data/e01/zones/zones.json")
    )
    parser.add_argument(
        "--zone-assignments",
        type=Path,
        default=Path("data/e03/zone_assignments.jsonl"),
    )
    parser.add_argument(
        "--config", type=Path, default=Path("configs/e04_demo_rules.json")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/e04"))
    parser.add_argument(
        "--demo-output", type=Path, default=Path("demo/data/rules.json")
    )
    parser.add_argument(
        "--crosscheck-supervision",
        action="store_true",
        help="使用 Supervision PolygonZone 对区域点归属做独立交叉核对",
    )
    parser.add_argument(
        "--disable-rule",
        action="append",
        default=[],
        choices=["long_stay", "reversal", "occupancy_change_60s"],
        help="独立关闭某条规则（可重复）；默认读取配置",
    )
    args = parser.parse_args()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_DIR / path

    run_dir = resolve(args.run)
    zones_path = resolve(args.zones)
    assignments_path = resolve(args.zone_assignments)
    config_path = resolve(args.config)
    output_dir = resolve(args.output_dir)
    demo_output = resolve(args.demo_output)

    try:
        run = load_run(run_dir)
        zones, camera = load_zones(zones_path, str(run.manifest["camera_id"]))
        assignments, assignment_details = attach_zone_assignments(
            run, assignments_path, zones
        )
        config = load_json(config_path)
        for rule in config.get("rules", []):
            if rule.get("rule_id") in set(args.disable_rule):
                rule["enabled"] = False
        input_hashes = {
            **run.hashes,
            "zone_config": sha256_file(zones_path),
            "zone_assignments": sha256_file(assignments_path),
            "rule_config": sha256_file(config_path),
            "e03_boundary_method": assignment_details["boundary_method"],
        }
        result = evaluate_rules(
            run, zones, assignments, config, input_hashes=input_hashes
        )
        result["zones_meta"] = {
            "zone_config_path": str(zones_path),
            "camera": {
                "camera_id": camera.get("camera_id"),
                "video_id": camera.get("video_id"),
                "version": camera.get("version"),
                "purpose": camera.get("purpose"),
                "is_field_business_zone": camera.get("is_field_business_zone"),
                "camera_motion": camera.get("camera_motion"),
            },
            "assignment_details": assignment_details,
        }
        if args.crosscheck_supervision:
            result["supervision_crosscheck"] = supervision_crosscheck(
                run, zones, assignments
            )
        else:
            result["supervision_crosscheck"] = {
                "status": "not_run",
                "reason": "未指定 --crosscheck-supervision",
            }

        write_json(output_dir / "rule_run.json", result)
        write_jsonl(output_dir / "candidates.jsonl", result["candidates"])
        write_jsonl(output_dir / "segments.jsonl", result["segments"])
        write_json(output_dir / "zone_stats.json", result["zone_stats"])
        write_json(
            output_dir / "zone_definitions.json",
            {
                "zones": [zone.as_dict() for zone in zones],
                "camera": result["zones_meta"]["camera"],
                "assignment_details": assignment_details,
            },
        )
        demo = compact_demo(
            run, zones, result, config_path, zones_path, assignments_path
        )
        write_json(demo_output, demo, compact=True)

        summary = {
            "generated_at_utc": result["generated_at_utc"],
            "run_id": run.run["run_id"],
            "engine_version": result["engine_version"],
            "candidate_count": result["candidate_count"],
            "rules": [
                {
                    "rule_id": item["rule_id"],
                    "evaluation_status": item["evaluation_status"],
                    "candidate_count": item["candidate_count"],
                    "status_text": item.get("status_text"),
                }
                for item in result["rules"]
            ],
            "zone_stats": {
                "points_with_zone": assignment_details["points_with_zone"],
                "per_zone": assignment_details["per_zone"],
            },
            "supervision_crosscheck": result["supervision_crosscheck"],
        }
        write_json(output_dir / "rule_run_summary.json", summary)
        default_output = (PROJECT_DIR / "data" / "e04").resolve()
        if output_dir.resolve() == default_output:
            write_json(PROJECT_DIR / "reports" / "E04规则运行摘要.json", summary)
    except E04Error as exc:
        print(f"E04 构建失败: {exc}", file=sys.stderr)
        return 2

    print(f"已生成 {output_dir / 'rule_run.json'}")
    print(f"已生成 {output_dir / 'candidates.jsonl'}（{result['candidate_count']} 条候选）")
    print(f"已生成 {demo_output}")
    print(
        "规则: "
        + " | ".join(
            f"{item['rule_id']}={item['candidate_count']} ({item['evaluation_status']})"
            for item in result["rules"]
        )
    )
    crosscheck = result.get("supervision_crosscheck") or {}
    print(
        "Supervision 交叉核对: "
        + (
            f"{crosscheck.get('status')}，差异 {crosscheck.get('mismatches')} / "
            f"{crosscheck.get('checked_point_zone_pairs')}，边界内="
            f"{crosscheck.get('boundary_only')}"
            if crosscheck.get("status") == "completed"
            else str(crosscheck.get("status"))
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
