#!/usr/bin/env python3
"""Create a hash-based change manifest for the same-source platform round."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entry(path: Path, note: str = "") -> dict:
    return {
        "path": str(path.relative_to(ROOT)) if path.is_absolute() and str(path).startswith(str(ROOT)) else str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": sha256(path),
        "note": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    args = parser.parse_args()
    round_dir = args.round_dir.resolve()
    code = [
        "src/st_memory/unified_platform.py",
        "src/st_memory/a2_adapter.py",
        "src/st_memory/unified_review_guard.py",
        "scripts/unified_round_prepare.py",
        "scripts/unified_config.py",
        "scripts/unified_replay_round.py",
        "scripts/unified_evidence_round.py",
        "scripts/unified_enrich_mapping.py",
        "scripts/unified_idempotency_check.py",
        "scripts/unified_prepare_review_test.py",
        "scripts/unified_representative_alignment.py",
        "scripts/unified_make_browser_original.py",
        "scripts/unified_browser_acceptance.py",
        "scripts/unified_change_manifest.py",
        "scripts/render_unified_report.py",
        "scripts/unified_r1_r2_correction.py",
        "scripts/unified_attach_correction_metadata.py",
        "scripts/unified_review_guard_check.py",
        "scripts/unified_f1_f2_summary.py",
        "tests/test_unified_platform.py",
        "tests/test_unified_review_guard.py",
        "tests/test_live_alignment_runtime.py",
    ]
    demo = [
        "demo/showcase.html",
        "demo/unified.html",
        "demo/assets/unified.js",
        "demo/assets/unified.css",
        "demo/serve.py",
        "demo/data/unified.json",
        "demo/data/unified_platform.json",
        "demo/data/unified_platform.test.json",
        "demo/tests/verify_showcase.py",
        "demo/README.md",
        "README.md",
        "reports/同源平台闭环执行报告.md",
        "reports/同源平台闭环执行报告.html",
        "reports/同源平台闭环修正报告-R1R3.md",
        "reports/同源平台闭环修正报告-R1R3.html",
        "reports/同源平台闭环复审修正-F1F2.md",
        "reports/同源平台闭环复审修正-F1F2.html",
    ]
    platform = [
        "deploy/frigate/runtime/config/config.yml",
        "deploy/frigate/runtime/input/a2_unified_mot20_05_h264.mp4",
        "data/a2/adapter/ledger.sqlite",
        "data/a2/adapter/ledger.test.sqlite",
        "demo/media/unified/original_h264.mp4",
        "demo/media/unified/analysis.mp4",
        "data/a2/unified/MOT20-05-raw.mp4",
    ]
    backups = [
        "backups/config.yml.pre-unified",
        "backups/ledger.sqlite",
        "backups/frigate.db",
        "backups/demo/showcase.html",
        "backups/demo/unified.js",
        "backups/demo/unified.css",
        "backups/demo/serve.py",
        "backups/demo/unified.json",
        "backups/demo/review_candidates.json",
    ]
    evidence = [
        "input/input_manifest.json",
        "snapshots/prechange_snapshot.json",
        "replay_manifest.json",
        "alignment/calibration_alignment.json",
        "alignment/clock_alignment.json",
        "alignment/representative_alignment.json",
        "candidate_event_mapping.jsonl",
        "evidence_manifest.json",
        "platform/calibration.json",
        "platform/calibration_attempt_notes.json",
        "platform/config_add_disabled.json",
        "platform/camera_main-enable.json",
        "platform/camera_main-disable.json",
        "platform/config.diff",
        "platform/config_semantic_diff.json",
        "platform/evidence_summary.json",
        "platform/idempotency_and_recovery.json",
        "platform/no_trigger_window.json",
        "platform/post_round_snapshot.json",
        "platform/final_state_check.txt",
        "platform/ssh_access_check.txt",
        "platform/submission_log.jsonl",
        "platform/injection_stdout.log",
        "browser/browser_acceptance.json",
        "browser/console.log",
        "browser/pageerrors.log",
        "browser/unified-acceptance.webm",
        "browser/13-tour.png",
        "browser/test-review-log.jsonl",
        "browser/test-setup.json",
        "browser/old-showcase/browser-check.json",
        "browser/05b-corrected-stay.png",
        "browser/06b-platform-player.png",
        "corrections/stay_windows_v2.json",
        "corrections/clock_semantics_v2.json",
        "corrections/correction_summary.json",
        "corrections/f1_f2_summary.json",
        "corrections/review_guard/backend_review_guard_v2.json",
        "corrections/review_guard/valid.json",
        "corrections/review_guard/clock_needs_review.json",
        "corrections/review_guard/evidence_partial.json",
        "corrections/review_guard/clip_missing.json",
        "corrections/review_guard/identity_mismatch.json",
        "corrections/review_guard/legacy_valid.json",
    ]
    manifest = {
        "schema": "unified-platform-change-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "round_id": round_dir.name,
        "note": "No Git repository in this directory; the manifest lists files touched by this round and their SHA256. Backups are in the round directory.",
        "code": [entry(ROOT / path) for path in code],
        "demo_and_docs": [entry(ROOT / path) for path in demo],
        "platform_and_inputs": [entry(ROOT / path) for path in platform],
        "backups": [entry(round_dir / path) for path in backups],
        "evidence": [entry(round_dir / path) for path in evidence],
        "test_logs": [
            entry(ROOT / "reports/unified/unit-tests.txt"),
            entry(ROOT / "reports/unified/unit-tests-full-gpu.txt"),
            entry(ROOT / "reports/unified/unit-tests-full-venv.txt"),
            entry(ROOT / "reports/unified/unit-tests-final-gpu.txt"),
            entry(ROOT / "reports/unified/http-check-unified.json"),
            entry(ROOT / "reports/unified/unit-tests-r1r3.txt"),
            entry(ROOT / "reports/unified/unit-tests-r1r3-venv.txt"),
            entry(ROOT / "reports/unified/unit-tests-f1f2.txt"),
            entry(ROOT / "reports/unified/unit-tests-f1f2-venv.txt"),
        ],
    }
    output = round_dir / "change_manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "code": len(code), "demo": len(demo), "platform": len(platform), "backups": len(backups), "evidence": len(evidence)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
