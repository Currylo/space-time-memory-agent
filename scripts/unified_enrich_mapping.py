#!/usr/bin/env python3
"""Enrich the evidence mapping with the actual injection-time clock record.

The injection scheduler and the evidence reconciliation are separate phases.
This small post-processing step joins the scheduler's stdout log (candidate id,
lateness, clock source) to the persisted event/mapping records.  It does not
touch the ledger, the platform or any clip.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_memory.unified_platform import event_request_plan  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    args = parser.parse_args()
    round_dir = args.round_dir.resolve()
    mapping_path = round_dir / "candidate_event_mapping.jsonl"
    log_path = round_dir / "platform" / "injection_stdout.log"
    records = [json.loads(line) for line in mapping_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    log_map = {}
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("submitted"):
                log_map[item["submitted"]] = item
    alignment = json.loads((round_dir / "alignment" / "clock_alignment.json").read_text(encoding="utf-8"))
    manifest = json.loads((round_dir / "replay_manifest.json").read_text(encoding="utf-8"))
    clock = alignment["final_recording_fit"]
    bias = float(manifest.get("schedule_bias_seconds", 0.0))
    enriched = []
    submission_log = []
    for record in records:
        candidate = {
            "condition_met_video_time": record["condition_met_video_time"],
            "evidence_start": record["evidence_start"],
            "evidence_end": record["evidence_end"],
        }
        plan = event_request_plan(candidate)
        log = log_map.get(record["candidate_id"], {})
        event_start = (record.get("platform") or {}).get("event_start_epoch")
        actual_post = (
            float(event_start) + plan["pre_capture"] if event_start is not None else None
        )
        lateness = log.get("late")
        target = actual_post - lateness if (actual_post is not None and lateness is not None) else None
        inferred_t0 = (
            target - bias - record["condition_met_video_time"]
            if target is not None else None
        )
        submission = {
            "emit_mode": "offline_replay_injection",
            "target_emit_epoch": target,
            "actual_post_epoch": actual_post,
            "lateness_seconds": lateness,
            "clock_model_source_at_submit": log.get("model"),
            "clock_model_samples_at_submit": log.get("samples"),
            "inferred_t0_at_submit_epoch": inferred_t0,
            "final_t0_epoch": clock["t0_epoch"],
            "mapping_residual_at_submit_seconds": (
                None if inferred_t0 is None else inferred_t0 - clock["t0_epoch"]
            ),
            "state": log.get("state", record.get("submit_state")),
            "note": (
                "Pre-computed offline candidate injected at its mapped source time; "
                "this is not proof of real-time inference or end-to-end alarm latency."
            ),
        }
        record["request_plan"] = plan
        record["submission"] = submission
        record["clock_model_final"] = {
            "t0_epoch": clock["t0_epoch"],
            "drift_source_seconds_per_platform_second": clock["drift_source_seconds_per_platform_second"],
            "samples": clock["samples"],
            "rmse_seconds": clock["rmse_seconds"],
            "max_abs_residual_seconds": clock["max_abs_residual_seconds"],
        }
        enriched.append(record)
        submission_log.append({
            "candidate_id": record["candidate_id"],
            "kind": record["kind"],
            "ledger_key": record["ledger_key"],
            "event_id": record.get("event_id"),
            **submission,
        })
    with mapping_path.open("w", encoding="utf-8") as handle:
        for record in enriched:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (round_dir / "platform" / "submission_log.jsonl").open("w", encoding="utf-8") as handle:
        for record in submission_log:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    # Update the page dataset in place with the same fields.
    page_path = ROOT / "demo" / "data" / "unified_platform.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    by_id = {record["candidate_id"]: record for record in enriched}
    for candidate in page.get("candidates") or []:
        source = by_id.get(candidate["candidate_id"])
        if source:
            candidate["request_plan"] = source["request_plan"]
            candidate["submission"] = source["submission"]
            candidate["clock_model_final"] = source["clock_model_final"]
    lateness = [item["lateness_seconds"] for item in submission_log if item.get("lateness_seconds") is not None]
    page.setdefault("summary", {})["submission_lateness_max_abs_seconds"] = max((abs(v) for v in lateness), default=None)
    page["summary"]["submission_modes"] = {"offline_replay_injection": len(submission_log)}
    page_path.write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (round_dir / "platform" / "page_dataset_snapshot.json").write_text(
        json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "records": len(enriched),
        "log_records": len(log_map),
        "lateness_max_abs_seconds": max((abs(v) for v in lateness), default=None),
        "target_example": enriched[0]["submission"] if enriched else None,
        "enriched_at_utc": utc_now(),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
