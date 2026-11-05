#!/usr/bin/env python3
"""Build the read-only demo dataset for E00-D from a completed perception run.

The demo never re-runs detection or tracking. It only reads the existing run
artifacts and writes a compact JSON file consumed by the browser UI.

Usage:
    python3 demo/build_demo.py
    python3 demo/build_demo.py --run runs/e00-public-demo-005
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DEMO_DIR.parent
DEFAULT_RUN = PROJECT_DIR / "runs" / "e00-public-demo-005"
OUTPUT = DEMO_DIR / "data" / "demo.json"

REQUIRED_RUN_FILES = [
    "manifest.json",
    "run.json",
    "tracks.jsonl",
    "trace_summaries.jsonl",
    "frames.jsonl",
    "replay.mp4",
    "preview.jpg",
]


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: JSON 解析失败: {exc}") from exc
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_point(row: dict) -> list[float]:
    bbox = row["bbox"]
    return [
        round(float(row["video_time"]), 2),
        round(float(bbox[0]), 1),
        round(float(bbox[1]), 1),
        round(float(bbox[2]), 1),
        round(float(bbox[3]), 1),
    ]


def build(run_dir: Path) -> dict:
    missing = [name for name in REQUIRED_RUN_FILES if not (run_dir / name).is_file()]
    if missing:
        raise SystemExit(
            "运行目录缺少必要文件: " + ", ".join(str(run_dir / name) for name in missing)
        )

    manifest = load_json(run_dir / "manifest.json")
    run = load_json(run_dir / "run.json")
    summaries = load_jsonl(run_dir / "trace_summaries.jsonl")
    track_rows = load_jsonl(run_dir / "tracks.jsonl")
    frame_rows = load_jsonl(run_dir / "frames.jsonl")

    if run.get("status") != "completed":
        raise SystemExit(f"运行状态不是 completed: {run.get('status')!r}")

    fps = float(manifest["fps"])
    analysis_end = float(run["processed_video_seconds"])
    frame_count = int(run["processed_frames"])
    expected_points = int(run["track_point_count"])
    expected_tracks = int(run["unique_track_count"])

    if len(track_rows) != expected_points:
        raise SystemExit(f"轨迹点数量不一致: {len(track_rows)} != {expected_points}")
    if len(summaries) != expected_tracks:
        raise SystemExit(f"轨迹摘要数量不一致: {len(summaries)} != {expected_tracks}")
    if len(frame_rows) != frame_count:
        raise SystemExit(f"帧记录数量不一致: {len(frame_rows)} != {frame_count}")

    points_by_track: dict[int, list[dict]] = defaultdict(list)
    for row in track_rows:
        points_by_track[int(row["track_id"])].append(row)

    track_ids = sorted(points_by_track)
    if len(track_ids) != expected_tracks:
        raise SystemExit(f"局部轨迹 ID 数量不一致: {len(track_ids)} != {expected_tracks}")

    tracks = []
    for summary in summaries:
        track_id = int(summary["track_id"])
        raw_points = sorted(points_by_track.get(track_id, []), key=lambda row: row["video_time"])
        if not raw_points:
            raise SystemExit(f"轨迹 {track_id} 没有轨迹点")
        points = [compact_point(row) for row in raw_points]
        first = points[0][0]
        last = points[-1][0]
        if int(summary["observed_point_count"]) != len(points):
            raise SystemExit(
                f"轨迹 {track_id} 点数不一致: {len(points)} != {summary['observed_point_count']}"
            )
        tracks.append(
            {
                "id": track_id,
                "start": first,
                "last": last,
                "duration": round(last - first, 2),
                "point_count": len(points),
                "start_frame": int(round(first * fps)),
                "last_frame": int(round(last * fps)),
                # [video_time, bbox_x1, bbox_y1, bbox_x2, bbox_y2]
                "points": points,
            }
        )

    tracks.sort(key=lambda item: item["id"])

    tracked_counts = [int(row["tracked_count"]) for row in frame_rows]
    detected_counts = [int(row["detected_count"]) for row in frame_rows]
    avg_tracked = round(sum(tracked_counts) / len(tracked_counts), 1)
    max_tracked = max(tracked_counts)
    avg_detected = round(sum(detected_counts) / len(detected_counts), 1)

    # Recommended examples, selected only from observed properties (not identity).
    longest = max(tracks, key=lambda item: (item["duration"], -item["id"]))
    late_long = max(
        (item for item in tracks if item["point_count"] >= 100),
        key=lambda item: (item["start"], item["point_count"]),
    )
    # Prefer a visible, early track with a non-trivial path for the first view.
    default_track = next(
        (item for item in tracks if item["id"] == 7),
        next(item for item in tracks if item["point_count"] >= 100),
    )

    duration_original = float(manifest["duration_seconds"])
    tail_seconds = round(max(0.0, duration_original - analysis_end), 2)
    artifact_hashes = run.get("artifact_hashes", {})

    provenance_files = {}
    for name in ("tracks.jsonl", "trace_summaries.jsonl", "frames.jsonl", "replay.mp4", "preview.jpg"):
        path = run_dir / name
        provenance_files[name] = {
            "sha256": artifact_hashes.get(name) or sha256(path),
            "bytes": path.stat().st_size,
        }

    data = {
        "schema": "e00-d-demo-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "name": "时空记忆智能体",
            "subtitle": "公开样例 · 视频轨迹、区域统计与规则证据",
            "experiment": "E00-D / E04",
            "demo_version": "1.1.0",
        },
        "sample": {
            "id": manifest["video_id"],
            "display_name": "行人通行 · 公开样例 01",
            "source_url": manifest["source_url"],
            "source_kind": manifest["source_kind"],
            "split": manifest["split"],
            "usage_scope": manifest["usage_scope"],
            "camera_id": manifest["camera_id"],
            "fps": fps,
            "width": int(manifest["width"]),
            "height": int(manifest["height"]),
            "original_duration": duration_original,
            "original_frame_count": int(manifest["frame_count"]),
            "analysis_start": 0.0,
            "analysis_end": analysis_end,
            "tail_seconds": tail_seconds,
            "processed_frames": frame_count,
            "track_point_count": expected_points,
            "track_count": expected_tracks,
            "average_tracked_boxes": avg_tracked,
            "max_tracked_boxes": max_tracked,
            "average_detected_boxes": avg_detected,
            "track_count_label": "累计轨迹编号",
            "track_count_note": "局部轨迹 ID 累计数量，不是独立人数，也不代表身份确认。",
        },
        "files": {
            "original_video": "/media/original.mp4",
            "analysis_video": "/media/analysis_replay.mp4",
            "preview_image": "/media/preview.jpg",
            "data": "/data/demo.json",
        },
        "expected_paths": {
            "original_video": "st_memory_experiments/data/raw/people-walking.mp4",
            "analysis_video": "st_memory_experiments/demo/media/analysis_replay.mp4",
            "preview_image": "st_memory_experiments/runs/e00-public-demo-005/preview.jpg",
            "data": "st_memory_experiments/demo/data/demo.json",
        },
        "run": {
            "run_id": run["run_id"],
            "experiment": run.get("experiment"),
            "status": run.get("status"),
            "started_at_utc": run.get("started_at_utc"),
            "finished_at_utc": run.get("finished_at_utc"),
            "result_dir": f"st_memory_experiments/runs/{run['run_id']}",
            "wall_seconds": run.get("wall_seconds"),
            "processing_fps": run.get("processing_fps"),
            "replay_frames_verified": run.get("replay_frames_verified"),
        },
        "components": {
            "detector": "YOLOv8n（Ultralytics 8.3.40）",
            "tracker": "Supervision 0.25.1 · ByteTrack",
            "renderer": "Supervision Box / Label / TraceAnnotator",
            "video_understanding": None,
            "event_rules": "E04 规则基线（公开样例功能验证；区域规则演示页）",
        },
        "capabilities": {
            "implemented": [
                "行人检测与单镜跟踪",
                "带编号的分析回放",
                "轨迹路径与出现时段",
                "按视频时刻回看证据",
                "逐帧区域统计（独立规则演示）",
                "停留/折返规则功能验证（独立规则演示）",
            ],
            "pending": [
                "真实场景准确率评估",
                "遮挡与密集人群验证",
                "跨摄像头关联",
                "现场规则阈值校准",
                "规则检出率与误报率评价",
            ],
            "disabled": [
                {
                    "name": "本页规则输出",
                    "text": "此页只做轨迹回放；区域统计和规则候选在独立规则演示页展示。",
                    "label": "本页不含规则",
                },
                {
                    "name": "视频理解",
                    "text": "尚未接入视频理解模型，不生成语义解释或告警。",
                    "label": "视频理解未接入",
                },
                {
                    "name": "现场评价",
                    "text": "没有现场视频、业务区域和人工 GT，规则只完成公开样例功能验证。",
                    "label": "现场评价未开展",
                },
                {
                    "name": "实时与多路",
                    "text": "当前只有一段公开视频的离线结果，不表示可实时处理多路监控。",
                    "label": "实时与多路未接入",
                },
            ],
        },
        "quick_picks": [
            {
                "track_id": default_track["id"],
                "label": "示例目标",
                "hint": f"{default_track['start']:.1f}–{default_track['last']:.1f} 秒",
            },
            {
                "track_id": longest["id"],
                "label": "全程持续",
                "hint": f"{longest['start']:.1f}–{longest['last']:.1f} 秒",
            },
            {
                "track_id": late_long["id"],
                "label": "后段进入",
                "hint": f"{late_long['start']:.1f} 秒首次出现",
            },
        ],
        "default_track_id": default_track["id"],
        "rule_demo": {
            "url": "/rules.html",
            "label": "区域统计与规则",
            "status": "公开样例功能验证；候选待人工复核，阈值未经现场校准",
        },
        "tracks": tracks,
        "provenance": {
            "source_url": manifest["source_url"],
            "source_sha256": manifest["sha256"],
            "camera_id": manifest["camera_id"],
            "video_id": manifest["video_id"],
            "usage_scope": manifest["usage_scope"],
            "timestamp_basis": manifest["timestamp_basis"],
            "vfr_limit": manifest["vfr_limit"],
            "run_id": run["run_id"],
            "config_hash": run.get("config_hash"),
            "model_hash": run.get("model_hash"),
            "components_lock_hash": run.get("components_lock_hash"),
            "files": provenance_files,
            "notes": [
                "原视频与回放均为本地文件，演示不联网、不调用外部推理服务。",
                "轨迹编号由跟踪算法在本次运行中分配，不能当作真实身份。",
                "轨迹摘要中的最后观察时间只表示观察窗口结束，不代表目标已经离场。",
                "轨迹页不运行规则；区域统计与规则候选在独立规则演示页展示。",
                "规则候选统一为“规则条件触发，待人工复核”，不等同危险事件。",
            ],
        },
    }

    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 E00-D 演示数据（只读现有运行结果）")
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN, help="已完成的运行目录")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="输出 JSON 路径")
    args = parser.parse_args()

    run_dir = args.run if args.run.is_absolute() else (PROJECT_DIR / args.run)
    data = build(run_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")

    size_kb = args.output.stat().st_size / 1024
    print(f"已生成 {args.output}（{size_kb:.0f} KB）")
    print(
        "样例: {run} | {frames} 帧 | {points:,} 轨迹点 | {tracks} 个累计轨迹编号".format(
            run=data["run"]["run_id"],
            frames=data["sample"]["processed_frames"],
            points=data["sample"]["track_point_count"],
            tracks=data["sample"]["track_count"],
        )
    )
    print(
        "视频: 原片 {:.2f} 秒，分析 {:.2f} 秒，未分析尾部 {:.2f} 秒".format(
            data["sample"]["original_duration"],
            data["sample"]["analysis_end"],
            data["sample"]["tail_seconds"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
