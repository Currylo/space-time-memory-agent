#!/usr/bin/env python3
"""A2/P0: verify GMDCSA-24 data, pose features and fall metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA = PROJECT_DIR / "data/a2/gmd_csa24"
DEFAULT_METRICS = PROJECT_DIR / "reports/a2/gmd/metrics.json"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/A2-GMDCSA24核验.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify GMDCSA-24 fall experiment")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.data = args.data.resolve()
    args.metrics = args.metrics.resolve()
    args.output = args.output.resolve()

    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    audit = json.loads((args.data / "dataset_audit.json").read_text(encoding="utf-8"))
    events = pd.read_csv(args.data / "events.csv")
    features = pd.read_csv(args.data / "pose_features.csv")
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    safer = json.loads((args.data / "safer_access_assessment.json").read_text(encoding="utf-8"))

    check("videos total 160", audit["videos_total"] == 160, audit["videos_total"])
    check("folder falls 79", audit["folder_fall_count"] == 79, audit["folder_fall_count"])
    check("folder ADL 81", audit["folder_adl_count"] == 81, audit["folder_adl_count"])
    check("fall intervals 78", audit["fall_intervals_total"] == 78, audit["fall_intervals_total"])
    check("no missing videos", len(audit["missing_videos"]) == 0, len(audit["missing_videos"]))
    check("no folder-label mismatch", len(audit["folder_label_mismatches"]) == 0, len(audit["folder_label_mismatches"]))
    check("one parse note", len(audit["parse_notes"]) == 1, len(audit["parse_notes"]))

    check("events rows 160", len(events) == 160, len(events))
    check("events unique ids", events["video_id"].nunique() == 160, events["video_id"].nunique())
    check("fall label count 79", int(events["has_fall"].sum()) == 79, int(events["has_fall"].sum()))
    check("fall videos have label", bool((events[events["kind"] == "Fall"]["has_fall"]).all()), None)
    check("ADL videos have no fall label", bool((~events[events["kind"] == "ADL"]["has_fall"]).all()), None)

    check("pose features non-empty", len(features) > 0, len(features))
    check("pose features 160 videos", features["video_id"].nunique() == 160, features["video_id"].nunique())
    key_columns = ["aspect", "torso_angle", "center_y", "bottom_y", "pose_conf", "time"]
    non_finite = {column: int(np.count_nonzero(~np.isfinite(features[column].to_numpy(dtype=float)))) for column in key_columns}
    check("pose features finite", all(value == 0 for value in non_finite.values()), non_finite)

    check("metrics evaluated 160", metrics["videos_evaluated"] == 160, metrics["videos_evaluated"])
    test = metrics["fixed_test"]["video_level"]
    check("test n 80", test["n"] == 80, test["n"])
    check("test positives 38", test["positives"] == 38, test["positives"])
    check("test confusion sums to n", test["tp"] + test["fp"] + test["fn"] + test["tn"] == test["n"], None)
    event = metrics["fixed_test"]["event_level"]
    check("test event TP+FN = 37 fall intervals", event["tp"] + event["fn"] == 37, event["tp"] + event["fn"])
    check("test event F1 in [0,1]", 0 <= event["f1"] <= 1, event["f1"])
    check("all video F1 in [0,1]", 0 <= metrics["fixed_all"]["video_level"]["f1"] <= 1, metrics["fixed_all"]["video_level"]["f1"])
    type_total = sum(item["videos"] for item in metrics["per_fall_type_fixed"])
    check("per-type videos sum to 79", type_total == 79, type_total)

    check("SAFER gated manual", safer["gated"] == "manual", safer["gated"])
    check("SAFER CC BY-NC-SA 4.0", any("cc-by-nc-sa-4.0" in tag for tag in safer["license_tags"]), safer["license_tags"])
    check("SAFER total > 150 GiB", safer["total_gib"] > 150, safer["total_gib"])

    for relative in (
        "reports/A2-GMDCSA24摔倒事件验证报告.md",
        "reports/a2/gmd/grid.csv",
        "reports/a2/gmd/predictions_fixed.csv",
    ):
        check(f"output exists: {relative}", (PROJECT_DIR / relative).is_file(), relative)

    result = {
        "schema": "a2-gmd-csa24-verification-v1",
        "checks_total": len(checks),
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_failed": sum(1 for item in checks if not item["passed"]),
        "checks": checks,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("checks_total", "checks_passed", "checks_failed")}, ensure_ascii=False, indent=2))
    failed = [item for item in checks if not item["passed"]]
    if failed:
        print(json.dumps(failed, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
