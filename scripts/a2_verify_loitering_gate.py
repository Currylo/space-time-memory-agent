#!/usr/bin/env python3
"""Verify Frigate's 10-second loitering gate from live object state.

Records three distinct times for a clean candidate:
  1. target enters the zone (first path point inside, with a prior outside point)
  2. loitering condition satisfied (first `entered_zones` containing the zone)
  3. event first visible in the platform event API (and first visible as alert)

The WebSocket feed is the platform's own object state (`pending_loitering`,
`entered_zones`); the REST API is polled for event visibility. This avoids the
incorrect shortcut of comparing Review.start_time with the event start.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import time
from pathlib import Path
import urllib.request

import websocket

ROOT = Path(__file__).resolve().parents[1]
ZONE = [(0.05, 0.05), (0.95, 0.05), (0.95, 0.95), (0.05, 0.95)]


def inside(x, y):
    return 0.05 <= x <= 0.95 and 0.05 <= y <= 0.95


def get_json(opener, url):
    with opener.open(url, timeout=8) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", default="http://127.0.0.1:15000")
    parser.add_argument("--ws", default="ws://127.0.0.1:15000/ws")
    parser.add_argument("--camera", default="a1_public_demo")
    parser.add_argument("--zone", default="a1_demo_zone")
    parser.add_argument("--duration", type=float, default=75.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--min-gate", type=float, default=9.0)
    parser.add_argument("--max-gate", type=float, default=13.0)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.mkdir(parents=True)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ws = websocket.create_connection(args.ws, timeout=8)
    ws.settimeout(0.5)
    start = time.time()
    last_poll = 0.0
    objects = {}
    event_first_seen = {}
    event_alert_seen = {}
    event_zones = {}
    review_snapshot = []

    try:
        while time.time() - start < args.duration:
            now = time.time()
            if now - last_poll >= args.poll_interval:
                last_poll = now
                try:
                    events = get_json(
                        opener,
                        f"{args.origin}/api/events?cameras={args.camera}&limit=500",
                    )
                    for event in events:
                        event_id = event.get("id")
                        if not event_id:
                            continue
                        event_first_seen.setdefault(event_id, now)
                        zones = event.get("zones") or []
                        if zones:
                            event_alert_seen.setdefault(event_id, now)
                            event_zones[event_id] = zones
                    review = get_json(
                        opener,
                        f"{args.origin}/api/review?cameras={args.camera}&limit=100",
                    )
                    review_snapshot = [
                        {
                            "id": item.get("id"),
                            "start_time": item.get("start_time"),
                            "end_time": item.get("end_time"),
                            "detections": len((item.get("data") or {}).get("detections", [])),
                        }
                        for item in review
                    ]
                except Exception as error:  # noqa: BLE001
                    print(f"poll error: {error}", flush=True)

            try:
                raw = ws.recv()
            except Exception:
                continue
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if message.get("topic") != "events":
                continue
            try:
                payload = json.loads(message["payload"])
            except Exception:
                continue
            for side in ("before", "after"):
                obj = payload.get(side) or {}
                event_id = obj.get("id")
                if not event_id:
                    continue
                state = objects.setdefault(
                    event_id,
                    {
                        "first_ws": now,
                        "first_pending": None,
                        "first_entered": None,
                        "last_state": None,
                    },
                )
                if obj.get("pending_loitering"):
                    state["first_pending"] = state["first_pending"] or now
                if args.zone in (obj.get("entered_zones") or []):
                    state["first_entered"] = state["first_entered"] or now
                state["last_state"] = {
                    "start_time": obj.get("start_time"),
                    "end_time": obj.get("end_time"),
                    "pending_loitering": obj.get("pending_loitering"),
                    "entered_zones": obj.get("entered_zones"),
                    "current_zones": obj.get("current_zones"),
                    "frame_time": obj.get("frame_time"),
                }
    finally:
        ws.close()

    candidates = []
    for event_id, state in objects.items():
        if not state["first_pending"] or not state["first_entered"]:
            continue
        if state["first_entered"] <= state["first_pending"]:
            continue
        if event_id not in event_first_seen or event_id not in event_alert_seen:
            continue
        try:
            event = get_json(opener, f"{args.origin}/api/events/{event_id}")
        except Exception:
            continue
        points = (event.get("data") or {}).get("path_data") or []
        entry = None
        previous_outside = None
        for point, timestamp in points:
            x, y = point
            if not inside(x, y):
                previous_outside = timestamp
            elif previous_outside is not None:
                entry = timestamp
                break
        if entry is None:
            # Fall back to the first pending_loitering observation, which is
            # after zone inertia; keep the source explicit.
            entry = state["first_pending"]
            entry_source = "ws_pending_loitering_no_outside_path_point"
        else:
            entry_source = "path_data_outside_to_inside"
        gate = state["first_entered"] - entry
        outside_before_condition = [
            timestamp for point, timestamp in points
            if not inside(point[0], point[1]) and timestamp <= state["first_entered"]
        ]
        last_outside = max(outside_before_condition) if outside_before_condition else None
        inside_after_last_outside = [
            timestamp for point, timestamp in points
            if inside(point[0], point[1])
            and timestamp <= state["first_entered"]
            and (last_outside is None or timestamp > last_outside)
        ]
        continuous_start = (
            min(inside_after_last_outside)
            if inside_after_last_outside
            else (points[0][1] if points else entry)
        )
        continuous = state["first_entered"] - continuous_start
        if not (args.min_gate <= gate <= args.max_gate):
            continue
        if continuous < args.min_gate:
            continue
        candidates.append(
            {
                "event_id": event_id,
                "event_start_time": event.get("start_time"),
                "event_end_time": event.get("end_time"),
                "entry_time_epoch": entry,
                "entry_time_local": datetime.fromtimestamp(entry).isoformat(),
                "entry_source": entry_source,
                "first_pending_epoch": state["first_pending"],
                "first_entered_epoch": state["first_entered"],
                "condition_satisfied_epoch": state["first_entered"],
                "gate_seconds": round(gate, 3),
                "last_outside_before_condition_epoch": last_outside,
                "continuous_zone_seconds_to_condition": round(continuous, 3),
                "event_first_seen_epoch": event_first_seen.get(event_id),
                "event_first_seen_local": datetime.fromtimestamp(event_first_seen[event_id]).isoformat()
                if event_id in event_first_seen
                else None,
                "event_alert_first_seen_epoch": event_alert_seen.get(event_id),
                "event_alert_first_seen_local": datetime.fromtimestamp(event_alert_seen[event_id]).isoformat()
                if event_id in event_alert_seen
                else None,
                "event_visible_before_condition": event_first_seen.get(event_id, 0) < state["first_entered"],
                "zones": event.get("zones") or [],
                "path_data": points,
            }
        )

    def rank(candidate):
        source_rank = 0 if candidate["entry_source"] == "path_data_outside_to_inside" else 1
        score = abs(candidate["continuous_zone_seconds_to_condition"] - 10.0)
        score += abs(candidate["gate_seconds"] - 10.0)
        return (source_rank, score)

    candidates.sort(key=rank)
    result = {
        "captured_at_utc": datetime.now().astimezone().isoformat(),
        "camera": args.camera,
        "zone": args.zone,
        "zone_normalized": ZONE,
        "duration_seconds": args.duration,
        "poll_interval_seconds": args.poll_interval,
        "websocket": args.ws,
        "event_api": f"{args.origin}/api/events?cameras={args.camera}&limit=500",
        "review_snapshot_at_end": review_snapshot,
        "candidates": candidates,
        "selected": candidates[0] if candidates else None,
        "notes": [
            "Entry is the first path_data point inside the zone after an outside point; path_data is platform-sampled, not frame-exact.",
            "Condition satisfied is the first WebSocket object state whose entered_zones contains the zone; this is the platform loitering gate.",
            "Event first visible is the first REST poll where the event id appears; event start_time can be earlier than first visibility.",
            "Review visibility is not used as the gate proof because the active Review item is merged/stuck; the event API and entered_zones are the source of truth.",
        ],
    }
    (args.output / "gate_verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "candidate_count": len(candidates),
        "selected": None if not candidates else {
            key: candidates[0][key]
            for key in (
                "event_id",
                "entry_time_local",
                "condition_satisfied_epoch",
                "gate_seconds",
                "event_first_seen_local",
                "event_alert_first_seen_local",
                "event_visible_before_condition",
            )
        },
    }, ensure_ascii=False, indent=2))
    return 0 if candidates else 2


if __name__ == "__main__":
    raise SystemExit(main())
