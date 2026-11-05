#!/usr/bin/env python3
"""Checks for E02 diagnosis and the single-factor 1280 comparison."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def check(condition: bool, label: str, detail: str = "") -> dict:
    result = {"check": label, "ok": bool(condition), "detail": detail}
    print(("PASS  " if condition else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))
    if not condition:
        raise AssertionError(label + (f": {detail}" if detail else ""))
    return result


def main() -> int:
    results = []
    base = json.loads((PROJECT_DIR / "reports/e02/diagnosis/diagnosis.json").read_text(encoding="utf-8"))
    alt = json.loads((PROJECT_DIR / "reports/e02/diagnosis_img1280/diagnosis.json").read_text(encoding="utf-8"))
    comp = json.loads((PROJECT_DIR / "reports/E02诊断对照指标.json").read_text(encoding="utf-8"))
    base_run = json.loads((PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001/run.json").read_text(encoding="utf-8"))
    alt_run = json.loads((PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-img1280-001/run.json").read_text(encoding="utf-8"))
    base_cfg = json.loads((PROJECT_DIR / "configs/e02_mot17_frcnn_baseline.json").read_text(encoding="utf-8"))
    alt_cfg = json.loads((PROJECT_DIR / "configs/e02_mot17_frcnn_img1280.json").read_text(encoding="utf-8"))

    for name, diagnosis in (("baseline", base), ("img1280", alt)):
        c = diagnosis["coverage"]
        categories = [
            "assigned_both", "det_assigned_not_tracked", "det_overlap_not_assigned",
            "track_assigned_det_not_matched", "partial_overlap_below_iou", "no_detection_overlap",
        ]
        results.append(check(sum(c[k] for k in categories) == c["valid_pedestrian_gt_boxes"] == 18581, f"{name} 覆盖分类总数等于有效 GT"))
        results.append(check(abs(c["detection_assigned_coverage_iou_0_5"] - (c["assigned_both"] + c["det_assigned_not_tracked"]) / 18581) < 0.0002, f"{name} 检测 assigned 覆盖率可复核"))
        results.append(check(abs(c["tracking_assigned_coverage_iou_0_5"] - (c["assigned_both"] + c["track_assigned_det_not_matched"]) / 18581) < 0.0002, f"{name} 跟踪 assigned 覆盖率可复核"))
        results.append(check(diagnosis["matching"]["iou_match"] == 0.5 and diagnosis["matching"]["partial_iou_lower"] == 0.1, f"{name} 诊断匹配口径固定"))

    c = base["coverage"]
    results.append(check(c["no_detection_overlap"] == 7113 and c["partial_overlap_below_iou"] == 4811, "基线无检测/partial 数一致"))
    results.append(check(c["det_assigned_not_tracked"] == 1584 and c["det_overlap_not_tracked"] == 2424, "基线检测到跟踪损失数一致"))
    results.append(check(base["confidence"]["detected_not_tracked_best_score"]["below_activation_0_25"] == 1674, "低于 0.25 启动阈值的损失数一致"))
    results.append(check(base["cases"]["gt3_frame335"]["frames"][0]["best_det_assigned_gt_id"] == 19, "GT3/335 匹配竞争归因到 GT19"))
    results.append(check(base["cases"]["gt33_frames548_549"]["frames"][0]["best_track_id"] == 123 and base["cases"]["gt33_frames548_549"]["frames"][1]["best_track_id"] == 150, "GT33 ID 切换 123→150 可复核"))
    results.append(check(base["cases"]["gt36_frames329_380_431"]["frames"][1]["best_det_iou"] < 0.5, "GT36/380 检测框低于 IoU 0.5"))

    results.append(check(base_run["status"] == "completed" and alt_run["status"] == "completed", "基线与 1280 对照运行均完成"))
    results.append(check(base_cfg["image_size"] == 640 and alt_cfg["image_size"] == 1280, "基线与 1280 对照输入尺寸正确"))
    results.append(check(all(base_cfg[k] == alt_cfg[k] for k in ("confidence", "iou", "classes", "tracker", "weights", "image_size") if k != "image_size"), "除输入尺寸外检测/跟踪参数一致"))
    results.append(check(base_cfg["confidence"] == alt_cfg["confidence"] and base_cfg["tracker"] == alt_cfg["tracker"], "confidence 与 ByteTrack 参数固定"))
    results.append(check(alt_run["config_hash"] != base_run["config_hash"], "1280 配置与基线配置不同"))
    results.append(check(comp["single_factor"].startswith("detector input size 640 -> 1280"), "对照只改变输入尺寸"))
    results.append(check(comp["deltas"]["IDF1"] > 0 and comp["deltas"]["IDSW"] > 0 and comp["deltas"]["Frag"] > 0, "1280 对照如实记录收益与退化"))
    results.append(check(comp["deltas"]["detection_assigned_coverage_pp"] > 0, "1280 检测覆盖提升"))
    results.append(check(comp["deltas"]["gpu_peak_ratio"] > 1, "1280 资源增加已记录"))

    for name in ("gt3-335.jpg", "gt33-548-549.jpg", "gt36-329-380-431.jpg"):
        results.append(check((PROJECT_DIR / "reports/e02/diagnosis/cases" / name).is_file(), f"诊断案例存在：{name}"))

    out = {"schema": "e02-diagnosis-verify-v1", "checks_passed": len(results), "checks_failed": 0, "checks": results}
    output = PROJECT_DIR / "reports/E02诊断核验.json"
    output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n全部 {len(results)} 项检查通过；结果已写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
