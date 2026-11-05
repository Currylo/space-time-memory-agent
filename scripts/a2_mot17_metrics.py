#!/usr/bin/env python3
"""A2: summarize TrackEval's seven-sequence MOT17 results."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RAW = PROJECT_DIR / "data/a2/mot17/trackeval/output/trackeval_raw_results.json"
DEFAULT_OUTPUT = PROJECT_DIR / "reports/a2/mot17"
TRACKER = "a2_yolov8n_bytetrack"


def mean100(values) -> float:
    return float(np.mean(np.asarray(values, dtype=float)) * 100.0)


def summarize_class(metrics: dict) -> dict:
    hota = metrics["HOTA"]
    clear = metrics["CLEAR"]
    identity = metrics["Identity"]
    count = metrics["Count"]
    return {
        "HOTA": mean100(hota["HOTA"]),
        "DetA": mean100(hota["DetA"]),
        "AssA": mean100(hota["AssA"]),
        "DetRe": mean100(hota["DetRe"]),
        "DetPr": mean100(hota["DetPr"]),
        "AssRe": mean100(hota["AssRe"]),
        "AssPr": mean100(hota["AssPr"]),
        "LocA": mean100(hota["LocA"]),
        "HOTA_0": float(hota["HOTA(0)"] * 100.0),
        "LocA_0": float(hota["LocA(0)"] * 100.0),
        "MOTA": float(clear["MOTA"] * 100.0),
        "MOTP": float(clear["MOTP"] * 100.0),
        "MODA": float(clear["MODA"] * 100.0),
        "CLR_Re": float(clear["CLR_Re"] * 100.0),
        "CLR_Pr": float(clear["CLR_Pr"] * 100.0),
        "MTR": float(clear["MTR"] * 100.0),
        "PTR": float(clear["PTR"] * 100.0),
        "MLR": float(clear["MLR"] * 100.0),
        "CLR_TP": int(clear["CLR_TP"]),
        "CLR_FN": int(clear["CLR_FN"]),
        "CLR_FP": int(clear["CLR_FP"]),
        "IDSW": int(clear["IDSW"]),
        "MT": int(clear["MT"]),
        "PT": int(clear["PT"]),
        "ML": int(clear["ML"]),
        "Frag": float(clear["Frag"]),
        "IDF1": float(identity["IDF1"] * 100.0),
        "IDR": float(identity["IDR"] * 100.0),
        "IDP": float(identity["IDP"] * 100.0),
        "IDTP": int(identity["IDTP"]),
        "IDFN": int(identity["IDFN"]),
        "IDFP": int(identity["IDFP"]),
        "Dets": int(count["Dets"]),
        "GT_Dets": int(count["GT_Dets"]),
        "IDs": int(count["IDs"]),
        "GT_IDs": int(count["GT_IDs"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize A2 MOT17 TrackEval results")
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tracker", default=TRACKER)
    args = parser.parse_args()

    raw_path = args.raw.resolve()
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    tracker_data = data["MotChallenge2DBox"][args.tracker]
    combined = summarize_class(tracker_data["COMBINED_SEQ"]["pedestrian"])
    per_sequence = {}
    for sequence, sequence_data in tracker_data.items():
        if sequence == "COMBINED_SEQ":
            continue
        per_sequence[sequence] = summarize_class(sequence_data["pedestrian"])

    result = {
        "schema": "a2-mot17-metrics-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trackeval_raw": str(raw_path.relative_to(PROJECT_DIR)),
        "tracker": args.tracker,
        "sequence_count": len(per_sequence),
        "combined": combined,
        "per_sequence": per_sequence,
        "aggregation": "TrackEval COMBINED_SEQ; HOTA-family means over its 19 alpha thresholds; CLEAR/Identity scalars",
        "limitations": [
            "Frames are extracted from the official MOTChallenge sequence videos, not the original MOT17.zip JPEG frames; the video may be downscaled/re-encoded.",
            "Predictions were scaled back to the original GT coordinate system before TrackEval.",
            "Seven training scenes, FRCNN variant only; not a held-out test set and not field data.",
            "One fixed detector/tracker configuration; no per-sequence parameter tuning.",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    header = [
        "sequence", "HOTA", "DetA", "AssA", "MOTA", "IDF1", "IDSW", "Frag",
        "CLR_TP", "CLR_FN", "CLR_FP", "IDs", "GT_IDs",
    ]
    lines = ["\t".join(header)]
    for sequence, row in [("COMBINED", combined), *sorted(per_sequence.items())]:
        values = [sequence]
        values.extend(str(row[key]) for key in header[1:])
        lines.append("\t".join(values))
    (args.output / "metrics.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
