"""E03 SQLite track store: idempotent import, resume, query and integrity checks.

The store is a derived artifact. It never writes into the source run directory
and never modifies `tracks.jsonl`, `trace_summaries.jsonl` or `frames.jsonl`.
Only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "e03-store-v1"
BOUNDARY_METHOD = "bbox_bottom_center_ray_casting_closed"
DEFAULT_BATCH_SIZE = 500


class StoreError(Exception):
    """Base error for the E03 store."""


class InputChangedError(StoreError):
    """The source run or its E01 inputs changed since the first import."""


class ConflictError(StoreError):
    """A unique key already exists with different content."""


class ImportValidationError(StoreError):
    """The run inputs are internally inconsistent or incomplete."""


class QueryError(StoreError):
    """Invalid or unsafe query."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_obj(value: Any) -> str:
    return sha256_text(canonical_json(value))


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
                raise ImportValidationError(f"{path}:{line_no}: JSON 解析失败: {exc}") from exc
    return rows


def compute_input_hashes(
    run_dir: Path,
    videos_manifest: Path,
    zones_path: Path,
    source_path: Path,
) -> dict[str, str]:
    files = {
        "run.json": run_dir / "run.json",
        "manifest.json": run_dir / "manifest.json",
        "tracks.jsonl": run_dir / "tracks.jsonl",
        "trace_summaries.jsonl": run_dir / "trace_summaries.jsonl",
        "frames.jsonl": run_dir / "frames.jsonl",
        "source_video": source_path,
        "e01_videos": videos_manifest,
        "e01_zones": zones_path,
    }
    hashes = {}
    for label, path in files.items():
        if not path.is_file():
            raise ImportValidationError(f"输入文件不存在: {label}: {path}")
        hashes[label] = sha256_file(path)
    return hashes


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    site_id TEXT,
    camera_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    source_group TEXT,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    fps REAL NOT NULL,
    duration_seconds REAL NOT NULL,
    processed_frames INTEGER NOT NULL,
    processed_video_seconds REAL NOT NULL,
    track_point_count INTEGER NOT NULL,
    unique_track_count INTEGER NOT NULL,
    input_hashes_json TEXT NOT NULL,
    input_hashes_sha256 TEXT NOT NULL,
    video_manifest_sha256 TEXT NOT NULL,
    zone_config_sha256 TEXT NOT NULL,
    zone_assignment_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    imported_at_utc TEXT,
    finished_at_utc TEXT,
    updated_at_utc TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS frames (
    run_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    video_time REAL NOT NULL,
    detected_count INTEGER,
    tracked_count INTEGER,
    wall_time_utc TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, frame_index),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tracks (
    run_id TEXT NOT NULL,
    track_id INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    first_observed_time REAL,
    last_observed_time REAL,
    observed_point_count INTEGER NOT NULL DEFAULT 0,
    first_frame_index INTEGER,
    last_frame_index INTEGER,
    max_gap_seconds REAL,
    gap_count INTEGER NOT NULL DEFAULT 0,
    missing_frame_count INTEGER NOT NULL DEFAULT 0,
    summary_start_time REAL,
    summary_last_time REAL,
    summary_point_count INTEGER NOT NULL,
    end_time REAL,
    observation_window_end REAL NOT NULL,
    activity_state TEXT NOT NULL,
    exit_status TEXT NOT NULL,
    window_state TEXT NOT NULL,
    termination_reason TEXT,
    summary_json TEXT NOT NULL,
    summary_hash TEXT NOT NULL,
    updated_at_utc TEXT,
    PRIMARY KEY (run_id, track_id),
    UNIQUE (trace_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS points (
    run_id TEXT NOT NULL,
    track_id INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    video_time REAL NOT NULL,
    bbox_x1 REAL NOT NULL,
    bbox_y1 REAL NOT NULL,
    bbox_x2 REAL NOT NULL,
    bbox_y2 REAL NOT NULL,
    position_x REAL NOT NULL,
    position_y REAL NOT NULL,
    score REAL,
    class_id INTEGER,
    raw_zone_id TEXT,
    raw_zone_ids TEXT,
    coordinate_system TEXT,
    quality_json TEXT,
    source_video_id TEXT,
    source_component TEXT,
    evidence_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, track_id, frame_index),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS zone_definitions (
    zone_id TEXT NOT NULL,
    version TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    polygon_json TEXT NOT NULL,
    coordinate_system TEXT NOT NULL,
    purpose TEXT,
    business_zone INTEGER NOT NULL,
    source_config_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (zone_id, version)
);

CREATE TABLE IF NOT EXISTS point_zones (
    run_id TEXT NOT NULL,
    track_id INTEGER NOT NULL,
    frame_index INTEGER NOT NULL,
    zone_id TEXT NOT NULL,
    zone_version TEXT NOT NULL,
    boundary_method TEXT NOT NULL,
    coordinate_system TEXT NOT NULL,
    derived_hash TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (run_id, track_id, frame_index, zone_id, zone_version),
    FOREIGN KEY (run_id, track_id, frame_index)
        REFERENCES points(run_id, track_id, frame_index) ON DELETE CASCADE,
    FOREIGN KEY (zone_id, zone_version)
        REFERENCES zone_definitions(zone_id, version)
);

CREATE TABLE IF NOT EXISTS import_progress (
    run_id TEXT PRIMARY KEY,
    batch_size INTEGER NOT NULL,
    points_total INTEGER NOT NULL,
    points_imported INTEGER NOT NULL,
    points_skipped_this_run INTEGER NOT NULL,
    batches_done INTEGER NOT NULL,
    last_track_id INTEGER,
    last_frame_index INTEGER,
    zone_assignments INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_points_run_time ON points(run_id, video_time);
CREATE INDEX IF NOT EXISTS idx_points_run_track_time ON points(run_id, track_id, video_time);
CREATE INDEX IF NOT EXISTS idx_points_run_camera ON points(run_id, frame_index);
CREATE INDEX IF NOT EXISTS idx_point_zones_zone ON point_zones(zone_id, run_id);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def _load_context(run_dir: Path, videos_manifest: Path, zones_config: Path) -> dict:
    run_dir = run_dir.resolve()
    for name in ("run.json", "manifest.json", "tracks.jsonl", "trace_summaries.jsonl", "frames.jsonl"):
        if not (run_dir / name).is_file():
            raise ImportValidationError(f"运行目录缺少文件: {run_dir / name}")
    run = load_json(run_dir / "run.json")
    manifest = load_json(run_dir / "manifest.json")
    if run.get("status") != "completed":
        raise ImportValidationError(f"运行状态不是 completed: {run.get('status')!r}")
    source = run.get("source", {})
    source_path = Path(source.get("path", ""))
    if not source_path.is_absolute():
        source_path = run_dir.parent.parent / source_path
    if not source_path.is_file():
        raise ImportValidationError(f"源视频不存在: {source_path}")
    source_sha = sha256_file(source_path)
    if source_sha != source.get("sha256"):
        raise ImportValidationError("源视频 SHA256 与 run.json 不一致")

    videos = load_jsonl(videos_manifest)
    video = next((item for item in videos if item["video_id"] == source.get("video_id")), None)
    if not video:
        raise ImportValidationError(f"E01 视频清单中没有 video_id={source.get('video_id')}")
    if video["sha256"] != source_sha:
        raise ImportValidationError("E01 视频清单与运行源视频 SHA256 不一致")
    if video["camera_id"] != source.get("camera_id"):
        raise ImportValidationError("E01 视频清单与运行 camera_id 不一致")

    zones = load_json(zones_config)
    camera_zones = next(
        (camera for camera in zones.get("cameras", []) if camera.get("camera_id") == source.get("camera_id")),
        None,
    )
    if camera_zones is None:
        raise ImportValidationError("区域配置缺少运行摄像头")
    if camera_zones.get("frame_size") != [manifest.get("width"), manifest.get("height")]:
        raise ImportValidationError("区域配置 frame_size 与视频 manifest 不一致")
    if camera_zones.get("is_field_business_zone") is not False:
        raise ImportValidationError("E03 只接受公开样例的非业务区域配置")

    summaries = load_jsonl(run_dir / "trace_summaries.jsonl")
    frames = load_jsonl(run_dir / "frames.jsonl")
    points = load_jsonl(run_dir / "tracks.jsonl")
    expected_points = int(run["track_point_count"])
    expected_tracks = int(run["unique_track_count"])
    expected_frames = int(run["processed_frames"])
    if len(points) != expected_points:
        raise ImportValidationError(f"tracks.jsonl 点数 {len(points)} != run.json {expected_points}")
    if len(summaries) != expected_tracks:
        raise ImportValidationError(f"trace_summaries 数 {len(summaries)} != run.json {expected_tracks}")
    if len(frames) != expected_frames:
        raise ImportValidationError(f"frames 数 {len(frames)} != run.json {expected_frames}")

    summary_map = {int(row["track_id"]): row for row in summaries}
    if len(summary_map) != expected_tracks:
        raise ImportValidationError("trace_summaries 存在重复 track_id")

    input_hashes = compute_input_hashes(
        run_dir, videos_manifest, zones_config, source_path
    )
    return {
        "run_dir": run_dir,
        "run": run,
        "manifest": manifest,
        "source": source,
        "source_path": source_path,
        "source_sha": source_sha,
        "video": video,
        "zones": zones,
        "camera_zones": camera_zones,
        "summaries": summaries,
        "summary_map": summary_map,
        "frames": frames,
        "points": points,
        "input_hashes": input_hashes,
        "videos_manifest": videos_manifest,
        "zones_config": zones_config,
    }


def _point_tuple(row: dict, run_id: str, source_video_id: str) -> tuple:
    required = [
        "run_id", "track_id", "trace_id", "frame_index", "video_time", "bbox",
        "position", "source_video_id", "evidence_ref",
    ]
    missing = [key for key in required if key not in row]
    if missing:
        raise ImportValidationError(f"轨迹点缺字段: {missing}")
    if row["run_id"] != run_id:
        raise ImportValidationError(f"轨迹点 run_id 混入其他运行: {row['run_id']}")
    if row["source_video_id"] != source_video_id:
        raise ImportValidationError("轨迹点 source_video_id 不一致")
    bbox = row["bbox"]
    position = row["position"]
    if not (isinstance(bbox, list) and len(bbox) == 4):
        raise ImportValidationError(f"track {row['track_id']} bbox 无效")
    if not (isinstance(position, list) and len(position) == 2):
        raise ImportValidationError(f"track {row['track_id']} position 无效")
    frame_index = int(row["frame_index"])
    video_time = float(row["video_time"])
    evidence = row["evidence_ref"]
    if not isinstance(evidence, dict):
        raise ImportValidationError("evidence_ref 不是对象")
    raw = canonical_json(row)
    return (
        run_id,
        int(row["track_id"]),
        str(row["trace_id"]),
        frame_index,
        video_time,
        float(bbox[0]),
        float(bbox[1]),
        float(bbox[2]),
        float(bbox[3]),
        float(position[0]),
        float(position[1]),
        float(row["score"]) if row.get("score") is not None else None,
        int(row["class_id"]) if row.get("class_id") is not None else None,
        row.get("zone_id"),
        canonical_json(row.get("zone_ids", [])),
        row.get("coordinate_system"),
        canonical_json(row.get("quality", {})),
        row["source_video_id"],
        row.get("source_component"),
        canonical_json(evidence),
        raw,
        hash_obj(row),
    )


def _frame_tuple(row: dict, run_id: str) -> tuple:
    raw = canonical_json(row)
    return (
        run_id,
        int(row["frame_index"]),
        float(row["video_time"]),
        int(row["detected_count"]) if row.get("detected_count") is not None else None,
        int(row["tracked_count"]) if row.get("tracked_count") is not None else None,
        row.get("wall_time_utc"),
        raw,
        hash_obj(row),
    )


def _insert_or_conflict(
    conn: sqlite3.Connection,
    table: str,
    key_sql: str,
    key_values: tuple,
    columns: list[str],
    values: tuple,
    content_hash: str,
) -> int:
    placeholders = ",".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    if cursor.rowcount == 1:
        return 1
    existing = conn.execute(
        f"SELECT content_hash FROM {table} WHERE {key_sql}", key_values
    ).fetchone()
    if existing is None:
        raise ConflictError(f"{table} 插入被忽略但找不到已有记录: {key_values}")
    if existing["content_hash"] != content_hash:
        raise ConflictError(
            f"{table} 唯一键冲突且内容不同: {key_values}; "
            f"existing={existing['content_hash'][:12]} new={content_hash[:12]}"
        )
    return 0


def _refresh_tracks(
    conn: sqlite3.Connection,
    run_id: str,
    summary_map: dict[int, dict],
    window_end: float,
    run_status: str,
    track_ids: Iterable[int] | None = None,
) -> None:
    ids = sorted(set(track_ids)) if track_ids is not None else sorted(summary_map)
    for track_id in ids:
        if track_id not in summary_map:
            continue
        rows = conn.execute(
            "SELECT video_time, frame_index FROM points WHERE run_id=? AND track_id=? "
            "ORDER BY video_time, frame_index",
            (run_id, track_id),
        ).fetchall()
        if rows:
            times = [float(row["video_time"]) for row in rows]
            frames = [int(row["frame_index"]) for row in rows]
            gap_count = 0
            missing_frame_count = 0
            max_gap = 0.0
            for prev_time, curr_time, prev_frame, curr_frame in zip(
                times, times[1:], frames, frames[1:]
            ):
                frame_delta = curr_frame - prev_frame
                if frame_delta > 1:
                    gap_count += 1
                    missing_frame_count += frame_delta - 1
                    max_gap = max(max_gap, curr_time - prev_time)
            max_gap = round(max_gap, 6)
            first_time = times[0]
            last_time = times[-1]
            first_frame = frames[0]
            last_frame = frames[-1]
            count = len(rows)
        else:
            first_time = last_time = first_frame = last_frame = None
            count = 0
            max_gap = 0.0
            gap_count = 0
            missing_frame_count = 0
        if count == 0:
            activity = "not_yet_observed"
        elif run_status != "completed":
            activity = "observed_at_import_cut"
        elif last_time >= window_end - 0.05:
            activity = "observed_near_window_end"
        else:
            activity = "not_observed_before_window_end"
        window_state = "closed" if run_status == "completed" else "open"
        conn.execute(
            "UPDATE tracks SET first_observed_time=?, last_observed_time=?, "
            "observed_point_count=?, first_frame_index=?, last_frame_index=?, "
            "max_gap_seconds=?, gap_count=?, missing_frame_count=?, observation_window_end=?, "
            "activity_state=?, exit_status=?, window_state=?, updated_at_utc=? "
            "WHERE run_id=? AND track_id=?",
            (
                first_time,
                last_time,
                count,
                first_frame,
                last_frame,
                max_gap,
                gap_count,
                missing_frame_count,
                window_end,
                activity,
                "unknown_not_confirmed",
                window_state,
                now_utc(),
                run_id,
                track_id,
            ),
        )


def _point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            xinters = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xinters:
                inside = not inside
        j = i
    return inside


def _insert_zone_definitions(
    conn: sqlite3.Connection,
    context: dict,
    zone_config_sha256: str,
) -> None:
    camera = context["camera_zones"]
    video = context["video"]
    for zone in camera.get("zones", []):
        definition = {
            "zone_id": zone["zone_id"],
            "version": zone["version"],
            "camera_id": camera["camera_id"],
            "video_id": video["video_id"],
            "polygon": zone["polygon"],
            "coordinate_system": zone["coordinate_system"],
            "purpose": zone.get("purpose"),
            "business_zone": bool(zone.get("business_zone", False)),
            "source_config_sha256": zone_config_sha256,
        }
        cursor = conn.execute(
            "INSERT OR IGNORE INTO zone_definitions "
            "(zone_id, version, camera_id, video_id, polygon_json, coordinate_system, "
            "purpose, business_zone, source_config_sha256, created_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                definition["zone_id"],
                definition["version"],
                definition["camera_id"],
                definition["video_id"],
                canonical_json(definition["polygon"]),
                definition["coordinate_system"],
                definition["purpose"],
                1 if definition["business_zone"] else 0,
                zone_config_sha256,
                now_utc(),
            ),
        )
        if cursor.rowcount == 0:
            existing = conn.execute(
                "SELECT polygon_json, coordinate_system, purpose, business_zone, "
                "source_config_sha256 FROM zone_definitions WHERE zone_id=? AND version=?",
                (definition["zone_id"], definition["version"]),
            ).fetchone()
            expected = (
                canonical_json(definition["polygon"]),
                definition["coordinate_system"],
                definition["purpose"],
                1 if definition["business_zone"] else 0,
                zone_config_sha256,
            )
            actual = tuple(existing) if existing else None
            if actual != expected:
                raise ConflictError(f"区域定义冲突: {definition['zone_id']}@{definition['version']}")


def _derive_zone_assignments(
    conn: sqlite3.Connection,
    run_id: str,
    context: dict,
    zone_config_sha256: str,
) -> int:
    _insert_zone_definitions(conn, context, zone_config_sha256)
    camera = context["camera_zones"]
    zones = camera.get("zones", [])
    if not zones:
        return 0
    inserted = 0
    points = conn.execute(
        "SELECT track_id, frame_index, bbox_x1, bbox_y1, bbox_x2, bbox_y2, video_time, trace_id "
        "FROM points WHERE run_id=? ORDER BY track_id, frame_index",
        (run_id,),
    ).fetchall()
    for point in points:
        x = (point["bbox_x1"] + point["bbox_x2"]) / 2.0
        y = point["bbox_y2"]
        for zone in zones:
            if not _point_in_polygon(x, y, zone["polygon"]):
                continue
            derived = {
                "run_id": run_id,
                "track_id": int(point["track_id"]),
                "frame_index": int(point["frame_index"]),
                "zone_id": zone["zone_id"],
                "zone_version": zone["version"],
                "boundary_method": BOUNDARY_METHOD,
                "coordinate_system": zone["coordinate_system"],
            }
            cursor = conn.execute(
                "INSERT OR IGNORE INTO point_zones "
                "(run_id, track_id, frame_index, zone_id, zone_version, boundary_method, "
                "coordinate_system, derived_hash, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    int(point["track_id"]),
                    int(point["frame_index"]),
                    zone["zone_id"],
                    zone["version"],
                    BOUNDARY_METHOD,
                    zone["coordinate_system"],
                    hash_obj(derived),
                    now_utc(),
                ),
            )
            if cursor.rowcount == 1:
                inserted += 1
            else:
                existing = conn.execute(
                    "SELECT derived_hash FROM point_zones WHERE run_id=? AND track_id=? "
                    "AND frame_index=? AND zone_id=? AND zone_version=?",
                    (
                        run_id,
                        int(point["track_id"]),
                        int(point["frame_index"]),
                        zone["zone_id"],
                        zone["version"],
                    ),
                ).fetchone()
                if not existing or existing["derived_hash"] != hash_obj(derived):
                    raise ConflictError("区域派生结果冲突")
    return inserted


def _update_progress(
    conn: sqlite3.Connection,
    run_id: str,
    batch_size: int,
    points_total: int,
    points_imported: int,
    skipped: int,
    batches_done: int,
    last_track_id: int | None,
    last_frame_index: int | None,
    status: str,
    zone_assignments: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO import_progress "
        "(run_id, batch_size, points_total, points_imported, points_skipped_this_run, "
        "batches_done, last_track_id, last_frame_index, zone_assignments, status, updated_at_utc, error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id) DO UPDATE SET batch_size=excluded.batch_size, "
        "points_total=excluded.points_total, points_imported=excluded.points_imported, "
        "points_skipped_this_run=excluded.points_skipped_this_run, batches_done=excluded.batches_done, "
        "last_track_id=excluded.last_track_id, last_frame_index=excluded.last_frame_index, "
        "zone_assignments=excluded.zone_assignments, status=excluded.status, "
        "updated_at_utc=excluded.updated_at_utc, error=excluded.error",
        (
            run_id,
            batch_size,
            points_total,
            points_imported,
            skipped,
            batches_done,
            last_track_id,
            last_frame_index,
            zone_assignments,
            status,
            now_utc(),
            error,
        ),
    )


def import_run(
    db_path: Path,
    run_dir: Path,
    videos_manifest: Path,
    zones_config: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    export_zones: Path | None = None,
) -> dict:
    """Import one completed run. Re-running is idempotent.

    max_batches is a deterministic pause hook for recovery tests. It leaves the
    store in `paused` state with committed batches; a later call resumes.
    """
    db_path = db_path.resolve()
    run_dir = run_dir.resolve()
    videos_manifest = videos_manifest.resolve()
    zones_config = zones_config.resolve()
    context = _load_context(run_dir, videos_manifest, zones_config)
    run = context["run"]
    run_id = run["run_id"]
    input_hashes = context["input_hashes"]
    input_hashes_json = canonical_json(input_hashes)
    input_hashes_sha = sha256_text(input_hashes_json)
    window_end = float(run["processed_video_seconds"])
    zone_sha = sha256_file(zones_config)

    conn = connect(db_path)
    try:
        init_schema(conn)
        existing = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if existing is not None:
            existing_hashes = json.loads(existing["input_hashes_json"])
            if existing_hashes != input_hashes:
                changed = sorted(
                    key for key in set(existing_hashes) | set(input_hashes)
                    if existing_hashes.get(key) != input_hashes.get(key)
                )
                raise InputChangedError(
                    f"运行 {run_id} 的输入已变化，拒绝拼接: {', '.join(changed)}"
                )
            if existing["source_sha256"] != context["source_sha"]:
                raise InputChangedError(f"运行 {run_id} 的源视频哈希已变化")
        else:
            conn.execute(
                "INSERT INTO runs "
                "(run_id, site_id, camera_id, video_id, source_group, source_path, source_sha256, "
                "fps, duration_seconds, processed_frames, processed_video_seconds, "
                "track_point_count, unique_track_count, input_hashes_json, input_hashes_sha256, "
                "video_manifest_sha256, zone_config_sha256, zone_assignment_count, status, "
                "imported_at_utc, updated_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    run.get("site_id"),
                    context["source"]["camera_id"],
                    context["source"]["video_id"],
                    context["source"].get("source_group"),
                    str(context["source_path"]),
                    context["source_sha"],
                    float(context["manifest"]["fps"]),
                    float(context["manifest"]["duration_seconds"]),
                    int(run["processed_frames"]),
                    window_end,
                    int(run["track_point_count"]),
                    int(run["unique_track_count"]),
                    input_hashes_json,
                    input_hashes_sha,
                    sha256_file(videos_manifest),
                    zone_sha,
                    0,
                    "importing",
                    now_utc(),
                    now_utc(),
                ),
            )
        conn.execute(
            "UPDATE runs SET status='importing', error=NULL, updated_at_utc=? WHERE run_id=?",
            (now_utc(), run_id),
        )

        # Frames: small table, import before points.
        for frame in context["frames"]:
            tuple_values = _frame_tuple(frame, run_id)
            _insert_or_conflict(
                conn,
                "frames",
                "run_id=? AND frame_index=?",
                (run_id, int(frame["frame_index"])),
                [
                    "run_id", "frame_index", "video_time", "detected_count",
                    "tracked_count", "wall_time_utc", "raw_json", "content_hash",
                ],
                tuple_values,
                hash_obj(frame),
            )

        # Track skeletons from summaries. A later batch updates observed state.
        for summary in context["summaries"]:
            track_id = int(summary["track_id"])
            summary_hash = hash_obj(summary)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO tracks "
                "(run_id, track_id, trace_id, summary_start_time, summary_last_time, "
                "summary_point_count, end_time, observation_window_end, activity_state, "
                "exit_status, window_state, termination_reason, summary_json, summary_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    track_id,
                    summary["trace_id"],
                    summary.get("start_time"),
                    summary.get("last_observed_time"),
                    int(summary["observed_point_count"]),
                    summary.get("end_time"),
                    window_end,
                    "not_yet_observed",
                    "unknown_not_confirmed",
                    "open",
                    summary.get("termination_reason"),
                    canonical_json(summary),
                    summary_hash,
                ),
            )
            if cursor.rowcount == 0:
                existing_track = conn.execute(
                    "SELECT summary_hash, trace_id FROM tracks WHERE run_id=? AND track_id=?",
                    (run_id, track_id),
                ).fetchone()
                if not existing_track or existing_track["summary_hash"] != summary_hash:
                    raise ConflictError(f"轨迹摘要冲突: {run_id}/{track_id}")
                if existing_track["trace_id"] != summary["trace_id"]:
                    raise ConflictError(f"trace_id 冲突: {run_id}/{track_id}")

        points = context["points"]
        total = len(points)
        inserted = 0
        skipped = 0
        batches_done = 0
        last_track_id = None
        last_frame_index = None
        for offset in range(0, total, batch_size):
            batch = points[offset : offset + batch_size]
            batch_track_ids = set()
            for row in batch:
                values = _point_tuple(row, run_id, context["source"]["video_id"])
                key = (run_id, int(row["track_id"]), int(row["frame_index"]))
                changed = _insert_or_conflict(
                    conn,
                    "points",
                    "run_id=? AND track_id=? AND frame_index=?",
                    key,
                    [
                        "run_id", "track_id", "trace_id", "frame_index", "video_time",
                        "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "position_x",
                        "position_y", "score", "class_id", "raw_zone_id", "raw_zone_ids",
                        "coordinate_system", "quality_json", "source_video_id",
                        "source_component", "evidence_json", "raw_json", "content_hash",
                    ],
                    values,
                    hash_obj(row),
                )
                if changed:
                    inserted += 1
                else:
                    skipped += 1
                batch_track_ids.add(int(row["track_id"]))
                last_track_id = int(row["track_id"])
                last_frame_index = int(row["frame_index"])
            conn.commit()
            batches_done += 1
            _refresh_tracks(conn, run_id, context["summary_map"], window_end, "importing", batch_track_ids)
            imported_count = conn.execute(
                "SELECT COUNT(*) AS n FROM points WHERE run_id=?", (run_id,)
            ).fetchone()["n"]
            _update_progress(
                conn, run_id, batch_size, total, imported_count, skipped,
                batches_done, last_track_id, last_frame_index, "importing",
            )
            conn.commit()
            if max_batches is not None and batches_done >= max_batches and offset + batch_size < total:
                conn.execute(
                    "UPDATE runs SET status='paused', updated_at_utc=? WHERE run_id=?",
                    (now_utc(), run_id),
                )
                _update_progress(
                    conn, run_id, batch_size, total, imported_count, skipped,
                    batches_done, last_track_id, last_frame_index, "paused",
                )
                conn.commit()
                return {
                    "run_id": run_id,
                    "status": "paused",
                    "points_imported": imported_count,
                    "points_inserted_this_run": inserted,
                    "points_skipped_this_run": skipped,
                    "points_total": total,
                    "batches_done": batches_done,
                    "db": str(db_path),
                }

        # Final integrity and incremental state.
        _refresh_tracks(conn, run_id, context["summary_map"], window_end, "completed")
        db_counts = {
            "points": conn.execute("SELECT COUNT(*) AS n FROM points WHERE run_id=?", (run_id,)).fetchone()["n"],
            "frames": conn.execute("SELECT COUNT(*) AS n FROM frames WHERE run_id=?", (run_id,)).fetchone()["n"],
            "tracks": conn.execute("SELECT COUNT(*) AS n FROM tracks WHERE run_id=?", (run_id,)).fetchone()["n"],
        }
        if db_counts["points"] != total:
            raise ImportValidationError(f"导入后点数 {db_counts['points']} != 输入 {total}")
        if db_counts["frames"] != len(context["frames"]):
            raise ImportValidationError("导入后帧数不一致")
        if db_counts["tracks"] != len(context["summaries"]):
            raise ImportValidationError("导入后轨迹数不一致")
        for summary in context["summaries"]:
            track_id = int(summary["track_id"])
            actual = conn.execute(
                "SELECT observed_point_count, first_observed_time, last_observed_time "
                "FROM tracks WHERE run_id=? AND track_id=?",
                (run_id, track_id),
            ).fetchone()
            if actual is None:
                raise ImportValidationError(f"缺少轨迹状态: {track_id}")
            if actual["observed_point_count"] != int(summary["observed_point_count"]):
                raise ImportValidationError(
                    f"轨迹 {track_id} 点数 {actual['observed_point_count']} != 摘要 {summary['observed_point_count']}"
                )
            if actual["first_observed_time"] is None or abs(actual["first_observed_time"] - float(summary["start_time"])) > 0.001:
                raise ImportValidationError(f"轨迹 {track_id} 首次观测时间不一致")
            if actual["last_observed_time"] is None or abs(actual["last_observed_time"] - float(summary["last_observed_time"])) > 0.001:
                raise ImportValidationError(f"轨迹 {track_id} 最后观测时间不一致")

        zone_assignments = _derive_zone_assignments(conn, run_id, context, zone_sha)
        # Re-derive is idempotent; count actual table rows for the final record.
        zone_assignments = conn.execute(
            "SELECT COUNT(*) AS n FROM point_zones WHERE run_id=?", (run_id,)
        ).fetchone()["n"]
        conn.execute(
            "UPDATE runs SET status='completed', zone_assignment_count=?, "
            "finished_at_utc=?, updated_at_utc=?, error=NULL WHERE run_id=?",
            (zone_assignments, now_utc(), now_utc(), run_id),
        )
        _update_progress(
            conn, run_id, batch_size, total, db_counts["points"], skipped,
            batches_done, last_track_id, last_frame_index, "completed",
            zone_assignments=zone_assignments,
        )
        conn.commit()

        if export_zones is not None:
            export_zones = Path(export_zones)
            export_zone_assignments(conn, run_id, export_zones)

        return {
            "run_id": run_id,
            "status": "completed",
            "points_total": total,
            "points_imported": db_counts["points"],
            "points_inserted_this_run": inserted,
            "points_skipped_this_run": skipped,
            "frames": db_counts["frames"],
            "tracks": db_counts["tracks"],
            "zone_assignments": zone_assignments,
            "batches_done": batches_done,
            "db": str(db_path),
        }
    except Exception as exc:
        conn.rollback()
        if isinstance(exc, InputChangedError):
            raise
        try:
            conn.execute(
                "UPDATE runs SET status='failed', error=?, updated_at_utc=? WHERE run_id=?",
                (f"{type(exc).__name__}: {exc}", now_utc(), run_id),
            )
            progress = conn.execute(
                "SELECT batch_size, points_total, points_imported, points_skipped_this_run, "
                "batches_done, last_track_id, last_frame_index, zone_assignments "
                "FROM import_progress WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if progress:
                _update_progress(
                    conn, run_id, progress["batch_size"], progress["points_total"],
                    progress["points_imported"], progress["points_skipped_this_run"],
                    progress["batches_done"], progress["last_track_id"],
                    progress["last_frame_index"], "failed",
                    zone_assignments=progress["zone_assignments"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    finally:
        conn.close()


def export_zone_assignments(conn: sqlite3.Connection, run_id: str, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT pz.run_id, p.trace_id, pz.track_id, pz.frame_index, p.video_time, "
        "pz.zone_id, pz.zone_version, pz.boundary_method, pz.coordinate_system, pz.derived_hash "
        "FROM point_zones pz JOIN points p ON p.run_id=pz.run_id "
        "AND p.track_id=pz.track_id AND p.frame_index=pz.frame_index "
        "WHERE pz.run_id=? ORDER BY p.video_time, pz.track_id, pz.zone_id",
        (run_id,),
    ).fetchall()
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
    return len(rows)


def query_points(
    db_path: Path,
    run_id: str | None = None,
    camera_id: str | None = None,
    track_id: int | None = None,
    start: float | None = None,
    end: float | None = None,
    zone_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    if not Path(db_path).is_file():
        raise QueryError(f"数据库不存在: {db_path}")
    if track_id is not None and not run_id:
        raise QueryError("按 track_id 查询必须同时指定 run_id，局部编号不能跨运行混用")
    if start is not None and end is not None and end < start:
        raise QueryError("end 必须 >= start")
    if limit <= 0 or limit > 10000:
        raise QueryError("limit 必须在 1..10000")
    if not any(value is not None for value in (run_id, camera_id, track_id, start, end, zone_id)):
        raise QueryError("至少需要一个查询条件，禁止无界全表导出")

    conn = connect(Path(db_path))
    try:
        where = []
        params: list[Any] = []
        joins = ""
        if run_id:
            where.append("p.run_id=?")
            params.append(run_id)
        if camera_id:
            where.append("r.camera_id=?")
            params.append(camera_id)
        if track_id is not None:
            where.append("p.track_id=?")
            params.append(track_id)
        if start is not None:
            where.append("p.video_time>=?")
            params.append(start)
        if end is not None:
            where.append("p.video_time<=?")
            params.append(end)
        if zone_id:
            joins = (
                " JOIN point_zones pz ON pz.run_id=p.run_id AND pz.track_id=p.track_id "
                "AND pz.frame_index=p.frame_index "
            )
            where.append("pz.zone_id=?")
            params.append(zone_id)
        sql = (
            "SELECT DISTINCT p.*, r.camera_id, r.source_path, r.zone_config_sha256, r.status AS store_status "
            "FROM points p JOIN runs r ON r.run_id=p.run_id " + joins
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY p.run_id, p.video_time, p.track_id LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            zone_rows = conn.execute(
                "SELECT zone_id, zone_version, boundary_method, coordinate_system "
                "FROM point_zones WHERE run_id=? AND track_id=? AND frame_index=? "
                "ORDER BY zone_id",
                (row["run_id"], row["track_id"], row["frame_index"]),
            ).fetchall()
            results.append(
                {
                    "run_id": row["run_id"],
                    "trace_id": row["trace_id"],
                    "camera_id": row["camera_id"],
                    "track_id": row["track_id"],
                    "frame_index": row["frame_index"],
                    "video_time": row["video_time"],
                    "bbox": [row["bbox_x1"], row["bbox_y1"], row["bbox_x2"], row["bbox_y2"]],
                    "position": [row["position_x"], row["position_y"]],
                    "score": row["score"],
                    "class_id": row["class_id"],
                    "source_video_id": row["source_video_id"],
                    "source_component": row["source_component"],
                    "zone_ids": [zone["zone_id"] for zone in zone_rows],
                    "zone_versions": [zone["zone_version"] for zone in zone_rows],
                    "boundary_method": zone_rows[0]["boundary_method"] if zone_rows else None,
                    "coordinate_system": zone_rows[0]["coordinate_system"] if zone_rows else None,
                    "store_status": row["store_status"],
                    "evidence": {
                        "video_path": row["source_path"],
                        "run_id": row["run_id"],
                        "track_id": row["track_id"],
                        "trace_id": row["trace_id"],
                        "frame_index": row["frame_index"],
                        "video_time": row["video_time"],
                    },
                }
            )
        return results
    finally:
        conn.close()


def query_track_state(db_path: Path, run_id: str, track_id: int) -> dict:
    if not Path(db_path).is_file():
        raise QueryError(f"数据库不存在: {db_path}")
    conn = connect(Path(db_path))
    try:
        row = conn.execute(
            "SELECT t.*, r.camera_id, r.source_path, r.status AS store_status, r.zone_config_sha256 "
            "FROM tracks t JOIN runs r ON r.run_id=t.run_id "
            "WHERE t.run_id=? AND t.track_id=?",
            (run_id, track_id),
        ).fetchone()
        if row is None:
            raise QueryError(f"找不到轨迹状态: {run_id}/{track_id}")
        return {
            "run_id": row["run_id"],
            "track_id": row["track_id"],
            "trace_id": row["trace_id"],
            "camera_id": row["camera_id"],
            "source_path": row["source_path"],
            "first_observed_time": row["first_observed_time"],
            "last_observed_time": row["last_observed_time"],
            "observed_point_count": row["observed_point_count"],
            "summary_point_count": row["summary_point_count"],
            "first_frame_index": row["first_frame_index"],
            "last_frame_index": row["last_frame_index"],
            "max_gap_seconds": row["max_gap_seconds"],
            "gap_count": row["gap_count"],
            "missing_frame_count": row["missing_frame_count"],
            "observation_window_end": row["observation_window_end"],
            "activity_state": row["activity_state"],
            "exit_status": row["exit_status"],
            "window_state": row["window_state"],
            "end_time": row["end_time"],
            "termination_reason": row["termination_reason"],
            "store_status": row["store_status"],
            "zone_config_sha256": row["zone_config_sha256"],
        }
    finally:
        conn.close()


def verify_store(
    db_path: Path,
    run_dir: Path,
    videos_manifest: Path,
    zones_config: Path,
) -> dict:
    if not Path(db_path).is_file():
        return {"ok": False, "run_id": None, "errors": [f"数据库不存在: {db_path}"], "warnings": []}
    context = _load_context(run_dir.resolve(), videos_manifest.resolve(), zones_config.resolve())
    run = context["run"]
    run_id = run["run_id"]
    conn = connect(Path(db_path))
    errors = []
    warnings = []
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return {"ok": False, "run_id": run_id, "errors": ["DB 中没有该运行"], "warnings": []}
        if row["status"] != "completed":
            errors.append(f"run status={row['status']}，不是 completed")
        if json.loads(row["input_hashes_json"]) != context["input_hashes"]:
            errors.append("DB 记录的输入哈希与当前输入不一致")
        if row["source_sha256"] != context["source_sha"]:
            errors.append("DB 源视频哈希不一致")

        counts = {
            "points": conn.execute("SELECT COUNT(*) AS n FROM points WHERE run_id=?", (run_id,)).fetchone()["n"],
            "distinct_point_keys": conn.execute(
                "SELECT COUNT(*) AS n FROM (SELECT DISTINCT track_id, frame_index FROM points WHERE run_id=?)",
                (run_id,),
            ).fetchone()["n"],
            "frames": conn.execute("SELECT COUNT(*) AS n FROM frames WHERE run_id=?", (run_id,)).fetchone()["n"],
            "tracks": conn.execute("SELECT COUNT(*) AS n FROM tracks WHERE run_id=?", (run_id,)).fetchone()["n"],
            "zone_assignments": conn.execute("SELECT COUNT(*) AS n FROM point_zones WHERE run_id=?", (run_id,)).fetchone()["n"],
        }
        expected = {
            "points": int(run["track_point_count"]),
            "frames": int(run["processed_frames"]),
            "tracks": int(run["unique_track_count"]),
        }
        for key, value in expected.items():
            if counts[key] != value:
                errors.append(f"{key} 数量 {counts[key]} != run.json {value}")
        if counts["distinct_point_keys"] != counts["points"]:
            errors.append("points 存在重复唯一键")

        db_points = {
            (row["run_id"], row["track_id"], row["frame_index"]): row["content_hash"]
            for row in conn.execute(
                "SELECT run_id, track_id, frame_index, content_hash FROM points WHERE run_id=?",
                (run_id,),
            )
        }
        source_points = {}
        for point in context["points"]:
            key = (run_id, int(point["track_id"]), int(point["frame_index"]))
            if key in source_points:
                errors.append(f"源 tracks.jsonl 重复唯一键: {key}")
            source_points[key] = hash_obj(point)
        if set(source_points) != set(db_points):
            missing = len(set(source_points) - set(db_points))
            extra = len(set(db_points) - set(source_points))
            errors.append(f"point key 集合不一致: missing={missing}, extra={extra}")
        mismatched = [key for key in source_points.keys() & db_points.keys() if source_points[key] != db_points[key]]
        if mismatched:
            errors.append(f"{len(mismatched)} 个点内容哈希不一致，示例 {mismatched[:3]}")

        for summary in context["summaries"]:
            track_id = int(summary["track_id"])
            track = conn.execute(
                "SELECT * FROM tracks WHERE run_id=? AND track_id=?", (run_id, track_id)
            ).fetchone()
            if track is None:
                errors.append(f"缺少轨迹状态 {track_id}")
                continue
            if track["observed_point_count"] != int(summary["observed_point_count"]):
                errors.append(f"轨迹 {track_id} 实际点数与摘要不一致")
            if track["last_observed_time"] is None or abs(track["last_observed_time"] - float(summary["last_observed_time"])) > 0.001:
                errors.append(f"轨迹 {track_id} 最后观测时间不一致")
            if track["exit_status"] != "unknown_not_confirmed":
                errors.append(f"轨迹 {track_id} exit_status 不应是已离场")
            if track["window_state"] != "closed":
                errors.append(f"轨迹 {track_id} window_state 不是 closed")
            expected_activity = (
                "observed_near_window_end"
                if track["last_observed_time"] is not None
                and track["last_observed_time"] >= track["observation_window_end"] - 0.05
                else "not_observed_before_window_end"
            )
            if track["observed_point_count"] == 0:
                expected_activity = "not_yet_observed"
            if track["activity_state"] != expected_activity:
                errors.append(f"轨迹 {track_id} activity_state 与最后观测不一致")

        if counts["zone_assignments"] != row["zone_assignment_count"]:
            errors.append("zone_assignment_count 与 point_zones 实际数量不一致")
        if context["camera_zones"].get("zones"):
            bad = conn.execute(
                "SELECT COUNT(*) AS n FROM point_zones WHERE run_id=? "
                "AND (zone_version IS NULL OR boundary_method IS NULL OR coordinate_system IS NULL)",
                (run_id,),
            ).fetchone()["n"]
            if bad:
                errors.append(f"{bad} 条区域派生记录缺少版本/边界方式/坐标系")
        else:
            warnings.append("摄像头没有区域配置；区域查询将为空")

        return {
            "ok": not errors,
            "run_id": run_id,
            "counts": counts,
            "expected": expected,
            "status": row["status"],
            "errors": errors,
            "warnings": warnings,
            "db": str(db_path),
        }
    finally:
        conn.close()
