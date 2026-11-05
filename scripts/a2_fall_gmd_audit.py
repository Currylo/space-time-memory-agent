#!/usr/bin/env python3
"""A2/P0: audit GMDCSA-24 CSV labels and build the event table."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(PROJECT_DIR / "src"))
from st_memory.a2_fall import load_gmd_events  # noqa: E402

DEFAULT_DATASET = PROJECT_DIR / "upstream" / "GMDCSA24"
DEFAULT_OUTPUT = PROJECT_DIR / "data" / "a2" / "gmd_csa24"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GMDCSA-24 annotations")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.dataset = args.dataset.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    events = load_gmd_events(args.dataset)
    rows = []
    missing = []
    mismatches = []
    parse_notes = []
    for event in events:
        if not event.video_path.is_file():
            missing.append(str(event.video_path.relative_to(PROJECT_DIR)))
        expected_fall = event.kind == "Fall"
        if expected_fall != event.has_fall:
            mismatches.append(
                {
                    "video_id": event.video_id,
                    "folder": event.kind,
                    "classes": event.classes,
                    "description": event.description,
                }
            )
        if event.parse_note:
            parse_notes.append({"video_id": event.video_id, "note": event.parse_note, "classes": event.classes})
        rows.append(
            {
                "video_id": event.video_id,
                "subject": event.subject,
                "kind": event.kind,
                "file_name": event.file_name,
                "video_path": str(event.video_path.relative_to(PROJECT_DIR)),
                "duration_csv": event.duration_csv,
                "description": event.description,
                "classes": event.classes,
                "has_fall": event.has_fall,
                "fall_intervals": json.dumps(event.fall_intervals, ensure_ascii=False),
                "fall_event_count": len(event.fall_intervals),
                "parse_note": event.parse_note,
            }
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(args.output / "events.csv", index=False)

    per_group = (
        frame.groupby(["subject", "kind"])
        .agg(videos=("video_id", "count"), falls=("has_fall", "sum"), events=("fall_event_count", "sum"))
        .reset_index()
        .to_dict(orient="records")
    )
    interval_lengths = [
        end - start for event in events for start, end in event.fall_intervals
    ]
    summary = {
        "schema": "a2-gmd-csa24-audit-v1",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(args.dataset.relative_to(PROJECT_DIR)),
        "videos_total": len(events),
        "videos_with_fall_label": sum(1 for event in events if event.has_fall),
        "folder_fall_count": sum(1 for event in events if event.kind == "Fall"),
        "folder_adl_count": sum(1 for event in events if event.kind == "ADL"),
        "fall_intervals_total": sum(len(event.fall_intervals) for event in events),
        "fall_interval_seconds": {
            "min": min(interval_lengths) if interval_lengths else None,
            "median": float(pd.Series(interval_lengths).median()) if interval_lengths else None,
            "max": max(interval_lengths) if interval_lengths else None,
        },
        "per_subject_kind": per_group,
        "missing_videos": missing,
        "folder_label_mismatches": mismatches,
        "parse_notes": parse_notes,
        "label_semantics": {
            "level": "video + event intervals",
            "fall_label": "any Classes segment containing 'fall'",
            "interval_unit": "seconds from video start",
            "folder_is_not_label": True,
        },
        "limitations": [
            "CSV durations are integer seconds and may differ slightly from the decoded video duration.",
            "Some Classes strings are malformed; fall segments are parsed by numeric pairs and notes are recorded.",
            "No per-frame boxes/poses are provided by the dataset.",
        ],
    }
    (args.output / "dataset_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "videos_total": summary["videos_total"],
        "videos_with_fall_label": summary["videos_with_fall_label"],
        "folder_fall_count": summary["folder_fall_count"],
        "fall_intervals_total": summary["fall_intervals_total"],
        "missing_videos": len(missing),
        "folder_label_mismatches": len(mismatches),
        "parse_notes": len(parse_notes),
    }, ensure_ascii=False, indent=2))
    if mismatches:
        print(json.dumps(mismatches[:10], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
