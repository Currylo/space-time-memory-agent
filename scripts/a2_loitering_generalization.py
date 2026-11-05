#!/usr/bin/env python3
"""A2/P2: Loitering generalization, per-scene errors, feature ablation.

Adds to the main Loitering experiment:
* five-fold mean/std for the supervised references;
* per-scene confusion matrices;
* feature-group ablation;
* leave-one-folder-out (train on 30 scenes, test on 1) for RF and the fixed
  area rule;
* a paired test of RF vs LR across folds.

All protocols group by source date folder; no scene leakage.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from a2_loitering_experiment import NUMERIC_FEATURES, metrics, tune_threshold  # noqa: E402
from st_memory.a2_loitering import apply_composite_rule  # noqa: E402

FEATURES = PROJECT_DIR / "data/a2/loitering/trajectory_features.csv"
REPORT = PROJECT_DIR / "reports/a2/loitering"
OUTPUT = REPORT / "generalization.json"

FEATURE_GROUPS = {
    "duration": ["n_points", "duration_seconds", "gap_seconds", "gap_ratio"],
    "motion": ["path_length_px", "net_displacement_px", "path_efficiency", "mean_speed_px_s", "step_median_px", "step_p95_px"],
    "spatial": ["radius_std_px", "radius_max_px", "radius_p95_px", "stay_ratio_5px", "bbox_area_px2", "hull_area_px2", "ellipse_area_px2", "rectangle_feature_area_px2"],
    "direction": ["direction_changes_25deg", "reversals_120deg", "self_intersections"],
    "stationary": ["no_motion_long", "no_motion_short_60s", "stationary_60s_r10"],
    "quality": ["occlusion_mean", "occlusion_max", "box_area_mean_px2"],
    "top_left": ["top_left_hull_area_px2", "top_left_bbox_area_px2", "top_left_rectangle_feature_area_px2"],
}


def rf_model(n_estimators: int = 300):
    from sklearn.ensemble import RandomForestClassifier

    return RandomForestClassifier(
        n_estimators=n_estimators,
        min_samples_leaf=5,
        max_features="sqrt",
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=42,
    )


def fit_predict_group_cv(frame: pd.DataFrame, columns: list[str], n_splits: int = 5) -> dict:
    from sklearn.model_selection import GroupKFold

    groups = frame["folder"].to_numpy()
    y = frame["label"].to_numpy(dtype=int)
    X = frame[columns].to_numpy(dtype=float)
    splitter = GroupKFold(n_splits=n_splits)
    predictions = []
    fold_f1 = []
    for train_index, test_index in splitter.split(X, y, groups):
        model = rf_model()
        model.fit(X[train_index], y[train_index])
        probability = model.predict_proba(X[test_index])[:, 1]
        prediction = (probability >= 0.5).astype(int)
        predictions.append((test_index, y[test_index], prediction, probability))
        fold_f1.append(metrics(y[test_index], prediction, probability)["f1"])
    y_true = np.concatenate([item[1] for item in predictions])
    y_pred = np.concatenate([item[2] for item in predictions])
    y_score = np.concatenate([item[3] for item in predictions])
    result = metrics(y_true, y_pred, y_score)
    result["fold_f1_mean"] = float(np.mean(fold_f1))
    result["fold_f1_std"] = float(np.std(fold_f1))
    return result


def main() -> int:
    frame = pd.read_csv(FEATURES)
    REPORT.mkdir(parents=True, exist_ok=True)

    # Five-fold mean/std from existing predictions.
    supervised = pd.read_csv(REPORT / "supervised_predictions_scene_cv.csv")
    fold_rows = []
    for method, group in supervised.groupby("method"):
        for fold, fold_group in group.groupby("fold"):
            result = metrics(fold_group["label"], fold_group["prediction"], fold_group["score"])
            fold_rows.append({"method": method, "fold": int(fold), **result})
    fold_table = pd.DataFrame(fold_rows)
    mean_std = (
        fold_table.groupby("method")
        .agg(f1_mean=("f1", "mean"), f1_std=("f1", "std"), precision_mean=("precision", "mean"), recall_mean=("recall", "mean"), pr_auc_mean=("pr_auc", "mean"))
        .reset_index()
        .to_dict(orient="records")
    )

    # Paired test between RF and LR across folds.
    rf = fold_table[fold_table["method"] == "random_forest"].sort_values("fold")["f1"].to_numpy()
    lr = fold_table[fold_table["method"] == "logistic_regression"].sort_values("fold")["f1"].to_numpy()
    from scipy.stats import ttest_rel, wilcoxon

    paired = {
        "rf_fold_f1": rf.tolist(),
        "lr_fold_f1": lr.tolist(),
        "diff_mean": float(np.mean(rf - lr)),
        "ttest_p": float(ttest_rel(rf, lr).pvalue),
        "wilcoxon_p": float(wilcoxon(rf, lr).pvalue),
    }

    # Per-scene confusion matrices for RF/LR.
    per_scene = []
    for (method, folder), group in supervised.groupby(["method", "folder"]):
        result = metrics(group["label"], group["prediction"], group["score"])
        per_scene.append({"method": method, "folder": int(folder), **result})
    per_scene_frame = pd.DataFrame(per_scene)
    per_scene_frame.to_csv(REPORT / "per_scene_confusion.csv", index=False)

    # Feature-group ablation.
    ablation = [{"removed_group": "none", **fit_predict_group_cv(frame, NUMERIC_FEATURES)}]
    for group_name, columns in FEATURE_GROUPS.items():
        kept = [column for column in NUMERIC_FEATURES if column not in columns]
        ablation.append({"removed_group": group_name, **fit_predict_group_cv(frame, kept)})
    pd.DataFrame(ablation).to_csv(REPORT / "feature_ablation.csv", index=False)

    # Leave-one-folder-out: 31 fits for RF and the area rule.
    from sklearn.model_selection import LeaveOneGroupOut

    groups = frame["folder"].to_numpy()
    y = frame["label"].to_numpy(dtype=int)
    X = frame[NUMERIC_FEATURES].to_numpy(dtype=float)
    logo = LeaveOneGroupOut()
    logo_rows = []
    for train_index, test_index in logo.split(X, y, groups):
        folder = int(groups[test_index][0])
        model = rf_model(n_estimators=250)
        model.fit(X[train_index], y[train_index])
        probability = model.predict_proba(X[test_index])[:, 1]
        prediction = (probability >= 0.5).astype(int)
        rf_result = metrics(y[test_index], prediction, probability)

        # Fixed area rule: area <= 200 px2; threshold is fixed, not tuned on
        # the held-out folder.
        test = frame.iloc[test_index]
        area_prediction = (test["bbox_area_px2"] <= 200.0).astype(int).to_numpy()
        area_result = metrics(y[test_index], area_prediction)

        logo_rows.append(
            {
                "folder": folder,
                "n": int(len(test_index)),
                "positives": int(y[test_index].sum()),
                "rf_f1": rf_result["f1"],
                "rf_precision": rf_result["precision"],
                "rf_recall": rf_result["recall"],
                "rf_pr_auc": rf_result["pr_auc"],
                "area_f1": area_result["f1"],
                "area_precision": area_result["precision"],
                "area_recall": area_result["recall"],
            }
        )
    logo_frame = pd.DataFrame(logo_rows)
    logo_frame.to_csv(REPORT / "leave_one_folder_out.csv", index=False)

    summary = {
        "schema": "a2-loitering-generalization-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "five_fold_mean_std": mean_std,
        "paired_rf_vs_lr": paired,
        "feature_ablation": ablation,
        "leave_one_folder_out": {
            "folds": len(logo_rows),
            "rf_f1_mean": float(logo_frame["rf_f1"].mean()),
            "rf_f1_std": float(logo_frame["rf_f1"].std()),
            "rf_f1_median": float(logo_frame["rf_f1"].median()),
            "rf_pr_auc_mean": float(logo_frame["rf_pr_auc"].mean()),
            "area_f1_mean": float(logo_frame["area_f1"].mean()),
            "area_f1_std": float(logo_frame["area_f1"].std()),
            "area_f1_median": float(logo_frame["area_f1"].median()),
        },
        "per_scene_confusion": str((REPORT / "per_scene_confusion.csv").relative_to(PROJECT_DIR)),
        "limitations": [
            "Leave-one-folder-out tests source-date generalization, not camera/site generalization.",
            "The area rule threshold is fixed at 200 px2 for the leave-one-folder-out run; it is not tuned on the held-out scene.",
            "RF/LR fold differences are small; a non-significant paired test must not be read as equivalence.",
        ],
    }
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "mean_std": mean_std,
        "paired": paired,
        "ablation": [{key: row[key] for key in ("removed_group", "f1", "pr_auc")} for row in ablation],
        "logo": summary["leave_one_folder_out"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
