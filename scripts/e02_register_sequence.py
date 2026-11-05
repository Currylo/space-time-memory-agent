#!/usr/bin/env python3
"""Register the official MOT17 sequence as an independent public benchmark set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Register one MOT17 public benchmark sequence")
    parser.add_argument("--sequence-dir", type=Path, default=PROJECT_DIR / "data/e02/raw/MOT17-02-FRCNN")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "data/e02/sequence_manifest.json")
    parser.add_argument("--splits", type=Path, default=PROJECT_DIR / "data/e02/splits.json")
    args = parser.parse_args()

    seq_dir = args.sequence_dir.resolve()
    source = json.loads((seq_dir / "source_manifest.json").read_text(encoding="utf-8"))
    ini_path = seq_dir / "seqinfo.ini"
    gt_path = seq_dir / "gt" / "gt.txt"
    image_paths = sorted((seq_dir / "img1").glob("*.jpg"))
    if len(image_paths) != 600:
        raise SystemExit(f"expected 600 images, found {len(image_paths)}")
    expected_names = [f"{index:06d}.jpg" for index in range(1, 601)]
    actual_names = [path.name for path in image_paths]
    if actual_names != expected_names:
        raise SystemExit("image sequence is not contiguous 000001.jpg..000600.jpg")

    rows = list(csv.reader(gt_path.open("r", encoding="utf-8"), delimiter=","))
    if any(len(row) != 9 for row in rows):
        raise SystemExit("GT is not the expected 9-column MOT format")
    frames = sorted({int(row[0]) for row in rows})
    if frames != list(range(1, 601)):
        raise SystemExit("GT frame range is not 1..600")
    class_counts = Counter(int(row[7]) for row in rows)
    conf_counts = Counter(int(float(row[6])) for row in rows)
    pedestrian = [row for row in rows if int(row[7]) == 1 and float(row[6]) > 0]
    pedestrian_ids = defaultdict(set)
    for row in pedestrian:
        pedestrian_ids[int(row[1])].add(int(row[0]))
    if len(pedestrian) != 18581 or len(pedestrian_ids) != 62:
        raise SystemExit("unexpected pedestrian GT count")

    image_manifest = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in image_paths
    ]
    image_manifest_hash = hashlib.sha256(
        json.dumps(image_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    # Read seqinfo without configparser semantics changes.
    ini_values = {}
    for line in ini_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            ini_values[key.strip()] = value.strip()

    manifest = {
        "schema": "e02-public-benchmark-sequence-v1",
        "dataset_id": "mot17_train_mot17-02-frcnn",
        "benchmark": "MOT17",
        "split": "public_benchmark_development",
        "official_split": "train",
        "sequence": "MOT17-02-FRCNN",
        "scene": "MOT17-02",
        "detector_variant": "FRCNN",
        "scene_variant_note": "MOT17-02-DPM/FRCNN/SDP share the same scene, images and GT; this registration counts one scene/sequence, not three independent datasets.",
        "frame_rate_fps": int(ini_values["frameRate"]),
        "frame_count": int(ini_values["seqLength"]),
        "width": int(ini_values["imWidth"]),
        "height": int(ini_values["imHeight"]),
        "gt_rows": len(rows),
        "gt_frame_min": frames[0],
        "gt_frame_max": frames[-1],
        "pedestrian_gt_rows": len(pedestrian),
        "pedestrian_gt_ids": len(pedestrian_ids),
        "gt_class_counts": {str(key): value for key, value in sorted(class_counts.items())},
        "gt_conf_counts": {str(key): value for key, value in sorted(conf_counts.items())},
        "gt_format": "MOTChallenge 9 columns: frame,id,x,y,w,h,conf,class,visibility; pedestrian class=1; conf=1 valid; class 2/4/7/8/9 treated as distractors/ignore by TrackEval",
        "gt_sha256": sha256_file(gt_path),
        "seqinfo_sha256": sha256_file(ini_path),
        "image_manifest_sha256": image_manifest_hash,
        "image_count": len(image_paths),
        "image_manifest": image_manifest,
        "source_manifest": str((seq_dir / "source_manifest.json").relative_to(PROJECT_DIR)),
        "source_manifest_sha256": sha256_file(seq_dir / "source_manifest.json"),
        "source_url": source["source_url"],
        "labels_url": source["labels_url"],
        "labels_sha256": source["labels_sha256"],
        "license": source["license"],
        "license_url": source["license_url"],
        "usage_scope": "public benchmark development evaluation only; not field data; not a held-out test set; no redistribution",
        "public_detections_used_as_predictions": False,
        "ground_truth_used_as_predictions": False,
        "official_gt_preserved": True,
        "local_human_review_claimed": False,
        "notes": [
            "Official MOT17 training sequence used as an independent public benchmark development evaluation.",
            "Only one scene and one detector variant (FRCNN) are registered; DPM/SDP variants are not counted as extra data.",
            "The official GT is preserved unchanged; no local human GT or double-review record is claimed.",
            "This sequence is not a field camera and not an unseen test set.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    splits = {
        "schema": "e02-public-benchmark-splits-v1",
        "description": "One official MOT17 training sequence registered for public benchmark development evaluation only.",
        "sets": {
            "public_benchmark_development": {
                "status": "available",
                "sequences": ["MOT17-02-FRCNN"],
                "source_group": "mot17_train_mot17-02",
                "is_field_data": False,
                "is_held_out_test": False,
                "note": "Development-only public benchmark; do not count as field E02 or as an independent test set.",
            },
            "validation": {"status": "not_established", "sequences": []},
            "test": {"status": "not_established", "sequences": []},
        },
    }
    args.splits.write_text(json.dumps(splits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("dataset_id", "frame_count", "pedestrian_gt_rows", "pedestrian_gt_ids", "gt_sha256", "image_manifest_sha256")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
