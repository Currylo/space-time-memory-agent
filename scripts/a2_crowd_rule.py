#!/usr/bin/env python3
"""Crowd/occupancy change rule scaffold with explicit data-sufficiency guard.

Frozen first definition (public demo only, not field-calibrated):
  object = person (local track, not unique identity)
  zone = explicit normalized polygon
  window = 60 continuous seconds
  condition = count < 3 at window start and count >= 6 at window end,
              sustained for 2 seconds
  gap rule = split/refuse if observation gap > 0.4 s

The 35 s public clip and its 8x-repeated 280 s concatenation are NOT accepted as
independent 60 s scenes. With insufficient data the script writes
`not_evaluated` rather than shortening the window or fabricating an event.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

ZONE = [(0.05, 0.05), (0.95, 0.05), (0.95, 0.95), (0.05, 0.95)]


def inside(x, y, polygon):
    result = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-duration", type=float, required=True)
    parser.add_argument("--repeated-source", action="store_true")
    parser.add_argument("--window", type=float, default=60.0)
    parser.add_argument("--from-below", type=int, default=3)
    parser.add_argument("--to-at-least", type=int, default=6)
    parser.add_argument("--sustain", type=float, default=2.0)
    parser.add_argument("--max-gap", type=float, default=0.4)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)
    definitions = {
        "object": "person_local_track_not_unique_identity",
        "zone": "a1_demo_zone",
        "zone_normalized": ZONE,
        "window_seconds": args.window,
        "from_below": args.from_below,
        "to_at_least": args.to_at_least,
        "sustain_seconds": args.sustain,
        "max_gap_seconds": args.max_gap,
    }
    if args.repeated_source:
        result = {
            "status": "not_evaluated",
            "reason": "source_is_repeated_35s_clip; not independent 60s observation",
            "definitions": definitions,
            "source_duration_seconds": args.source_duration,
        }
    elif args.source_duration < args.window:
        result = {
            "status": "not_evaluated",
            "reason": f"continuous source {args.source_duration}s is shorter than {args.window}s window",
            "definitions": definitions,
            "source_duration_seconds": args.source_duration,
        }
    else:
        samples = defaultdict(set)
        times = []
        last_time = None
        max_gap = 0.0
        with args.tracks.open() as handle:
            for line in handle:
                row = json.loads(line)
                t = row["video_time"]
                x = (row["bbox_xyxy"][0] + row["bbox_xyxy"][2]) / 2 / 960
                y = row["bbox_xyxy"][3] / 540
                times.append(t)
                if last_time is not None:
                    max_gap = max(max_gap, t - last_time)
                last_time = t
                if inside(x, y, ZONE):
                    bucket = round(t * 3) / 3
                    samples[bucket].add(row["track_id"])
        full = sorted(samples)
        counts = {t: len(samples[t]) for t in full}

        def count_at(time_value):
            if not full:
                return 0
            import bisect
            index = bisect.bisect_left(full, time_value)
            candidates = [i for i in (index - 1, index) if 0 <= i < len(full)]
            nearest = min(candidates, key=lambda i: abs(full[i] - time_value))
            return counts[full[nearest]] if abs(full[nearest] - time_value) <= 0.5 else 0

        events = []
        if max_gap <= args.max_gap and full and full[-1] - full[0] >= args.window:
            for t in full:
                start = t - args.window
                if count_at(start) < args.from_below and count_at(t) >= args.to_at_least:
                    sustained = all(count_at(t + dt) >= args.to_at_least for dt in (0.0, args.sustain))
                    if sustained:
                        events.append({"start": round(start, 3), "end": round(t, 3),
                                       "from": count_at(start), "to": count_at(t)})
        result = {
            "status": "evaluated" if max_gap <= args.max_gap else "not_evaluated",
            "reason": "continuous observation meets guard" if max_gap <= args.max_gap else "observation gap exceeds guard",
            "definitions": definitions,
            "source_duration_seconds": args.source_duration,
            "observed_span_seconds": round(full[-1] - full[0], 3) if full else 0,
            "max_observation_gap_seconds": round(max_gap, 3),
            "events": events,
        }
    result["created_at"] = datetime.now().astimezone().isoformat()
    (args.output / "crowd_rule_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "reason", "source_duration_seconds")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
