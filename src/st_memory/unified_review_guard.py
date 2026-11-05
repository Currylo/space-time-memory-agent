"""Server-side review admission checks for the same-source platform page.

The browser may disable a button, but the HTTP handler must independently
reject stale alignment, incomplete evidence, missing clips or mismatched
candidate/replay identities.  Legacy three-case reviews do not have the new
clock/evidence fields and are handled by their existing evidence rules.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_unified_review(
    payload: dict,
    item: dict,
    ledger_row: dict,
    evidence_path: Path | None,
) -> dict:
    """Return ``{"ok": bool, "errors": [...], "checks": {...}}``."""
    errors: list[str] = []
    checks: dict[str, Any] = {}

    clock = payload.get("clock") or {}
    replay = payload.get("replay") or {}
    source = payload.get("source") or {}
    clock_status = clock.get("status")
    checks["clock_status"] = clock_status
    if clock_status != "verified":
        errors.append(f"clock_status_{clock_status or 'missing'}")

    checks["replay_id"] = {
        "payload": replay.get("replay_id"),
        "candidate": item.get("replay_id"),
        "match": bool(replay.get("replay_id")) and replay.get("replay_id") == item.get("replay_id"),
    }
    if not checks["replay_id"]["match"]:
        errors.append("replay_id_mismatch")
    checks["camera"] = {
        "payload": replay.get("camera"),
        "candidate": item.get("camera"),
        "match": bool(replay.get("camera")) and replay.get("camera") == item.get("camera"),
    }
    if not checks["camera"]["match"]:
        errors.append("camera_mismatch")
    checks["loop_index"] = {
        "payload": replay.get("loop_index"),
        "candidate": item.get("loop_index"),
        "match": replay.get("loop_index") == item.get("loop_index"),
    }
    if not checks["loop_index"]["match"]:
        errors.append("loop_index_mismatch")
    checks["source_hash"] = {
        "payload": source.get("sha256"),
        "candidate": item.get("source_video_sha256"),
        "match": bool(source.get("sha256")) and source.get("sha256") == item.get("source_video_sha256"),
    }
    if not checks["source_hash"]["match"]:
        errors.append("source_hash_mismatch")

    evidence = item.get("evidence") or {}
    window = evidence.get("window") or {}
    checks["evidence_status"] = evidence.get("status")
    if evidence.get("status") != "verified":
        errors.append(f"evidence_status_{evidence.get('status') or 'missing'}")
    checks["window_condition_ok"] = window.get("condition_ok")
    if window.get("condition_ok") is not True:
        errors.append("window_condition_not_verified")

    clip = item.get("clip") or {}
    coverage = clip.get("coverage") or {}
    checks["clip_http_status"] = clip.get("http_status")
    checks["clip_decoded"] = clip.get("decoded")
    checks["clip_coverage_status"] = coverage.get("status")
    if clip.get("http_status") != 200:
        errors.append("clip_not_downloaded")
    if clip.get("decoded") is not True:
        errors.append("clip_not_decoded")
    if coverage.get("status") != "verified":
        errors.append(f"clip_coverage_{coverage.get('status') or 'missing'}")

    checks["ledger_event_id"] = {
        "candidate": item.get("event_id"),
        "ledger": ledger_row.get("event_id"),
        "match": bool(item.get("event_id")) and item.get("event_id") == ledger_row.get("event_id"),
    }
    if not checks["ledger_event_id"]["match"]:
        errors.append("event_id_mismatch")
    checks["ledger_key"] = {
        "candidate": item.get("ledger_key"),
        "ledger": ledger_row.get("key"),
        "match": bool(item.get("ledger_key")) and item.get("ledger_key") == ledger_row.get("key"),
    }
    if not checks["ledger_key"]["match"]:
        errors.append("ledger_key_mismatch")

    checks["rule_window"] = {
        "start": item.get("rule_window_start"),
        "end": item.get("rule_window_end"),
        "valid": (
            item.get("rule_window_start") is not None
            and item.get("rule_window_end") is not None
            and float(item["rule_window_start"]) < float(item["rule_window_end"])
        ),
    }
    if not checks["rule_window"]["valid"]:
        errors.append("rule_window_missing_or_invalid")

    expected_sha = clip.get("sha256")
    checks["evidence_file"] = {
        "path": None if evidence_path is None else str(evidence_path),
        "exists": bool(evidence_path and Path(evidence_path).is_file()),
        "sha256": None,
        "expected_sha256": expected_sha,
        "match": False,
    }
    if evidence_path and Path(evidence_path).is_file():
        actual = sha256_file(Path(evidence_path))
        checks["evidence_file"]["sha256"] = actual
        checks["evidence_file"]["match"] = bool(expected_sha) and actual == expected_sha
        if not checks["evidence_file"]["match"]:
            errors.append("evidence_file_hash_mismatch")
    else:
        errors.append("evidence_file_missing")

    return {"ok": not errors, "errors": errors, "checks": checks}


def validate_legacy_review(candidate: dict, ledger_row: dict, evidence_path: Path | None) -> dict:
    """Old three-case evidence rule: key row and evidence file only."""
    errors = []
    checks = {
        "ledger_key": {
            "candidate": candidate.get("key"),
            "ledger": ledger_row.get("key"),
            "match": bool(candidate.get("key")) and candidate.get("key") == ledger_row.get("key"),
        },
        "evidence_file": {
            "path": None if evidence_path is None else str(evidence_path),
            "exists": bool(evidence_path and Path(evidence_path).is_file()),
        },
    }
    if not checks["ledger_key"]["match"]:
        errors.append("ledger_key_mismatch")
    if not checks["evidence_file"]["exists"]:
        errors.append("evidence_file_missing")
    return {"ok": not errors, "errors": errors, "checks": checks}
