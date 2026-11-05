#!/usr/bin/env python3
"""A2: prepare seven MOT17 sequences and predictions for TrackEval."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare A2 MOT17 seven-sequence TrackEval input")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "configs/a2_mot17_multiseq_baseline.json")
    parser.add_argument("--run", type=Path, default=PROJECT_DIR / "runs/a2-mot17-7seq-yolov8n-byte-001")
    parser.add_argument("--manifest", type=Path, default=PROJECT_DIR / "data/a2/mot17/sequences_manifest.json")
    parser.add_argument("--root", type=Path, default=PROJECT_DIR / "data/a2/mot17/trackeval")
    parser.add_argument("--tracker-name", default="a2_yolov8n_bytetrack")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    if run.get("status") != "completed":
        raise SystemExit(f"run is not completed: {run.get('status')}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    sequences = manifest["sequences"]
    if len(sequences) != 7:
        raise SystemExit(f"expected 7 sequences, found {len(sequences)}")

    root = args.root.resolve()
    gt_set = "MOT17-train"
    gt_root = root / "gt" / gt_set
    tracker_dir = root / "trackers" / gt_set / args.tracker_name / "data"
    seqmap_dir = root / "seqmaps"
    output_dir = root / "output"
    for directory in (gt_root, tracker_dir, seqmap_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    copied = []
    for sequence in sequences:
        name = sequence["sequence"]
        seq_record = run["sequences"].get(name)
        if not seq_record or seq_record["status"] != "completed":
            raise SystemExit(f"sequence not completed: {name}")
        sequence_dir = PROJECT_DIR / "data/a2/mot17/raw" / name
        gt_src = sequence_dir / "gt" / "gt.txt"
        ini_src = sequence_dir / "seqinfo.ini"
        if sha256_file(gt_src) != sequence["gt_sha256"]:
            raise SystemExit(f"GT hash mismatch: {name}")
        gt_dst_dir = gt_root / name / "gt"
        gt_dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gt_src, gt_dst_dir / "gt.txt")
        shutil.copyfile(ini_src, gt_root / name / "seqinfo.ini")
        pred_src = PROJECT_DIR / seq_record["prediction_file"]
        if sha256_file(pred_src) != seq_record["prediction_sha256"]:
            raise SystemExit(f"prediction hash mismatch: {name}")
        shutil.copyfile(pred_src, tracker_dir / f"{name}.txt")
        copied.append({
            "sequence": name,
            "gt_sha256": sequence["gt_sha256"],
            "prediction_sha256": seq_record["prediction_sha256"],
            "prediction_source": seq_record["prediction_file"],
            "prediction_rows": seq_record["prediction_rows"],
        })

    seqmap = seqmap_dir / "MOT17-train-7.txt"
    seqmap.write_text("name\n" + "\n".join(item["sequence"] for item in copied) + "\n", encoding="utf-8")
    input_manifest = {
        "schema": "a2-mot17-trackeval-input-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trackeval_commit": config["evaluation"]["commit"],
        "benchmark": config["evaluation"]["benchmark"],
        "split": config["evaluation"]["split_to_eval"],
        "classes_to_eval": config["evaluation"]["classes_to_eval"],
        "metrics": config["evaluation"]["metrics"],
        "threshold": config["evaluation"]["threshold"],
        "do_preproc": config["evaluation"]["do_preproc"],
        "use_parallel": config["evaluation"]["use_parallel"],
        "sequence_count": len(copied),
        "sequences": copied,
        "seqmap": str(seqmap.relative_to(PROJECT_DIR)),
        "gt_folder": str((root / "gt").relative_to(PROJECT_DIR)),
        "trackers_folder": str((root / "trackers").relative_to(PROJECT_DIR)),
        "output_folder": str(output_dir.relative_to(PROJECT_DIR)),
        "tracker_name": args.tracker_name,
        "run_id": run["run_id"],
        "format_note": "Official GT copied unchanged; predictions are YOLOv8n+ByteTrack MOT files; GT is not used as prediction.",
    }
    manifest_path = root / "trackeval_input_manifest.json"
    manifest_path.write_text(json.dumps(input_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(input_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
