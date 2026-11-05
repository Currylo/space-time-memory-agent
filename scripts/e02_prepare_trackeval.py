#!/usr/bin/env python3
"""Prepare the official MOT17 GT and E02 predictions for TrackEval."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare MOT17 GT/prediction layout for TrackEval")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/e02_mot17_frcnn_baseline.json")
    parser.add_argument("--run", type=Path, default=PROJECT_DIR / "runs/e02-mot17-02-frcnn-yolov8n-byte-001")
    parser.add_argument("--root", type=Path, default=PROJECT_DIR / "data/e02/trackeval")
    parser.add_argument("--tracker-name", default="e02_yolov8n_bytetrack")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    if run.get("status") != "completed":
        raise SystemExit(f"run is not completed: {run.get('status')}")
    if run["prediction_sha256"] != sha256_file(PROJECT_DIR / run["prediction_file"]):
        raise SystemExit("prediction hash mismatch")

    seq = config["sequence"]
    benchmark = config["evaluation"]["benchmark"]
    split = config["evaluation"]["split_to_eval"]
    gt_set = f"{benchmark}-{split}"
    raw_seq = PROJECT_DIR / config["sequence_dir"]
    pred_path = PROJECT_DIR / run["prediction_file"]

    root = args.root.resolve()
    gt_dir = root / "gt" / gt_set / seq
    tracker_dir = root / "trackers" / gt_set / args.tracker_name / "data"
    seqmap_dir = root / "seqmaps"
    output_dir = root / "output"
    for directory in (gt_dir / "gt", tracker_dir, seqmap_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    gt_src = raw_seq / "gt" / "gt.txt"
    ini_src = raw_seq / "seqinfo.ini"
    gt_dst = gt_dir / "gt" / "gt.txt"
    ini_dst = gt_dir / "seqinfo.ini"
    shutil.copyfile(gt_src, gt_dst)
    shutil.copyfile(ini_src, ini_dst)
    pred_dst = tracker_dir / f"{seq}.txt"
    shutil.copyfile(pred_path, pred_dst)
    seqmap = seqmap_dir / "MOT17-train-single.txt"
    seqmap.write_text(f"name\n{seq}\n", encoding="utf-8")

    manifest = {
        "schema": "e02-trackeval-input-v1",
        "trackeval_commit": config["evaluation"]["commit"],
        "benchmark": benchmark,
        "split": split,
        "classes_to_eval": config["evaluation"]["classes_to_eval"],
        "metrics": config["evaluation"]["metrics"],
        "threshold": config["evaluation"]["threshold"],
        "do_preproc": config["evaluation"]["do_preproc"],
        "use_parallel": config["evaluation"]["use_parallel"],
        "sequence": seq,
        "seqmap": str(seqmap.relative_to(PROJECT_DIR)),
        "gt_folder": str((root / "gt").relative_to(PROJECT_DIR)),
        "trackers_folder": str((root / "trackers").relative_to(PROJECT_DIR)),
        "output_folder": str(output_dir.relative_to(PROJECT_DIR)),
        "gt_path": str(gt_dst.relative_to(PROJECT_DIR)),
        "gt_sha256": sha256_file(gt_dst),
        "seqinfo_path": str(ini_dst.relative_to(PROJECT_DIR)),
        "seqinfo_sha256": sha256_file(ini_dst),
        "prediction_path": str(pred_dst.relative_to(PROJECT_DIR)),
        "prediction_sha256": sha256_file(pred_dst),
        "prediction_source": run["prediction_file"],
        "tracker_name": args.tracker_name,
        "run_id": run["run_id"],
        "format_note": "Official GT copied unchanged; prediction is the E02 YOLOv8n+ByteTrack MOT file. GT boxes are not used as predictions.",
    }
    manifest_path = root / "trackeval_input_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
