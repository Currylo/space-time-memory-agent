#!/usr/bin/env python3
"""Compare platform zone events with a platform-equivalent cumulative self rule.

Frigate 0.18.2 increments a per-track in-zone counter and does not reset it on a
brief exit; `entered_zones` is appended when the counter reaches
`loitering_time * detect.fps`. This script applies the same cumulative 10 s
definition to the already-saved 3 fps self tracks and compares the resulting
events with the same platform event snapshot.

No detections or rules are re-run; this is a definition-aligned re-score of the
existing A2 evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOP_START = 1793785296.5
LOOP_SECONDS = 280.0


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cumulative_events(tracks_path, dwell, sample_fps):
    tracks = defaultdict(list)
    with tracks_path.open() as handle:
        for line in handle:
            row = json.loads(line)
            tracks[row["track_id"]].append(row)
    events = []
    for track_id, rows in tracks.items():
        rows.sort(key=lambda row: row["video_time"])
        cumulative = 0.0
        first_in_zone = None
        last_in_zone = None
        event = None
        for row in rows:
            if not row["in_zone"]:
                continue
            if first_in_zone is None:
                first_in_zone = row["video_time"]
            cumulative += 1.0 / sample_fps
            last_in_zone = row["video_time"]
            if event is None and cumulative >= dwell:
                event = {
                    "track_id": track_id,
                    "start_time": first_in_zone,
                    "trigger_time": row["video_time"],
                    "end_time": last_in_zone,
                    "duration_at_trigger": round(cumulative, 3),
                    "end_reason": "track_end_or_video_end",
                }
                events.append(event)
        if event is not None:
            event["end_time"] = last_in_zone
            event["duration"] = round(event["end_time"] - event["start_time"], 3)
    return events, len(tracks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--platform-events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dwell", type=float, default=10.0)
    parser.add_argument("--sample-fps", type=float, default=3.0)
    parser.add_argument("--overlap", type=float, default=2.0)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)

    events, track_count = cumulative_events(args.tracks, args.dwell, args.sample_fps)
    platform = json.loads(args.platform_events.read_text())
    zone_events = []
    for event in platform:
        start = event.get("start_time")
        if start is None or not (LOOP_START <= start < LOOP_START + LOOP_SECONDS):
            continue
        if "a1_demo_zone" not in (event.get("zones") or []):
            continue
        end = event.get("end_time")
        source_start = start - LOOP_START
        source_end = (min(end, LOOP_START + LOOP_SECONDS) if end is not None else LOOP_START + LOOP_SECONDS) - LOOP_START
        zone_events.append({
            "event_id": event.get("id"),
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "duration": round(source_end - source_start, 3),
            "ongoing": end is None,
        })

    used = set()
    pairs = []
    for platform_event in sorted(zone_events, key=lambda item: item["source_start"]):
        best = None
        for index, self_event in enumerate(events):
            if index in used:
                continue
            overlap = min(platform_event["source_end"], self_event["end_time"]) - max(
                platform_event["source_start"], self_event["start_time"]
            )
            if overlap >= args.overlap and (best is None or overlap > best[0]):
                best = (overlap, index)
        if best is not None:
            used.add(best[1])
            pairs.append({
                "platform_event": platform_event["event_id"],
                "self_track": events[best[1]]["track_id"],
                "overlap_seconds": round(best[0], 3),
            })

    def any_overlap(start, end, intervals):
        return any(min(end, other_end) - max(start, other_start) >= args.overlap for other_start, other_end in intervals)

    self_intervals = [(event["start_time"], event["end_time"]) for event in events]
    platform_intervals = [(event["source_start"], event["source_end"]) for event in zone_events]
    result = {
        "captured_at_utc": datetime.now().astimezone().isoformat(),
        "definition": "cumulative in-zone time, >=10 s, 3 fps, matching Frigate 0.18.2 zone_loitering counter",
        "tracks": track_count,
        "self_cumulative_events": len(events),
        "platform_zone_events": len(zone_events),
        "one_to_one_matches": len(pairs),
        "platform_only": len(zone_events) - len(pairs),
        "self_only": len(events) - len(pairs),
        "platform_events_with_any_self_overlap": sum(any_overlap(p["source_start"], p["source_end"], self_intervals) for p in zone_events),
        "self_events_with_any_platform_overlap": sum(any_overlap(s["start_time"], s["end_time"], platform_intervals) for s in events),
        "overlap_threshold_seconds": args.overlap,
        "pairs": pairs,
        "self_events": events,
        "platform_events": zone_events,
        "caveats": [
            "No independent event ground truth; counts are not precision/recall.",
            "Platform detector/tracker and self YOLOv8n+ByteTrack remain different.",
            "The A1 demo zone covers almost the whole frame, so this is still a flow-level comparison.",
            "The 280 s input is 8 repeats of the same 35 s clip; it is not eight independent samples.",
            "Any-overlap coverage is inflated by long tracks; use the one-to-one counts for interpretation.",
        ],
    }
    write_json(args.output / "cumulative_comparison.json", result)
    print(json.dumps({
        "output": str(args.output),
        "self_cumulative_events": result["self_cumulative_events"],
        "platform_zone_events": result["platform_zone_events"],
        "one_to_one_matches": result["one_to_one_matches"],
        "platform_only": result["platform_only"],
        "self_only": result["self_only"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
