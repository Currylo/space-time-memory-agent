#!/usr/bin/env python3
"""Independent integrity checks for the E02 public benchmark run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(condition: bool, label: str, detail: str = "") -> dict:
    result = {"check": label, "ok": bool(condition), "detail": detail}
    print(("PASS  " if condition else "FAIL  ") + label + (f"  [{detail}]" if detail else ""))
    if not condition:
        raise AssertionError(label + (f": {detail}" if detail else ""))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="E02 public benchmark integrity checks")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/e02_mot17_frcnn_baseline.json")
    parser.add_argument("--run", type=Path, default=PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "reports/E02公开基准核验.json")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    manifest = json.loads((PROJECT_DIR / "data/e02/sequence_manifest.json").read_text(encoding="utf-8"))
    lock = json.loads((PROJECT_DIR / "components.lock.json").read_text(encoding="utf-8"))
    results = []

    results.append(check(run["status"] == "completed", "推理运行状态 completed", run["run_id"]))
    results.append(check(run["dataset_id"] == config["dataset_id"], "运行与冻结配置数据集一致"))
    results.append(check(run["config_hash"] == sha256_file(args.config), "运行配置哈希一致"))
    results.append(check(run["weights_hash"] == lock["assets"]["detector_weights"]["sha256"], "检测器权重哈希与组件锁一致", run["weights_hash"][:12]))
    results.append(check(run["gt_sha256"] == manifest["gt_sha256"], "运行记录保留官方 GT 哈希"))
    results.append(check(run["image_manifest_sha256"] == manifest["image_manifest_sha256"], "运行记录保留图像清单哈希"))
    results.append(check(run["public_detections_used"] is False and run["ground_truth_used_as_predictions"] is False, "未把官方检测框/GT 当作系统预测"))
    results.append(check(run["processed_frames"] == 600 and run["prediction_rows"] > 0, "完整处理 600 帧并输出预测", f"{run['processed_frames']} 帧 / {run['prediction_rows']} 行"))

    images = sorted((PROJECT_DIR / config["image_dir"]).glob("*.jpg"))
    results.append(check(len(images) == 600 and images[0].name == "000001.jpg" and images[-1].name == "000600.jpg", "官方图像序列完整", f"{len(images)} 张"))
    results.append(check(not (PROJECT_DIR / config["sequence_dir"] / "det").exists(), "未使用官方 det.txt 目录"))

    with (PROJECT_DIR / "data/e02/raw/MOT17-02-FRCNN/gt/gt.txt").open("r", encoding="utf-8") as handle:
        gt_rows = [row for row in csv.reader(handle, delimiter=",") if row]
    pedestrian = [row for row in gt_rows if int(row[7]) == 1 and float(row[6]) > 0]
    results.append(check(len(gt_rows) == 30003 and len(pedestrian) == 18581, "官方 GT 行数与行人 GT 数一致", f"{len(gt_rows)} / {len(pedestrian)}"))
    results.append(check(len({int(row[1]) for row in pedestrian}) == 62, "官方行人 GT ID 数为 62"))

    pred_path = PROJECT_DIR / run["prediction_file"]
    results.append(check(sha256_file(pred_path) == run["prediction_sha256"], "预测 MOT 文件哈希一致"))
    pred_copy = PROJECT_DIR / "data/e02/predictions/MOT17-02-FRCNN.txt"
    results.append(check(pred_copy.is_file() and sha256_file(pred_copy) == run["prediction_sha256"], "预测副本哈希一致"))
    pred_rows = []
    for line in pred_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = line.split(",")
        pred_rows.append(row)
    results.append(check(len(pred_rows) == run["prediction_rows"], "预测行数与运行记录一致", str(len(pred_rows))))
    frame_numbers = [int(row[0]) for row in pred_rows]
    ids = {int(row[1]) for row in pred_rows}
    valid_boxes = all(
        len(row) >= 10
        and float(row[4]) > 0
        and float(row[5]) > 0
        and -1e-6 <= float(row[2]) <= float(config["width"])
        and -1e-6 <= float(row[3]) <= float(config["height"])
        and float(row[2]) + float(row[4]) <= float(config["width"]) + 1e-3
        and float(row[3]) + float(row[5]) <= float(config["height"]) + 1e-3
        for row in pred_rows
    )
    results.append(check(min(frame_numbers) == 1 and max(frame_numbers) == 600 and valid_boxes, "预测帧号 1..600、坐标有效", f"{min(frame_numbers)}..{max(frame_numbers)}"))
    results.append(check(len(ids) == run["track_count"], "原始预测轨迹 ID 数与运行记录一致", str(len(ids))))
    results.append(check(len(pred_rows) != len(pedestrian), "预测行不是官方 GT 的复制"))

    trackeval_summary = PROJECT_DIR / "data/e02/trackeval/output/e02_yolov8n_bytetrack/pedestrian_summary.txt"
    lines = [line for line in trackeval_summary.read_text().splitlines() if line.strip()]
    header, values = lines[0].split(), lines[1].split()
    summary = {key: (float(value) if "." in value else int(value)) for key, value in zip(header, values)}
    results.append(check(all(key in summary for key in ("IDF1", "IDSW", "Frag", "HOTA", "MOTA")), "TrackEval 摘要包含 IDF1/IDSW/Frag/HOTA/MOTA"))
    results.append(check(summary["GT_Dets"] == 18581 and summary["GT_IDs"] == 62, "TrackEval 使用完整有效 GT", f"{summary['GT_Dets']} / {summary['GT_IDs']}"))
    metrics = json.loads((PROJECT_DIR / "reports/E02公开基准指标.json").read_text(encoding="utf-8"))["metrics"]
    results.append(check(metrics == summary, "保存指标与 TrackEval 摘要一致"))
    raw = json.loads((PROJECT_DIR / "data/e02/trackeval/output/trackeval_raw_results.json").read_text(encoding="utf-8"))
    seq_data = raw["MotChallenge2DBox"]["e02_yolov8n_bytetrack"]["MOT17-02-FRCNN"]
    results.append(check("pedestrian" in seq_data, "原始 TrackEval 结果包含目标序列"))

    command = json.loads((PROJECT_DIR / "data/e02/trackeval/output/trackeval_command.json").read_text(encoding="utf-8"))
    results.append(check(command["trackeval_commit"] == config["evaluation"]["commit"], "TrackEval 提交与冻结配置一致"))
    results.append(check(command["dataset_config"]["BENCHMARK"] == "MOT17" and command["dataset_config"]["SPLIT_TO_EVAL"] == "train", "TrackEval 使用 MOT17 train 协议"))
    results.append(check(command["dataset_config"]["CLASSES_TO_EVAL"] == ["pedestrian"] and command["metrics_config"]["THRESHOLD"] == 0.5, "TrackEval 行人类别与 0.5 阈值"))

    for name in ("01-id-switch.jpg", "02-miss.jpg", "03-fragment.jpg", "04-entry-exit-border.jpg", "error_cases.json"):
        results.append(check((PROJECT_DIR / "reports/e02/cases" / name).is_file(), f"错误案例存在：{name}"))

    output = {
        "schema": "e02-public-benchmark-verify-v1",
        "checks_passed": len(results),
        "checks_failed": 0,
        "run_id": run["run_id"],
        "sequence": manifest["sequence"],
        "prediction_rows": len(pred_rows),
        "trackeval_summary": summary,
        "checks": results,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n全部 {len(results)} 项检查通过；结果已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
