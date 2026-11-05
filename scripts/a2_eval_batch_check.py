#!/usr/bin/env python3
"""Check whether the frozen A2 evaluation batch is ready for event metrics."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = ROOT / "data/a2/eval_batch"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/a2/eval_batch_status.json")
    args = parser.parse_args()
    manifest = json.loads((args.batch / "manifest.json").read_text())
    rows = list(csv.DictReader((args.batch / "annotations.csv").open()))
    capabilities = {}
    for row in rows:
        cap = row["capability"]
        item = capabilities.setdefault(cap, {"positive": 0, "negative": 0, "pending_human": 0, "gt": 0})
        item[row["polarity"]] += 1
        if row["annotation_status"].startswith("pending_human"):
            item["pending_human"] += 1
        else:
            item["gt"] += 1
    missing = []
    for cap in ("stay", "fall", "crowd"):
        item = capabilities.get(cap, {})
        if not item.get("positive") or not item.get("negative"):
            missing.append(f"{cap}: needs both positive and negative")
        if item.get("pending_human"):
            missing.append(f"{cap}: {item['pending_human']} annotation(s) pending_human")
    ready = not missing
    result = {
        "batch": str(args.batch),
        "schema": manifest.get("schema"),
        "status": "ready_for_event_metrics" if ready else "not_ready_for_event_metrics",
        "capabilities": capabilities,
        "gathering": manifest.get("frozen_config", {}).get("gathering"),
        "blocking_items": missing,
        "policy": "Model-derived or detection-derived intervals are not human GT; do not compute P/R/F1 or tIoU until review is submitted.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "capabilities": capabilities, "blocking_items": missing}, ensure_ascii=False, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
