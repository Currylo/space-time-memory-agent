#!/usr/bin/env python3
"""A2: verify the Loitering feature table, scene folds and metric tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FEATURES = PROJECT_DIR / "data/a2/loitering/trajectory_features.csv"
DEFAULT_AUDIT = PROJECT_DIR / "data/a2/loitering/dataset_audit.json"
DEFAULT_METRICS = PROJECT_DIR / "reports/a2/loitering/metrics.json"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/A2-Loitering核验.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify A2 Loitering outputs")
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.features = args.features.resolve()
    args.audit = args.audit.resolve()
    args.metrics = args.metrics.resolve()
    args.output = args.output.resolve()

    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    features = pd.read_csv(args.features)
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))

    check("audit rows 874936", audit["rows"] == 874936, audit["rows"])
    check("audit trajectories 19737", audit["trajectories"] == 19737, audit["trajectories"])
    check("audit clips 1256", audit["clips"] == 1256, audit["clips"])
    check("audit folders 31", audit["folders"] == 31, audit["folders"])
    check("label positives 3860", audit["label_counts"]["1"] == 3860, audit["label_counts"])
    check("label negatives 15877", audit["label_counts"]["0"] == 15877, audit["label_counts"])
    check("official train 15789", audit["official_split"]["train_trajectories"] == 15789, audit["official_split"]["train_trajectories"])
    check("official test 3948", audit["official_split"]["test_trajectories"] == 3948, audit["official_split"]["test_trajectories"])

    check("feature rows 19737", len(features) == 19737, len(features))
    check("feature unique track ids", features["track_id"].nunique() == len(features), features["track_id"].nunique())
    check("feature labels match", int(features["label"].sum()) == 3860, int(features["label"].sum()))
    check("no missing track/folder", features[["track_id", "folder", "clip"]].notna().all().all(), None)
    numeric = features.select_dtypes(include=[np.number]).columns.tolist()
    non_finite = {column: int(np.count_nonzero(~np.isfinite(features[column].to_numpy(dtype=float)))) for column in numeric}
    check("all numeric features finite", all(value == 0 for value in non_finite.values()), {k: v for k, v in non_finite.items() if v})

    folds = metrics["fold_assignment"]
    check("five scene folds", len(folds) == 5, len(folds))
    test_folders = []
    for fold in folds:
        test_folders.extend(fold["test_folders"])
        check(
            f"fold {fold['fold']} no folder overlap",
            not (set(fold["train_folders"]) & set(fold["test_folders"])),
            None,
        )
    check("test folders cover all 31 once", sorted(test_folders) == sorted(set(test_folders)) and len(test_folders) == 31, len(test_folders))

    methods = metrics["primary_metrics"]
    check("primary methods >= 20", len(methods) >= 20, len(methods))
    probabilities_ok = True
    confusion_ok = True
    for row in methods:
        for key in ("precision", "recall", "f1"):
            if not (0.0 <= row[key] <= 1.0):
                probabilities_ok = False
        if row["tp"] + row["fp"] + row["fn"] + row["tn"] != row["n"]:
            confusion_ok = False
    check("metrics in [0,1]", probabilities_ok, None)
    check("confusion sums equal n", confusion_ok, None)
    check("random forest present", any(row["method"] == "random_forest" for row in methods), None)
    check("bbox rule present", any(row["method"] == "bbox_area_200" for row in methods), None)

    for name in ("rule_predictions_scene_cv.csv", "supervised_predictions_scene_cv.csv", "methods_pooled.csv", "feature_distributions.png"):
        path = args.metrics.parent / name if name.endswith(".png") is False else args.metrics.parent / "figures" / name
        check(f"output exists: {name}", path.is_file(), str(path))

    generalization_path = args.metrics.parent / "generalization.json"
    check("generalization output exists", generalization_path.is_file(), str(generalization_path))
    if generalization_path.is_file():
        generalization = json.loads(generalization_path.read_text(encoding="utf-8"))
        check("leave-one-folder-out folds 31", generalization["leave_one_folder_out"]["folds"] == 31, generalization["leave_one_folder_out"]["folds"])
        check("ablation rows 8", len(generalization["feature_ablation"]) == 8, len(generalization["feature_ablation"]))
        check("paired t-test p in [0,1]", 0 <= generalization["paired_rf_vs_lr"]["ttest_p"] <= 1, generalization["paired_rf_vs_lr"]["ttest_p"])
        check("RF LOO F1 in [0,1]", 0 <= generalization["leave_one_folder_out"]["rf_f1_mean"] <= 1, generalization["leave_one_folder_out"]["rf_f1_mean"])

    result = {
        "schema": "a2-loitering-verification-v1",
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
