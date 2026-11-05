#!/usr/bin/env python3
"""A2: register the seven MOT17 FRCNN training scenes.

The seven distinct MOT17 training scenes are 02/04/05/09/10/11/13.  Each scene
has DPM/FRCNN/SDP variants that share the same images and GT; only FRCNN is
registered so the scene count is not inflated.  Official GT is preserved
unchanged and is never used as a system prediction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SEQUENCES = [
    "MOT17-02-FRCNN",
    "MOT17-04-FRCNN",
    "MOT17-05-FRCNN",
    "MOT17-09-FRCNN",
    "MOT17-10-FRCNN",
    "MOT17-11-FRCNN",
    "MOT17-13-FRCNN",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_seqinfo(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def register_one(sequence_dir: Path) -> dict:
    sequence = sequence_dir.name
    source_path = sequence_dir / "source_manifest.json"
    ini_path = sequence_dir / "seqinfo.ini"
    gt_path = sequence_dir / "gt" / "gt.txt"
    image_paths = sorted((sequence_dir / "img1").glob("*.jpg"))
    if not source_path.is_file():
        raise ValueError(f"{sequence}: missing source_manifest.json")
    if not ini_path.is_file() or not gt_path.is_file():
        raise ValueError(f"{sequence}: missing seqinfo.ini or gt/gt.txt")
    ini = parse_seqinfo(ini_path)
    frame_count = int(ini["seqLength"])
    fps = int(ini["frameRate"])
    width = int(ini["imWidth"])
    height = int(ini["imHeight"])
    if len(image_paths) != frame_count:
        raise ValueError(f"{sequence}: {len(image_paths)} images, expected {frame_count}")
    expected_names = [f"{index:06d}.jpg" for index in range(1, frame_count + 1)]
    if [path.name for path in image_paths] != expected_names:
        raise ValueError(f"{sequence}: images are not contiguous 000001.jpg..{frame_count:06d}.jpg")

    rows = list(csv.reader(gt_path.open("r", encoding="utf-8"), delimiter=","))
    if any(len(row) != 9 for row in rows):
        raise ValueError(f"{sequence}: GT is not 9-column MOT format")
    frames = sorted({int(row[0]) for row in rows})
    if frames != list(range(1, frame_count + 1)):
        raise ValueError(f"{sequence}: GT frames are not 1..{frame_count}")
    class_counts = Counter(int(row[7]) for row in rows)
    conf_counts = Counter(int(float(row[6])) for row in rows)
    pedestrian = [row for row in rows if int(row[7]) == 1 and float(row[6]) > 0]
    pedestrian_ids = defaultdict(set)
    for row in pedestrian:
        pedestrian_ids[int(row[1])].add(int(row[0]))

    # The A2 video-derived variant may be downscaled relative to the original
    # GT.  Record the actual frame size and the axis scale factors explicitly.
    frame_width, frame_height = width, height
    try:
        import cv2

        sample = cv2.imread(str(image_paths[0]))
        if sample is not None:
            frame_height, frame_width = sample.shape[:2]
    except Exception:
        pass

    image_manifest = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in image_paths
    ]
    image_manifest_hash = hashlib.sha256(
        json.dumps(image_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source = json.loads(source_path.read_text(encoding="utf-8"))

    scene = sequence.rsplit("-", 1)[0]
    return {
        "dataset_id": f"mot17_train_{sequence.lower()}",
        "benchmark": "MOT17",
        "split": "public_benchmark_development",
        "official_split": "train",
        "sequence": sequence,
        "scene": scene,
        "detector_variant": "FRCNN",
        "scene_variant_note": "DPM/FRCNN/SDP variants share images and GT; one scene is counted once.",
        "frame_rate_fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "frame_scale_x": width / frame_width,
        "frame_scale_y": height / frame_height,
        "frame_source": "official sequence video (may be downscaled/re-encoded); predictions scaled to GT coordinates"
        if (frame_width, frame_height) != (width, height)
        else "official sequence video at original resolution",
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
        "source_manifest": str(source_path.relative_to(PROJECT_DIR)),
        "source_manifest_sha256": sha256_file(source_path),
        "source_url": source.get("source_url"),
        "labels_url": source.get("labels_url"),
        "labels_sha256": source.get("labels_sha256"),
        "license": source.get("license"),
        "license_url": source.get("license_url"),
        "usage_scope": "public benchmark development evaluation only; not field data; not a held-out test set; no redistribution",
        "public_detections_used_as_predictions": False,
        "ground_truth_used_as_predictions": False,
        "official_gt_preserved": True,
        "local_human_review_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Register MOT17 FRCNN training scenes")
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_DIR / "data/a2/mot17/raw")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "data/a2/mot17/sequences_manifest.json")
    parser.add_argument("--splits", type=Path, default=PROJECT_DIR / "data/a2/mot17/splits.json")
    parser.add_argument("--sequences", nargs="*", default=SEQUENCES)
    args = parser.parse_args()

    sequences = []
    errors = []
    for sequence in args.sequences:
        sequence_dir = args.raw_dir / sequence
        if not sequence_dir.is_dir():
            errors.append({"sequence": sequence, "error": "directory not present"})
            continue
        try:
            sequences.append(register_one(sequence_dir))
        except Exception as exc:
            errors.append({"sequence": sequence, "error": str(exc)})

    manifest = {
        "schema": "a2-mot17-sequences-v1",
        "registered_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": "MOT17",
        "official_split": "train",
        "scene_count": len(sequences),
        "sequence_count": len(sequences),
        "detector_variant": "FRCNN",
        "source_group_note": "Seven distinct training scenes, FRCNN variant only; DPM/SDP are not counted as extra scenes.",
        "sequences": sequences,
        "errors": errors,
        "license": "CC BY-NC-SA 3.0 (official MOTChallenge page); third-party source conditions may also apply",
        "usage_scope": "public benchmark development evaluation only; not field data; not an unseen test set; no redistribution",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    splits = {
        "schema": "a2-mot17-splits-v1",
        "description": "Seven official MOT17 training scenes registered for public benchmark development evaluation only.",
        "sets": {
            "public_benchmark_development": {
                "status": "available" if not errors else "partial",
                "sequences": [item["sequence"] for item in sequences],
                "source_groups": [item["scene"] for item in sequences],
                "is_field_data": False,
                "is_held_out_test": False,
                "note": "Development-only public benchmark; do not count as field E02 or as an independent test set.",
            },
            "validation": {"status": "not_established", "sequences": []},
            "test": {"status": "not_established", "sequences": []},
        },
    }
    args.splits.write_text(json.dumps(splits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "scene_count": len(sequences),
        "frames": sum(item["frame_count"] for item in sequences),
        "pedestrian_gt_rows": sum(item["pedestrian_gt_rows"] for item in sequences),
        "pedestrian_gt_ids": sum(item["pedestrian_gt_ids"] for item in sequences),
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
