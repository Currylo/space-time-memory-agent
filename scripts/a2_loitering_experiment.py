#!/usr/bin/env python3
"""A2: RS-WACV24 Loitering trajectory-level experiment.

What this script does
---------------------
1. Audits the released annotation tables and reproduces the repository's
   official random-by-id train/test split as a reference.
2. Extracts trajectory-level features (dwell, speed, path efficiency, area,
   direction changes, reversals, stationary windows, self-intersections).
3. Evaluates fixed and threshold-tuned rule baselines with a scene-grouped
   5-fold split (groups = source date folder), so no scene appears in both
   train and test.
4. Trains Logistic Regression and Random Forest references on the same
   scene-grouped folds, to show how much signal is in the feature set.
5. Writes metrics, predictions, per-fold/per-folder tables and figures.

The label is trajectory-level binary `loitering` (0/1).  The release does not
provide event start/end times, so no event-localization metric is computed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from st_memory.a2_loitering import (  # noqa: E402
    RULES,
    Trajectory,
    apply_composite_rule,
    extract_features,
)

DATASET_DIR = PROJECT_DIR / "upstream" / "RS-WACV24_Loitering" / "dataset"
DEFAULT_OUTPUT = PROJECT_DIR / "data" / "a2" / "loitering"
DEFAULT_REPORT = PROJECT_DIR / "reports" / "a2" / "loitering"

NUMERIC_FEATURES = [
    "n_points",
    "duration_seconds",
    "gap_seconds",
    "gap_ratio",
    "path_length_px",
    "net_displacement_px",
    "path_efficiency",
    "mean_speed_px_s",
    "step_median_px",
    "step_p95_px",
    "radius_std_px",
    "radius_max_px",
    "radius_p95_px",
    "stay_ratio_5px",
    "direction_changes_25deg",
    "reversals_120deg",
    "self_intersections",
    "bbox_area_px2",
    "hull_area_px2",
    "ellipse_area_px2",
    "rectangle_feature_area_px2",
    "no_motion_long",
    "no_motion_short_60s",
    "stationary_60s_r10",
    "occlusion_mean",
    "occlusion_max",
    "box_area_mean_px2",
    "top_left_hull_area_px2",
    "top_left_bbox_area_px2",
    "top_left_rectangle_feature_area_px2",
]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def metrics(y_true, y_pred, score=None) -> dict:
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y_true = np.asarray(y_true).astype(int)
    y_pred = np.asarray(y_pred).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    result = {
        "n": int(len(y_true)),
        "positives": int(y_true.sum()),
        "negatives": int((y_true == 0).sum()),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
    }
    if score is not None and len(np.unique(y_true)) == 2:
        score = np.asarray(score, dtype=float)
        result["pr_auc"] = float(average_precision_score(y_true, score))
        result["roc_auc"] = float(roc_auc_score(y_true, score))
    else:
        result["pr_auc"] = None
        result["roc_auc"] = None
    return result


def load_trajectories(dataset_dir: Path) -> tuple[pd.DataFrame, list[Trajectory], dict]:
    annotations_path = dataset_dir / "annotations_loitering.csv"
    gt_path = dataset_dir / "gt_df.csv"
    train_path = dataset_dir / "train_df.csv"
    test_path = dataset_dir / "test_df.csv"

    annotations = pd.read_csv(annotations_path)
    gt = pd.read_csv(gt_path)
    train = pd.read_csv(train_path)
    test = pd.read_csv(test_path)

    required = {"folder", "clip", "frame", "id", "class", "x", "y", "width", "height", "occlusion", "loitering"}
    missing = required - set(annotations.columns)
    if missing:
        raise SystemExit(f"annotation columns missing: {sorted(missing)}")
    if annotations.duplicated(["id", "frame"]).any():
        raise SystemExit("duplicate (id, frame) rows in annotations")
    if annotations.groupby("id")["loitering"].nunique().max() > 1:
        raise SystemExit("inconsistent loitering label within an id")
    if set(annotations["id"]) != set(gt["id"]):
        raise SystemExit("annotation ids and gt_df ids differ")
    if len(set(train["id"]) & set(test["id"])) != 0:
        raise SystemExit("official train/test id overlap")
    if len(train) + len(test) != len(gt):
        raise SystemExit("official train+test does not cover gt_df")

    annotations = annotations.sort_values(["id", "frame"]).reset_index(drop=True)
    trajectories: list[Trajectory] = []
    for track_id, group in annotations.groupby("id", sort=False):
        group = group.sort_values("frame")
        trajectories.append(
            Trajectory(
                track_id=int(track_id),
                folder=int(group["folder"].iloc[0]),
                clip=str(group["clip"].iloc[0]),
                label=int(group["loitering"].max()),
                frames=group["frame"].to_numpy(dtype=int),
                x=group["x"].to_numpy(dtype=float),
                y=group["y"].to_numpy(dtype=float),
                width=group["width"].to_numpy(dtype=float),
                height=group["height"].to_numpy(dtype=float),
                occlusion=group["occlusion"].to_numpy(dtype=float),
            )
        )

    audit = {
        "schema": "a2-loitering-dataset-audit-v1",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "repo": "https://github.com/johnnynunez/RS-WACV24_Loitering",
            "license": "MIT (repository LICENSE)",
            "annotations_sha256": _sha256(annotations_path),
            "gt_sha256": _sha256(gt_path),
            "train_sha256": _sha256(train_path),
            "test_sha256": _sha256(test_path),
        },
        "rows": int(len(annotations)),
        "trajectories": int(annotations["id"].nunique()),
        "clips": int(annotations.groupby(["folder", "clip"]).ngroups),
        "clip_names": int(annotations["clip"].nunique()),
        "clip_note": "clips are folder/clip combinations; the same clip name can recur on different dates",
        "folders": int(annotations["folder"].nunique()),
        "label_counts": {str(k): int(v) for k, v in gt["loitering"].value_counts().sort_index().items()},
        "official_split": {
            "train_trajectories": int(len(train)),
            "test_trajectories": int(len(test)),
            "train_label_counts": {str(k): int(v) for k, v in train["loitering"].value_counts().sort_index().items()},
            "test_label_counts": {str(k): int(v) for k, v in test["loitering"].value_counts().sort_index().items()},
            "split_unit": "trajectory id, random/stratified (repository release)",
            "scene_grouped": False,
            "leakage_note": "Same source folder/clip can appear in both official train and test; this split is reported only as a reference.",
        },
        "frame_semantics": {
            "annotation_fps": 1,
            "note": "extract_LTD.py uses every=25 on 25 fps clips; released frame indices are approximately seconds, max 120 per 2-minute clip.",
        },
        "label_semantics": {
            "level": "trajectory",
            "start_end_times": False,
            "note": "Binary loitering label per track id; the release does not provide event start/end labels.",
        },
        "coordinate_note": {
            "primary": "bbox centre (x + width/2, y + height/2)",
            "paper_style": "released top-left x/y retained for rectangle/hull/ellipse methods",
            "ipm": "released x_transform/y_transform not used; ranges include extreme outliers up to 2.0e6 / 1.9e7",
        },
        "folder_label_table": [
            {"folder": int(folder), "trajectories": int(len(group)), "positives": int(group["label"].sum())}
            for folder, group in pd.DataFrame(
                [{"folder": t.folder, "label": t.label} for t in trajectories]
            ).groupby("folder")
        ],
    }
    return annotations, trajectories, audit


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_features(trajectories: list[Trajectory], output: Path) -> pd.DataFrame:
    rows = []
    started = time.monotonic()
    for index, trajectory in enumerate(trajectories, 1):
        rows.append(extract_features(trajectory))
        if index % 2000 == 0:
            print(f"features {index}/{len(trajectories)} ({time.monotonic() - started:.1f}s)", flush=True)
    features = pd.DataFrame(rows)
    features.to_csv(output / "trajectory_features.csv", index=False)
    print(f"features written: {len(features)} rows in {time.monotonic() - started:.1f}s", flush=True)
    return features


def oriented_scores(frame: pd.DataFrame, feature: str, direction: str) -> np.ndarray:
    values = frame[feature].to_numpy(dtype=float)
    return values if direction == "high" else -values


def tune_threshold(
    scores: np.ndarray,
    labels: np.ndarray,
    fallback_threshold: float | None = None,
) -> tuple[float, dict]:
    """Choose a non-degenerate threshold on the training folds by maximum F1.

    Unconstrained F1 tuning on an imbalanced set can select "predict every
    trajectory positive" (F1 = 2p/(1+p)).  That is not a detector, so a
    threshold is accepted only when its positive rate is between 1% and
    min(50%, 3 x prevalence) and its precision is at least the base rate.
    If no threshold satisfies that, the fixed rule threshold is used.
    """

    from sklearn.metrics import f1_score, precision_score

    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)
    if len(scores) == 0:
        return (float(fallback_threshold or 0.0), {"valid": False, "reason": "empty"})
    if len(np.unique(scores)) == 1:
        return (
            float(fallback_threshold if fallback_threshold is not None else scores[0]),
            {"valid": False, "reason": "constant_score"},
        )
    prevalence = float(labels.mean()) if len(labels) else 0.0
    max_rate = min(0.5, max(0.1, 3.0 * prevalence))
    candidates = np.unique(
        np.concatenate(
            (
                np.quantile(scores, np.linspace(0.0, 1.0, 101)),
                np.array([scores.min(), scores.max()]),
            )
        )
    )
    best = None
    for threshold in candidates:
        prediction = (scores >= threshold).astype(int)
        rate = float(prediction.mean())
        if rate < 0.01 or rate > max_rate:
            continue
        precision = float(precision_score(labels, prediction, zero_division=0))
        if precision + 1e-12 < prevalence:
            continue
        f1 = float(f1_score(labels, prediction, zero_division=0))
        recall = float(prediction[labels == 1].mean()) if np.any(labels == 1) else 0.0
        key = (f1, recall, -rate)
        if best is None or key > best[0]:
            best = (key, float(threshold), rate, precision)
    if best is None:
        threshold = float(fallback_threshold if fallback_threshold is not None else np.quantile(scores, 1.0 - prevalence))
        prediction = (scores >= threshold).astype(int)
        return (
            threshold,
            {
                "valid": False,
                "reason": "no_threshold_beats_base_rate",
                "train_positive_rate": float(prediction.mean()),
                "train_precision": float(precision_score(labels, prediction, zero_division=0)),
                "base_rate": prevalence,
            },
        )
    _, threshold, rate, precision = best
    return (
        threshold,
        {
            "valid": True,
            "train_positive_rate": rate,
            "train_precision": precision,
            "base_rate": prevalence,
        },
    )


def evaluate_fixed_rules(frame: pd.DataFrame, mask: np.ndarray | None = None) -> list[dict]:
    if mask is None:
        mask = np.ones(len(frame), dtype=bool)
    subset = frame.loc[mask].reset_index(drop=True)
    results = []
    for rule_name, rule in RULES.items():
        if rule_name == "closed_area_200":
            prediction = subset.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
        else:
            feature = rule["feature"]
            values = subset[feature].to_numpy(dtype=float)
            threshold = float(rule["fixed_threshold"])
            prediction = (values >= threshold) if rule["direction"] == "high" else (values <= threshold)
        result = metrics(subset["label"], prediction)
        result.update(
            {
                "method": rule_name,
                "method_type": "rule_fixed",
                "threshold_policy": "fixed",
                "threshold": float(rule["fixed_threshold"]),
                "description": rule["description"],
            }
        )
        results.append(result)
    for rule_name in ("stay_and_low_efficiency", "low_efficiency_and_reversal", "hull_and_dwell"):
        prediction = subset.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
        result = metrics(subset["label"], prediction)
        result.update(
            {
                "method": rule_name,
                "method_type": "rule_fixed",
                "threshold_policy": "fixed_composite",
                "threshold": None,
                "description": rule_name,
            }
        )
        results.append(result)
    return results


def scene_grouped_rules(frame: pd.DataFrame, n_splits: int = 5) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    from sklearn.model_selection import GroupKFold

    groups = frame["folder"].to_numpy()
    y = frame["label"].to_numpy(dtype=int)
    splitter = GroupKFold(n_splits=n_splits)
    prediction_rows = []
    fold_rows = []
    rule_summary = []

    for fold_index, (train_index, test_index) in enumerate(splitter.split(frame, y, groups), 1):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        fold_row = {
            "fold": fold_index,
            "train_n": int(len(train)),
            "test_n": int(len(test)),
            "train_positives": int(train["label"].sum()),
            "test_positives": int(test["label"].sum()),
            "train_folders": sorted(int(x) for x in train["folder"].unique()),
            "test_folders": sorted(int(x) for x in test["folder"].unique()),
        }
        if set(train["folder"]) & set(test["folder"]):
            raise RuntimeError("scene leakage between train and test folds")
        fold_rows.append(fold_row)
        for rule_name, rule in RULES.items():
            tune_info = {"valid": False, "reason": "binary_rule"}
            if rule_name == "closed_area_200":
                train_scores = train.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
                test_scores = test.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
                threshold = 0.5
                train_prediction = (train_scores >= threshold).astype(int)
                test_prediction = (test_scores >= threshold).astype(int)
                continuous = False
            elif rule.get("binary"):
                feature = rule["feature"]
                train_scores = train[feature].to_numpy(dtype=float)
                test_scores = test[feature].to_numpy(dtype=float)
                threshold = 0.5
                train_prediction = (train_scores >= threshold).astype(int)
                test_prediction = (test_scores >= threshold).astype(int)
                continuous = False
            else:
                feature = rule["feature"]
                direction = rule["direction"]
                train_scores = oriented_scores(train, feature, direction)
                test_scores = oriented_scores(test, feature, direction)
                fallback = (
                    float(rule["fixed_threshold"])
                    if direction == "high"
                    else -float(rule["fixed_threshold"])
                )
                threshold, tune_info = tune_threshold(
                    train_scores, train["label"].to_numpy(), fallback_threshold=fallback
                )
                train_prediction = (train_scores >= threshold).astype(int)
                test_prediction = (test_scores >= threshold).astype(int)
                continuous = True
            train_metrics = metrics(train["label"], train_prediction, train_scores if continuous else None)
            test_metrics = metrics(test["label"], test_prediction, test_scores if continuous else None)
            for row_index, track_id, label, score, prediction in zip(
                test.index, test["track_id"], test["label"], test_scores, test_prediction
            ):
                prediction_rows.append(
                    {
                        "method": rule_name,
                        "fold": fold_index,
                        "track_id": int(track_id),
                        "folder": int(test.loc[row_index, "folder"]),
                        "label": int(label),
                        "score": float(score),
                        "prediction": int(prediction),
                        "threshold": float(threshold),
                    }
                )
            rule_summary.append(
                {
                    "method": rule_name,
                    "method_type": "rule_scene_cv",
                    "fold": fold_index,
                    "threshold": float(threshold),
                    "train_f1": train_metrics["f1"],
                    "test_f1": test_metrics["f1"],
                    "test_precision": test_metrics["precision"],
                    "test_recall": test_metrics["recall"],
                    "test_n": test_metrics["n"],
                    "test_positives": test_metrics["positives"],
                    "threshold_valid": tune_info.get("valid"),
                    "threshold_reason": tune_info.get("reason"),
                    "train_positive_rate": tune_info.get("train_positive_rate"),
                    "train_precision": tune_info.get("train_precision"),
                    "base_rate": tune_info.get("base_rate"),
                }
            )
    return pd.DataFrame(prediction_rows), fold_rows, rule_summary


def scene_grouped_fixed_rules(frame: pd.DataFrame, n_splits: int = 5) -> tuple[pd.DataFrame, list[dict]]:
    """Apply the fixed thresholds unchanged, but evaluate them on scene folds."""

    from sklearn.model_selection import GroupKFold

    groups = frame["folder"].to_numpy()
    y = frame["label"].to_numpy(dtype=int)
    splitter = GroupKFold(n_splits=n_splits)
    rows = []
    for fold_index, (train_index, test_index) in enumerate(splitter.split(frame, y, groups), 1):
        test = frame.iloc[test_index]
        for rule_name, rule in RULES.items():
            if rule_name == "closed_area_200":
                prediction = test.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
                score = prediction.astype(float)
            elif rule.get("binary"):
                score = test[rule["feature"]].to_numpy(dtype=float)
                prediction = (score >= 0.5).astype(int)
            else:
                values = test[rule["feature"]].to_numpy(dtype=float)
                prediction = (values >= float(rule["fixed_threshold"])) if rule["direction"] == "high" else (
                    values <= float(rule["fixed_threshold"])
                )
                prediction = prediction.astype(int)
                score = oriented_scores(test, rule["feature"], rule["direction"])
            for row_index, track_id, label, value, pred in zip(
                test.index, test["track_id"], test["label"], score, prediction
            ):
                rows.append(
                    {
                        "method": rule_name,
                        "fold": fold_index,
                        "track_id": int(track_id),
                        "folder": int(test.loc[row_index, "folder"]),
                        "label": int(label),
                        "score": float(value),
                        "prediction": int(pred),
                        "threshold": rule.get("fixed_threshold"),
                    }
                )
        for rule_name in ("stay_and_low_efficiency", "low_efficiency_and_reversal", "hull_and_dwell"):
            prediction = test.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
            for row_index, track_id, label, pred in zip(test.index, test["track_id"], test["label"], prediction):
                rows.append(
                    {
                        "method": rule_name,
                        "fold": fold_index,
                        "track_id": int(track_id),
                        "folder": int(test.loc[row_index, "folder"]),
                        "label": int(label),
                        "score": float(pred),
                        "prediction": int(pred),
                        "threshold": None,
                    }
                )
    return pd.DataFrame(rows), []


def pooled_fixed_rule_metrics(predictions: pd.DataFrame) -> list[dict]:
    rows = []
    for method, group in predictions.groupby("method"):
        result = metrics(group["label"], group["prediction"])
        result.update(
            {
                "method": f"{method}__fixed",
                "method_type": "rule_fixed_scene_cv",
                "threshold_policy": "fixed_threshold_no_tuning",
                "threshold": group["threshold"].dropna().iloc[0] if group["threshold"].notna().any() else None,
                "description": RULES.get(method, {}).get("description", method),
            }
        )
        rows.append(result)
    return rows


def pooled_rule_metrics(predictions: pd.DataFrame) -> list[dict]:
    rows = []
    for method, group in predictions.groupby("method"):
        result = metrics(group["label"], group["prediction"], group["score"])
        result.update(
            {
                "method": method,
                "method_type": "rule_scene_cv",
                "threshold_policy": "tuned_on_train_folds_max_f1",
                "threshold": float(group["threshold"].median()),
                "description": RULES.get(method, {}).get("description", method),
            }
        )
        rows.append(result)
    return rows


def supervised_scene_cv(frame: pd.DataFrame, n_splits: int = 5) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    groups = frame["folder"].to_numpy()
    y = frame["label"].to_numpy(dtype=int)
    X = frame[NUMERIC_FEATURES].to_numpy(dtype=float)
    splitter = GroupKFold(n_splits=n_splits)
    prediction_rows = []
    summary = []
    importances = []

    for fold_index, (train_index, test_index) in enumerate(splitter.split(X, y, groups), 1):
        X_train, X_test = X[train_index], X[test_index]
        y_train, y_test = y[train_index], y[test_index]
        if set(groups[train_index]) & set(groups[test_index]):
            raise RuntimeError("scene leakage between train and test folds")

        models = {
            "logistic_regression": make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42),
            ),
            "random_forest": RandomForestClassifier(
                n_estimators=400,
                min_samples_leaf=5,
                max_features="sqrt",
                class_weight="balanced_subsample",
                n_jobs=-1,
                random_state=42,
            ),
        }
        for name, model in models.items():
            model.fit(X_train, y_train)
            probabilities = model.predict_proba(X_test)[:, 1]
            threshold = 0.5
            prediction = (probabilities >= threshold).astype(int)
            test_metrics = metrics(y_test, prediction, probabilities)
            for row_index, probability, pred in zip(test_index, probabilities, prediction):
                prediction_rows.append(
                    {
                        "method": name,
                        "fold": fold_index,
                        "track_id": int(frame.iloc[row_index]["track_id"]),
                        "folder": int(frame.iloc[row_index]["folder"]),
                        "label": int(y[row_index]),
                        "score": float(probability),
                        "prediction": int(pred),
                        "threshold": threshold,
                    }
                )
            summary.append(
                {
                    "method": name,
                    "method_type": "supervised_scene_cv",
                    "fold": fold_index,
                    "threshold": threshold,
                    "test_f1": test_metrics["f1"],
                    "test_precision": test_metrics["precision"],
                    "test_recall": test_metrics["recall"],
                    "test_pr_auc": test_metrics["pr_auc"],
                    "test_n": test_metrics["n"],
                    "test_positives": test_metrics["positives"],
                }
            )
            if name == "random_forest":
                importances.append(model.feature_importances_)
    importance_rows = []
    if importances:
        mean_importance = np.mean(np.vstack(importances), axis=0)
        order = np.argsort(mean_importance)[::-1]
        importance_rows = [
            {"feature": NUMERIC_FEATURES[index], "importance": float(mean_importance[index])}
            for index in order
        ]
    return pd.DataFrame(prediction_rows), summary, importance_rows


def pooled_supervised_metrics(predictions: pd.DataFrame) -> list[dict]:
    rows = []
    for method, group in predictions.groupby("method"):
        result = metrics(group["label"], group["prediction"], group["score"])
        result.update(
            {
                "method": method,
                "method_type": "supervised_scene_cv",
                "threshold_policy": "probability_0.5",
                "threshold": 0.5,
                "description": "reference classifier on trajectory-level features",
            }
        )
        rows.append(result)
    return rows


def official_split_reference(frame: pd.DataFrame, dataset_dir: Path) -> list[dict]:
    train = pd.read_csv(dataset_dir / "train_df.csv")
    test = pd.read_csv(dataset_dir / "test_df.csv")
    train_ids = set(train["id"])
    test_ids = set(test["id"])
    results = []
    for rule_name, rule in RULES.items():
        train_frame = frame[frame["track_id"].isin(train_ids)]
        test_frame = frame[frame["track_id"].isin(test_ids)]
        if rule_name == "closed_area_200":
            train_scores = train_frame.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
            test_scores = test_frame.apply(lambda row: apply_composite_rule(row, rule_name), axis=1).to_numpy()
            threshold = 0.5
        elif rule.get("binary"):
            feature = rule["feature"]
            train_scores = train_frame[feature].to_numpy(dtype=float)
            test_scores = test_frame[feature].to_numpy(dtype=float)
            threshold = 0.5
        else:
            feature = rule["feature"]
            direction = rule["direction"]
            train_scores = oriented_scores(train_frame, feature, direction)
            test_scores = oriented_scores(test_frame, feature, direction)
            fallback = (
                float(rule["fixed_threshold"])
                if direction == "high"
                else -float(rule["fixed_threshold"])
            )
            threshold, _ = tune_threshold(
                train_scores, train_frame["label"].to_numpy(), fallback_threshold=fallback
            )
        prediction = (test_scores >= threshold).astype(int)
        result = metrics(test_frame["label"], prediction, test_scores if rule_name != "closed_area_200" else None)
        result.update(
            {
                "method": rule_name,
                "method_type": "rule_official_split_reference",
                "threshold_policy": "tuned_on_official_train_max_f1",
                "threshold": float(threshold),
                "description": "NOT scene-grouped; same clip/folder may appear in train and test",
            }
        )
        results.append(result)
    return results


def per_folder_table(predictions: pd.DataFrame, method: str) -> list[dict]:
    rows = []
    for folder, group in predictions[predictions["method"] == method].groupby("folder"):
        result = metrics(group["label"], group["prediction"])
        rows.append({"folder": int(folder), **result})
    return rows


def write_figures(frame: pd.DataFrame, pooled_predictions: pd.DataFrame, output: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    paths = []

    # Confusion matrix for the best supervised method.
    from sklearn.metrics import ConfusionMatrixDisplay

    method = "random_forest"
    group = pooled_predictions[pooled_predictions["method"] == method]
    if len(group):
        fig, ax = plt.subplots(figsize=(4.2, 3.6))
        ConfusionMatrixDisplay.from_predictions(
            group["label"], group["prediction"], labels=[0, 1], ax=ax, colorbar=False
        )
        ax.set_title("Random Forest, scene-grouped CV")
        path = output / "confusion_random_forest.png"
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(str(path.relative_to(PROJECT_DIR)))

    # Score distributions.
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
    for ax, feature, title in zip(
        axes,
        ["duration_seconds", "path_efficiency", "hull_area_px2"],
        ["Duration (s)", "Path efficiency", "Hull area (px$^2$)"],
    ):
        data = [frame[frame["label"] == 0][feature], frame[frame["label"] == 1][feature]]
        ax.hist(data, bins=40, density=True, alpha=0.55, label=["non-loitering", "loitering"])
        ax.set_title(title)
        ax.set_yscale("log")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    path = output / "feature_distributions.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(PROJECT_DIR)))

    # F1 bar chart.
    summary_path = output.parent / "metrics.json"
    if summary_path.is_file():
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = [row for row in data["primary_metrics"] if row["method_type"] in {"rule_scene_cv", "rule_fixed_scene_cv", "supervised_scene_cv"}]
        rows.sort(key=lambda row: row["f1"], reverse=True)
        fig, ax = plt.subplots(figsize=(8.5, 4.8))
        ax.barh([row["method"] for row in rows], [row["f1"] for row in rows], color="#4C78A8")
        ax.set_xlabel("F1")
        ax.set_title("Scene-grouped CV, pooled out-of-fold F1")
        ax.set_xlim(0, max(0.05, max(row["f1"] for row in rows) * 1.15))
        fig.tight_layout()
        path = output / "method_f1.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(str(path.relative_to(PROJECT_DIR)))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="A2 Loitering trajectory-level experiment")
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.report.mkdir(parents=True, exist_ok=True)

    print("loading annotations...", flush=True)
    _, trajectories, audit = load_trajectories(args.dataset_dir)
    write_json(args.output / "dataset_audit.json", audit)

    print("extracting features...", flush=True)
    features = build_features(trajectories, args.output)
    # Degenerate geometry (e.g. a collinear ellipse fit) is recorded as 0 by
    # the extractor; this guard makes any future non-finite value explicit.
    non_finite_counts = {}
    for column in NUMERIC_FEATURES:
        values = features[column].to_numpy(dtype=float)
        bad = int(np.count_nonzero(~np.isfinite(values)))
        if bad:
            non_finite_counts[column] = bad
            features[column] = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    if non_finite_counts:
        print(f"non-finite feature values replaced with 0: {non_finite_counts}", flush=True)

    print("fixed rules on all data...", flush=True)
    fixed_all = evaluate_fixed_rules(features)

    print("fixed rules on official test split...", flush=True)
    train_ids = set(pd.read_csv(args.dataset_dir / "train_df.csv")["id"])
    test_ids = set(pd.read_csv(args.dataset_dir / "test_df.csv")["id"])
    fixed_official_test = evaluate_fixed_rules(features, features["track_id"].isin(test_ids).to_numpy())

    print("tuning rules with scene-grouped folds...", flush=True)
    rule_predictions, fold_rows, rule_fold_summary = scene_grouped_rules(features, args.folds)
    rule_pooled = pooled_rule_metrics(rule_predictions)

    print("fixed rules on scene-grouped folds...", flush=True)
    fixed_scene_predictions, _ = scene_grouped_fixed_rules(features, args.folds)
    fixed_scene_pooled = pooled_fixed_rule_metrics(fixed_scene_predictions)

    print("training supervised references with scene-grouped folds...", flush=True)
    supervised_predictions, supervised_fold_summary, importances = supervised_scene_cv(features, args.folds)
    supervised_pooled = pooled_supervised_metrics(supervised_predictions)

    print("official random-split reference...", flush=True)
    official_reference = official_split_reference(features, args.dataset_dir)

    methods = rule_pooled + fixed_scene_pooled + supervised_pooled
    methods.sort(key=lambda row: row["f1"], reverse=True)
    summary = {
        "schema": "a2-loitering-metrics-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_audit": str((args.output / "dataset_audit.json").relative_to(PROJECT_DIR)),
        "label_level": "trajectory",
        "event_localization": "not available in release",
        "primary_protocol": "scene-grouped 5-fold CV (groups = source date folder)",
        "primary_metrics": methods,
        "fixed_rules_all_data": fixed_all,
        "fixed_rules_scene_cv_pooled": fixed_scene_pooled,
        "fixed_rules_official_test": fixed_official_test,
        "official_random_split_reference": official_reference,
        "fold_assignment": fold_rows,
        "rule_fold_summary": rule_fold_summary,
        "supervised_fold_summary": supervised_fold_summary,
        "random_forest_feature_importance": importances,
        "per_folder_random_forest": per_folder_table(supervised_predictions, "random_forest"),
        "per_folder_bbox_area_fixed": per_folder_table(fixed_scene_predictions, "bbox_area_200"),
        "per_folder_low_path_efficiency_fixed": per_folder_table(fixed_scene_predictions, "low_path_efficiency_0.3"),
        "non_finite_feature_values_replaced_with_zero": non_finite_counts,
        "limitations": [
            "Labels are trajectory-level; no event start/end or false-alarm duration.",
            "Scene grouping is by source date folder; the release does not expose camera/site ids, so same-site leakage across dates cannot be excluded.",
            "Features use the whole trajectory, so this is not a causal event detector.",
            "The repository's official split is random by track id and is reported only as a reference.",
            "No IPM features are used; released IPM coordinates contain extreme outliers.",
            "PAB is a separate image-text dataset and is not used here.",
        ],
    }
    write_json(args.report / "metrics.json", summary)

    # Predictions and tables.
    rule_predictions.to_csv(args.report / "rule_predictions_scene_cv.csv", index=False)
    fixed_scene_predictions.to_csv(args.report / "fixed_rule_predictions_scene_cv.csv", index=False)
    supervised_predictions.to_csv(args.report / "supervised_predictions_scene_cv.csv", index=False)
    pd.DataFrame(rule_fold_summary).to_csv(args.report / "rule_per_fold.csv", index=False)
    pd.DataFrame(supervised_fold_summary).to_csv(args.report / "supervised_per_fold.csv", index=False)
    pd.DataFrame(importances).to_csv(args.report / "feature_importance.csv", index=False)
    pd.DataFrame(fixed_all + fixed_official_test).to_csv(args.report / "fixed_rules.csv", index=False)
    pd.DataFrame(official_reference).to_csv(args.report / "official_split_reference.csv", index=False)

    # Combined metrics table for reports.
    all_methods = pd.DataFrame(methods)
    all_methods.to_csv(args.report / "methods_pooled.csv", index=False)
    fixed_all_frame = pd.DataFrame(fixed_all)
    fixed_all_frame.to_csv(args.report / "fixed_rules_all_data.csv", index=False)

    figure_paths = write_figures(features, pd.concat([rule_predictions, supervised_predictions]), args.report / "figures")
    summary["figures"] = figure_paths
    write_json(args.report / "metrics.json", summary)

    print("\n=== scene-grouped pooled metrics ===", flush=True)
    print(all_methods[["method", "method_type", "precision", "recall", "f1", "pr_auc", "tp", "fp", "fn", "tn"]].to_string(index=False), flush=True)
    print("\n=== fixed rules (all data) ===", flush=True)
    print(fixed_all_frame[["method", "precision", "recall", "f1", "tp", "fp", "fn", "tn"]].to_string(index=False), flush=True)
    print(f"\noutput: {args.output}\nreport: {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
