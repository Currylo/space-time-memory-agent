#!/usr/bin/env python3
"""Attach R1/R2 correction metadata and guard pointers to page/evidence data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-dir", type=Path, required=True)
    args = parser.parse_args()
    round_dir = args.round_dir.resolve()
    summary = read(round_dir / "corrections" / "correction_summary.json")
    stay = read(round_dir / "corrections" / "stay_windows_v2.json")
    clock = read(round_dir / "corrections" / "clock_semantics_v2.json")
    guard_path = round_dir / "corrections" / "review_guard" / "backend_review_guard_v2.json"
    guard = read(guard_path) if guard_path.is_file() else {}
    correction = {
        "version": "r1-r2-r3-v2",
        "r1": {
            "version": "stay-window-v2-causal",
            "changed_starts": summary["r1_changed_starts"],
            "changed_ends": summary["r1_changed_ends"],
            "all_condition_ok": summary["r1_all_condition_ok"],
            "all_coverage_verified": summary["r1_all_coverage_verified"],
            "table": "corrections/stay_windows_v2.json",
        },
        "r2": {
            "version": "clock-endpoints-v2",
            "old_t0_epoch": summary["r2_old_t0"],
            "new_t0_epoch": summary["r2_new_t0"],
            "boundary_correction_seconds": summary["r2_boundary_correction_seconds"],
            "final_samples": summary["r2_final_samples"],
            "frame_period_seconds": summary["r2_frame_period_seconds"],
            "holdout_numeric_max_abs_residual_seconds": summary["r2_holdout_numeric_max_abs_residual_seconds"],
            "physical_clock_accuracy_measured": False,
            "table": "corrections/clock_semantics_v2.json",
        },
        "r3": {
            "backend_guard": "server-side clock/evidence/identity/hash checks in demo/serve.py",
            "guard_module": "src/st_memory/unified_review_guard.py",
            "negative_positive_check": "corrections/review_guard/backend_review_guard_v2.json",
            "production_reviews_unchanged": guard.get("production_ledger_reviews"),
            "passed": guard.get("passed"),
        },
    }
    page_path = ROOT / "demo" / "data" / "unified_platform.json"
    page = read(page_path)
    page["corrections"] = correction
    page.setdefault("summary", {})["corrections"] = correction
    page_path.write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (round_dir / "platform" / "page_dataset_snapshot.json").write_text(
        json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    evidence_path = round_dir / "evidence_manifest.json"
    evidence = read(evidence_path)
    evidence["corrections"] = correction
    evidence["corrections"]["records"] = {
        "stay_windows": len(stay.get("stay_candidates", [])),
        "clock_anchors": len(clock.get("anchors", [])),
        "guard_results": len(guard.get("results", [])),
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"page": str(page_path.relative_to(ROOT)), "evidence": str(evidence_path.relative_to(ROOT)), "r1": correction["r1"], "r2": {k: correction["r2"][k] for k in ("new_t0_epoch", "boundary_correction_seconds", "final_samples", "frame_period_seconds")}, "r3": correction["r3"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
