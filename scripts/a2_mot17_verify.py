#!/usr/bin/env python3
"""A2: verify the seven-scene MOT17 run, predictions, GT and TrackEval inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN = PROJECT_DIR / "runs/a2-mot17-7seq-yolov8n-byte-001"
DEFAULT_MANIFEST = PROJECT_DIR / "data/a2/mot17/sequences_manifest.json"
DEFAULT_TRACKEVAL = PROJECT_DIR / "data/a2/mot17/trackeval"
DEFAULT_METRICS = PROJECT_DIR / "reports/a2/mot17/metrics.json"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/A2-MOT17核验.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify A2 MOT17 run")
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/a2_mot17_multiseq_baseline.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--trackeval", type=Path, default=DEFAULT_TRACKEVAL)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    args.run = args.run.resolve()
    args.config = args.config.resolve()
    args.manifest = args.manifest.resolve()
    args.trackeval = args.trackeval.resolve()
    args.metrics = args.metrics.resolve()
    args.output = args.output.resolve()
    checks = []

    def check(name: str, passed: bool, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    run = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    check("run completed", run.get("status") == "completed", run.get("status"))
    check("seven sequences registered", len(manifest["sequences"]) == 7, len(manifest["sequences"]))
    check("seven sequences completed", len(run.get("sequences", {})) == 7, len(run.get("sequences", {})))
    check(
        "all frames processed",
        run.get("processed_frames") == sum(item["frame_count"] for item in manifest["sequences"]),
        run.get("processed_frames"),
    )
    check(
        "config hash",
        run["config_hash"] == sha256_file(args.config),
        run["config_hash"],
    )

    prediction_rows = {}
    for sequence in manifest["sequences"]:
        name = sequence["sequence"]
        record = run["sequences"][name]
        seq_dir = args.run / name
        pred_path = PROJECT_DIR / record["prediction_file"]
        check(f"{name}: completed", record["status"] == "completed", record["status"])
        check(f"{name}: frames", record["processed_frames"] == sequence["frame_count"], record["processed_frames"])
        check(f"{name}: prediction file", pred_path.is_file(), str(pred_path))
        check(f"{name}: prediction hash", sha256_file(pred_path) == record["prediction_sha256"], record["prediction_sha256"])
        gt_path = PROJECT_DIR / "data/a2/mot17/raw" / name / "gt" / "gt.txt"
        check(f"{name}: GT hash", sha256_file(gt_path) == sequence["gt_sha256"], sequence["gt_sha256"])
        source_path = PROJECT_DIR / "data/a2/mot17/raw" / name / "source_manifest.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        video_path = PROJECT_DIR / source["video_path"]
        check(f"{name}: video hash", sha256_file(video_path) == source["video_sha256"], source["video_sha256"])
        check(f"{name}: image count", len(list((PROJECT_DIR / "data/a2/mot17/raw" / name / "img1").glob("*.jpg"))) == sequence["frame_count"], sequence["frame_count"])
        check(f"{name}: frame scale", math.isclose(sequence["frame_scale_x"] * sequence["frame_width"], sequence["width"]), sequence["frame_scale_x"])

        rows = []
        with pred_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                values = [float(value) for value in line.split(",")]
                if len(values) != 10:
                    check(f"{name}: prediction columns", False, f"line {line_number}")
                    continue
                rows.append(values)
        prediction_rows[name] = len(rows)
        frame_ok = all(1 <= int(row[0]) <= sequence["frame_count"] for row in rows)
        id_ok = all(int(row[1]) > 0 for row in rows)
        finite_ok = all(all(math.isfinite(value) for value in row[:7]) for row in rows)
        coord_ok = all(
            0 <= row[2] <= sequence["width"] + 1e-6
            and 0 <= row[3] <= sequence["height"] + 1e-6
            and row[4] > 0 and row[5] > 0
            and row[2] + row[4] <= sequence["width"] + 1e-6
            and row[3] + row[5] <= sequence["height"] + 1e-6
            for row in rows
        )
        score_ok = all(0.0 <= row[6] <= 1.0 for row in rows)
        check(f"{name}: prediction frame range", frame_ok, f"rows={len(rows)}")
        check(f"{name}: prediction ids", id_ok, f"rows={len(rows)}")
        check(f"{name}: prediction finite", finite_ok, f"rows={len(rows)}")
        check(f"{name}: prediction coordinates", coord_ok, f"rows={len(rows)}")
        check(f"{name}: prediction confidence", score_ok, f"rows={len(rows)}")
        check(f"{name}: prediction not GT copy", sha256_file(pred_path) != sha256_file(gt_path), "different file hashes")

    seqmap = args.trackeval / "seqmaps" / "MOT17-train-7.txt"
    seqmap_lines = [line.strip() for line in seqmap.read_text(encoding="utf-8").splitlines() if line.strip()]
    check("seqmap header", seqmap_lines[0] == "name", seqmap_lines[0] if seqmap_lines else None)
    check("seqmap seven sequences", len(seqmap_lines) == 8, len(seqmap_lines))
    check(
        "seqmap matches manifest",
        set(seqmap_lines[1:]) == {item["sequence"] for item in manifest["sequences"]},
        seqmap_lines[1:],
    )
    input_manifest = json.loads((args.trackeval / "trackeval_input_manifest.json").read_text(encoding="utf-8"))
    check("TrackEval input seven sequences", input_manifest["sequence_count"] == 7, input_manifest["sequence_count"])

    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    check("metrics seven sequences", len(metrics["per_sequence"]) == 7, len(metrics["per_sequence"]))
    for key in ("HOTA", "MOTA", "IDF1", "IDSW", "Frag", "CLR_TP", "CLR_FN", "CLR_FP"):
        check(f"combined metric present: {key}", key in metrics["combined"], metrics["combined"].get(key))
    check("combined HOTA plausible", 0 < metrics["combined"]["HOTA"] < 100, metrics["combined"]["HOTA"])
    check("combined IDF1 plausible", 0 < metrics["combined"]["IDF1"] < 100, metrics["combined"]["IDF1"])
    check("combined MOTA plausible", -100 < metrics["combined"]["MOTA"] < 100, metrics["combined"]["MOTA"])

    # Sanity check: the video-derived MOT17-02 variant should be close to the
    # original-image E02 run; large differences would indicate a scaling bug.
    e02 = json.loads((PROJECT_DIR / "reports/E02公开基准指标.json").read_text(encoding="utf-8"))["metrics"]
    combined_02 = metrics["per_sequence"]["MOT17-02-FRCNN"]
    check("MOT17-02 video vs image HOTA within 2 points", abs(combined_02["HOTA"] - e02["HOTA"]) < 2.0, {"video": combined_02["HOTA"], "image": e02["HOTA"]})
    check("MOT17-02 video vs image IDF1 within 3 points", abs(combined_02["IDF1"] - e02["IDF1"]) < 3.0, {"video": combined_02["IDF1"], "image": e02["IDF1"]})
    check("MOT17-02 video vs image MOTA within 3 points", abs(combined_02["MOTA"] - e02["MOTA"]) < 3.0, {"video": combined_02["MOTA"], "image": e02["MOTA"]})

    result = {
        "schema": "a2-mot17-verification-v1",
        "run": str(args.run.relative_to(PROJECT_DIR)),
        "checks_total": len(checks),
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_failed": sum(1 for item in checks if not item["passed"]),
        "checks": checks,
        "prediction_rows": prediction_rows,
        "metrics_combined": metrics["combined"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("checks_total", "checks_passed", "checks_failed", "prediction_rows")}, ensure_ascii=False, indent=2))
    failed = [item for item in checks if not item["passed"]]
    if failed:
        print(json.dumps(failed, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
