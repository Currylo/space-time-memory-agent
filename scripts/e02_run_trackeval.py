#!/usr/bin/env python3
"""Run the official TrackEval MOT17 evaluator through its Python API.

TrackEval commit 12c8791 predates NumPy 1.24 and uses np.float/np.int.
The shim only restores those aliases; all metric code and configs remain
TrackEval's own.  This script does not compute any metric itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
TRACKEVAL_DIR = PROJECT_DIR / "upstream" / "TrackEval"
DEFAULT_ROOT = PROJECT_DIR / "data" / "e02" / "trackeval"
DEFAULT_TRACKER = "e02_yolov8n_bytetrack"
TRACKEVAL_COMMIT = "12c8791b303e0a0b50f753af204249e622d0281a"

for name, value in (("float", float), ("int", int), ("bool", bool)):
    if not hasattr(np, name):
        setattr(np, name, value)

sys.path.insert(0, str(TRACKEVAL_DIR))
import trackeval  # noqa: E402


def to_jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run official TrackEval API for one MOT17 tracker")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--tracker-name", default=DEFAULT_TRACKER)
    parser.add_argument(
        "--seqmap",
        default="MOT17-train-single.txt",
        help="Seqmap filename under <root>/seqmaps (default keeps the E02 single-sequence behaviour)",
    )
    args = parser.parse_args()
    if not (TRACKEVAL_DIR / "trackeval" / "eval.py").is_file():
        raise SystemExit(f"TrackEval checkout not found: {TRACKEVAL_DIR}")
    ROOT = args.root.resolve()
    OUTPUT = ROOT / "output"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    eval_config = {
        "USE_PARALLEL": False,
        "NUM_PARALLEL_CORES": 1,
        "BREAK_ON_ERROR": True,
        "RETURN_ON_ERROR": False,
        "LOG_ON_ERROR": str(OUTPUT / "trackeval_error.log"),
        "PRINT_RESULTS": True,
        "PRINT_ONLY_COMBINED": False,
        "PRINT_CONFIG": True,
        "TIME_PROGRESS": True,
        "DISPLAY_LESS_PROGRESS": False,
        "OUTPUT_SUMMARY": True,
        "OUTPUT_EMPTY_CLASSES": True,
        "OUTPUT_DETAILED": True,
        "PLOT_CURVES": False,
    }
    dataset_config = {
        "GT_FOLDER": str(ROOT / "gt"),
        "TRACKERS_FOLDER": str(ROOT / "trackers"),
        "OUTPUT_FOLDER": str(OUTPUT),
        "TRACKERS_TO_EVAL": [args.tracker_name],
        "CLASSES_TO_EVAL": ["pedestrian"],
        "BENCHMARK": "MOT17",
        "SPLIT_TO_EVAL": "train",
        "INPUT_AS_ZIP": False,
        "PRINT_CONFIG": True,
        "DO_PREPROC": True,
        "TRACKER_SUB_FOLDER": "data",
        "OUTPUT_SUB_FOLDER": "",
        "TRACKER_DISPLAY_NAMES": None,
        "SEQMAP_FOLDER": None,
        "SEQMAP_FILE": str(ROOT / "seqmaps" / args.seqmap),
        "SEQ_INFO": None,
        "GT_LOC_FORMAT": "{gt_folder}/{seq}/gt/gt.txt",
        "SKIP_SPLIT_FOL": False,
    }
    metrics_config = {"METRICS": ["HOTA", "CLEAR", "Identity"], "THRESHOLD": 0.5, "PRINT_CONFIG": True}
    command_record = {
        "trackeval_commit": TRACKEVAL_COMMIT,
        "trackeval_dir": str(TRACKEVAL_DIR.relative_to(PROJECT_DIR)),
        "root": str(ROOT.relative_to(PROJECT_DIR)),
        "tracker_name": args.tracker_name,
        "api": "trackeval.Evaluator.evaluate([MotChallenge2DBox], [HOTA, CLEAR, Identity])",
        "numpy_compat_aliases": ["np.float=float", "np.int=int", "np.bool=bool"],
        "eval_config": eval_config,
        "dataset_config": dataset_config,
        "metrics_config": metrics_config,
        "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (OUTPUT / "trackeval_command.json").write_text(json.dumps(command_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("TrackEval commit:", TRACKEVAL_COMMIT)
    print("GT:", dataset_config["GT_FOLDER"], flush=True)
    print("Predictions:", dataset_config["TRACKERS_FOLDER"], flush=True)
    evaluator = trackeval.Evaluator(eval_config)
    dataset = trackeval.datasets.MotChallenge2DBox(dataset_config)
    metrics = [
        trackeval.metrics.HOTA(metrics_config),
        trackeval.metrics.CLEAR(metrics_config),
        trackeval.metrics.Identity(metrics_config),
    ]
    results, messages = evaluator.evaluate([dataset], metrics)
    (OUTPUT / "trackeval_raw_results.json").write_text(json.dumps(to_jsonable(results), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "trackeval_messages.json").write_text(json.dumps(to_jsonable(messages), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("raw results:", OUTPUT / "trackeval_raw_results.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
