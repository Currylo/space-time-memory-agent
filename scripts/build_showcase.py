#!/usr/bin/env python3
"""Build the local showcase snapshot from completed evaluations; no inference."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "tracking": "reports/a2/mot17/metrics.json",
    "loitering": "reports/a2/loitering/metrics.json",
    "fall": "reports/a2/gmd/metrics.json",
    "comparison": "reports/a2/platform-compare-20261104T175324-v2/comparison.json",
    "rules": "demo/data/rules.json",
    "gate": "reports/a2/loitering-gate-20261104T181925-final/gate_verification.json",
    "bridge": "reports/a2/external-bridge-20261104T182728/bridge_result.json",
    "roi": "reports/a2/roi-cases-v2-20261104T190602/roi_cases_v2.json",
    "cumulative": "reports/a2/platform-compare-20261104T175324-v2/cumulative/cumulative_comparison.json",
    "adapter": "reports/a2/adapter/status.json",
    "adapter_duplicate": "reports/a2/adapter-idempotency/submit-duplicate.json",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default=None, help="ffmpeg executable for browser-compatible MP4 remux")
    args = parser.parse_args()
    values, sources = {}, {}
    for key, relative in SOURCES.items():
        raw = (ROOT / relative).read_bytes()
        values[key] = json.loads(raw)
        sources[key] = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
    mot, loit, fall, comp = (values[k] for k in ("tracking", "loitering", "fall", "comparison"))
    rf = next(m for m in loit["primary_metrics"] if m["method"] == "random_forest")
    bridge = values["bridge"]
    adapter = values["adapter"]["candidate"]
    duplicate = values["adapter_duplicate"]
    event_id = bridge["event_id"]
    gate = values["gate"]["selected"]
    media = {}
    for key, relative, filename in (
        ("clip", "reports/a2/external-bridge-20261104T182728/clip.mp4", "external-candidate-clip.mp4"),
        ("closure", "reports/a2/case-closure-20261104T183702/case-closure.webm", "case-closure.webm"),
        ("roi_sheet", "reports/a2/roi-cases-v2-20261104T190602/roi-cases-v2-contact-sheet.png", "roi-cases.png"),
    ):
        source = ROOT / relative
        raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if key == "clip" and raw_hash != bridge["clip"]["sha256"]:
            raise ValueError("External clip hash differs from bridge_result.json")
        target = ROOT / "demo/media/showcase" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != raw_hash:
            shutil.copyfile(source, target)
        if key == "clip":
            ffmpeg = args.ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
            browser_target = target.with_name("external-candidate-browser.mp4")
            temporary = target.with_name("external-candidate-browser.tmp.mp4")
            subprocess.run([str(ffmpeg), "-v", "error", "-i", str(source), "-map", "0:v:0",
                            "-c", "copy", "-movflags", "+faststart", "-y", str(temporary)], check=True)
            temporary.replace(browser_target)
            target = browser_target
        media[key] = {"url": f"/media/showcase/{target.name}", "source": relative,
                      "source_sha256": raw_hash, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "transform": "container_remux_no_video_reencoding" if key == "clip" else "byte_identical_copy"}
    snapshot = {
        "schema": "leadership-showcase-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "completed_offline_results_with_single_external_event_case",
        "benchmarks": {
            "tracking": {"sequences": mot["sequence_count"], "hota": mot["combined"]["HOTA"],
                         "idf1": mot["combined"]["IDF1"], "mota": mot["combined"]["MOTA"]},
            "loitering": {"f1": rf["f1"], "protocol": loit["primary_protocol"], "level": "trajectory"},
            "fall": {"videos": fall["videos_evaluated"],
                     "development_event_f1": fall["fixed_dev"]["event_level"]["f1"],
                     "test_event_f1": fall["fixed_test"]["event_level"]["f1"],
                     "all_event_f1": fall["fixed_all"]["event_level"]["f1"],
                     "temporal_iou": 0.3},
        },
        "comparison": {k: comp[k] for k in (
            "video_duration_seconds", "sample_fps", "dwell_seconds", "platform_zone_events_total",
            "platform_zone_events_ge_dwell", "self_long_stay_events", "temporal_matches",
            "platform_only", "self_only", "match_overlap_seconds")},
        "cumulative_comparison": {k: values["cumulative"][k] for k in (
            "self_cumulative_events", "platform_zone_events", "one_to_one_matches", "platform_only", "self_only")},
        "platform": {
            "review_url": "http://127.0.0.1:15000/review?timestamp=a1_public_demo_1793785420",
            "home_url": "http://127.0.0.1:15000/",
            "acceptance_path": "reports/a1/frigate/replay-acceptance-20261104T094800Z/acceptance.json",
            "integration_status": "single_external_event_verified_batch_and_human_review_pending",
            "event_url": f"http://127.0.0.1:15000/explore?event_id={event_id}",
            "gate": {k: gate[k] for k in ("event_id", "gate_seconds", "entry_time_local", "event_alert_first_seen_local")},
            "gate_semantics": "cumulative_in_zone_detection_frames_not_strict_continuous",
            "external_case": {
                "event_id": event_id, "candidate": bridge["candidate"],
                "has_clip": bridge["event_after_end"]["body"]["has_clip"],
                "clip_sha256": bridge["clip"]["sha256"],
                "review_record_write_status": bridge["manual_review"]["status"],
                "review_status": adapter["review_status"],
                "review_provenance": "legacy_description_confirmed_is_script_written; adapter default is pending_review until an operator submits",
                "dedup_status": "persistent_sqlite_ledger_duplicate_skip_verified",
                "duplicate_submit_state": duplicate["state"],
                "ledger_key": duplicate["key"],
            },
        },
        "roi": {
            "roi_id": values["roi"]["roi_id"],
            "source_scope": values["roi"]["source_video_scope"],
            "timing_method": values["roi"]["timing_method"],
            "cases": [{k: c[k] for k in ("type", "track_id", "observed_dwell_seconds", "gap_seconds", "threshold_seconds", "review_status")}
                      for c in values["roi"]["cases"]],
            "review_status": "pending_review",
        },
        "media": media,
        "sources": sources,
    }
    target = ROOT / "demo/data/showcase.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Written {target.relative_to(ROOT)} from {len(sources)} existing result files")


if __name__ == "__main__":
    main()
