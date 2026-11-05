"""Causal, shared-clock demo rules. Inputs are predictions, never human GT."""
from __future__ import annotations

import bisect
import hashlib
import math
import statistics


class UnifiedRules:
    def __init__(self, config: dict, run_id: str):
        self.config = config
        self.run_id = run_id
        self.states = {}
        self.samples = []
        self.times = []
        self.events = []
        self.crowd_active = False
        self.last_time = None

    def _event(self, kind, t, dt, track_id=None, features=None, segment=None):
        key = f"{self.run_id}|{kind}|{track_id}|{segment if segment is not None else t}"
        event = {
            "candidate_id": hashlib.sha256(key.encode()).hexdigest()[:16],
            "kind": kind, "track_id": track_id, "evidence_frame_video_time": round(t, 6),
            "condition_met_video_time": round(t + dt, 6), "review_status": "pending_review",
            "run_id": self.run_id, "source_sha256": self.config["source_sha256"],
            "zone_id": self.config["zone_id"], "rule_version": self.config["version"],
            "features": features or {}, "platform_event_id": None,
        }
        self.events.append(event)
        return event

    def update(self, t: float, people: list[dict], dt: float, *, count_observable: bool = True) -> dict:
        if self.last_time is not None and t <= self.last_time:
            raise ValueError("Frame times must increase strictly")
        if not 0 < dt <= self.config["max_gap_seconds"]:
            raise ValueError("Invalid observed-frame coverage")
        self.last_time = t
        x1, y1, x2, y2 = self.config["roi"]
        count = 0
        pose_valid = 0
        new_events = []
        for person in people:
            track_id = person["id"]
            box = person["box"]  # normalized xyxy
            cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
            inside = x1 <= cx <= x2 and y1 <= box[3] <= y2
            count += int(inside)
            previous = self.states.get(track_id)
            if previous is None or t - previous["last"] > self.config["max_gap_seconds"] + 1e-6:
                previous = {"last": t, "start": t, "inside": False, "dwell": 0., "sent": False,
                            "centers": [], "fall_run": 0., "fall_sent": False}
                self.states[track_id] = previous
            s = previous
            if t - s["last"] > dt * 1.5:
                s["fall_run"] = 0.
            if inside:
                if not s["inside"]:
                    s.update(dwell=0., sent=False, segment=t)
                s["dwell"] += dt
                if not s["sent"] and s["dwell"] + 1e-7 >= self.config["stay_seconds"]:
                    new_events.append(self._event("stay", t, dt, track_id,
                        {"observed_dwell_seconds": round(s["dwell"], 6),
                         "threshold_seconds": self.config["stay_seconds"],
                         "entry_semantics": "first_observed_inside_not_confirmed_entry"}, s["segment"]))
                    s["sent"] = True
            else:
                s.update(dwell=0., sent=False)
            s["inside"] = inside
            if t - s["start"] <= 1.0 + 1e-6:
                s["centers"].append(cy)
            baseline = statistics.median(s["centers"])
            pose = person.get("pose")
            valid = pose is not None and pose["confidence"] >= self.config["fall"]["conf_th"]
            pose_valid += int(valid)
            s["fall_score"] = 0.
            if valid and t - s["start"] >= 1.0:
                f = self.config["fall"]
                aspect = (box[2]-box[0])/max(1e-9, box[3]-box[1])
                drop = cy - baseline
                posture = pose["angle"] < f["angle_th"] and aspect > f["aspect_th"]
                hit = (posture or aspect > f["wide_aspect_th"]) and drop > f["drop_th"]
                s["fall_score"] = float(hit)
                s["fall_run"] = s["fall_run"] + dt if hit else 0.
                required = max(f["min_duration"], f["min_consecutive"] * dt)
                if hit and not s["fall_sent"] and s["fall_run"] + 1e-7 >= required:
                    new_events.append(self._event("fall", t, dt, track_id,
                        {"torso_angle": round(pose["angle"], 3), "pose_confidence": round(pose["confidence"], 4),
                         "aspect": round(aspect, 4), "vertical_drop_ratio": round(drop, 4),
                         "observed_posture_seconds": round(s["fall_run"], 6)}, t - s["fall_run"] + dt))
                    s["fall_sent"] = True
                if not hit:
                    s["fall_sent"] = False
            else:
                s["fall_run"] = 0.
            s["last"] = t
            person["inside"] = inside
            person["dwell"] = round(s["dwell"], 3) if inside else 0.
            person["pose_valid"] = valid

        # Empty observations are explicitly retained with count=0.
        self.times.append(t)
        # A detector observation with no established tracks is not evidence
        # that the region was empty (notably during tracker warm-up).
        self.samples.append(count if count_observable else None)
        crowd = self.config["crowd"]
        ready = t - self.times[0] + 1e-7 >= crowd["window_seconds"]
        crowd_hit = False
        count_window_observable = count_observable
        if ready:
            start = bisect.bisect_right(self.times, t - crowd["window_seconds"] + 1e-7) - 1
            sustain = bisect.bisect_left(self.times, t + dt - crowd["sustain_seconds"] - 1e-7)
            recent_times = self.times[start:]
            gaps_ok = all(b-a <= self.config["max_gap_seconds"] + 1e-7 for a,b in zip(recent_times,recent_times[1:]))
            coverage_ok = self.times[-1] + dt - self.times[sustain] + 1e-7 >= crowd["sustain_seconds"]
            count_window_observable = all(c is not None for c in self.samples[start:])
            crowd_hit = (count_window_observable and gaps_ok and coverage_ok and self.samples[start] < crowd["from_below"]
                         and all(c >= crowd["to_at_least"] for c in self.samples[sustain:]))
            if crowd_hit and not self.crowd_active:
                new_events.append(self._event("crowd", t, dt, features={
                    "window_start": round(t-crowd["window_seconds"], 6), "window_end": round(t, 6),
                    "from_count": self.samples[start], "to_count": count,
                    "sustain_seconds": crowd["sustain_seconds"], "definition": "count_change_proxy_not_confirmed_gathering"}))
        self.crowd_active = crowd_hit
        return {"time": round(t, 6), "count": count, "count_observable": count_observable,
                "count_window_observable": count_window_observable, "pose_valid": pose_valid,
                "tracked": len(people), "crowd_ready": ready,
                "max_dwell": round(max((p["dwell"] for p in people), default=0), 3),
                "people": people, "new_candidates": [e["candidate_id"] for e in new_events]}
