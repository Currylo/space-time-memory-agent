#!/usr/bin/env python3
"""Publish a separate rules-v3 view from immutable GPU prediction results."""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.unified_rules import UnifiedRules
from st_memory.unified_platform import annotate_causal_stay_events, evidence_window, rule_window


def write(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,separators=(",",":"))+"\n")
    temporary.replace(path)


def main():
    parent = ROOT / "runs/unified-mot20-05-coverage1280-20261105-001"
    result = json.loads((parent/"result.json").read_text())
    run = ROOT / "runs/unified-mot20-05-coverage1280-rulesv3-20261105-003"
    run.mkdir(exist_ok=False)
    result["parent_perception_run"] = result["run_id"]
    result["run_id"] = run.name
    cfg = result["config"]
    cfg.update(version="unified-causal-demo-v3-observable-count",
               track_activation_threshold=.25,new_track_detection_threshold=.35,
               count_observability="detected>0 and tracked=0 is unknown, never an empty-region anchor")
    write(run/"config.json",cfg)
    result["config_sha256"] = hashlib.sha256((run/"config.json").read_bytes()).hexdigest()
    engine = UnifiedRules(cfg, run.name)
    frames = []
    for old in result["frames"]:
        people = copy.deepcopy(old["people"])
        row = engine.update(old["time"],people,.2,count_observable=not(old["detected"]>0 and old["tracked"]==0))
        for key in ("detections","detected","pending_tracks","outside_zone"):
            row[key] = old[key]
        frames.append(row)
    events = annotate_causal_stay_events(engine.events,frames,cfg)
    for event in events:
        event["evidence_start"], event["evidence_end"] = evidence_window(event)
        event["rule_window_start"], event["rule_window_end"] = rule_window(event)
        event["required_coverage_end"] = event["condition_met_video_time"]
    result["frames"], result["candidates"] = frames, events
    result["candidate_counts"] = {k:sum(e["kind"]==k for e in events) for k in ("stay","fall","crowd")}
    result["files"]["analysis"] = copy.deepcopy(result["files"]["original_h264"])
    write(run/"result.json",result)
    write(run/"candidates.json",events)
    baseline = json.loads((ROOT/"demo/data/unified.json").read_text())
    trial = json.loads((ROOT/"runs/unified-mot20-05-coverage1280-start025-20261105-002/result.json").read_text())
    assert all(
        [{k:v for k,v in d.items() if k!='track_id'} for d in a["detections"]]
        == [{k:v for k,v in d.items() if k!='track_id'} for d in b["detections"]]
        for a,b in zip(frames,trial["frames"])
    )
    summary={"selected":result["run_id"],"source_sha256":result["source"]["sha256"],"gt_used":False,
             "sample_count":len(frames),"selected_candidates":result["candidate_counts"],
             "baseline_candidates":baseline["candidate_counts"],"coverage":result["detector_coverage"],
             "tracker_trial":{"new_track_score":.25,"mean_tracked":trial["detector_coverage"]["mean_tracked"],
                 "identical_detections":True,"selected":False,"reason":"small extra coverage, identity quality not evaluated; retain original tracker"},
             "cold_start_count_candidates_excluded":1,"platform_events_created":0,
             "counts_at_times":[{"time":t,"baseline_tracked":baseline["frames"][i]["tracked"],"detected":frames[i]["detected"],
                                 "tracked":frames[i]["tracked"],"inside":frames[i]["count"]} for t in (10,40,60,90,120) for i in [round(t*5)]]}
    write(ROOT/"reports/person-coverage-20261105/comparison.json",summary)
    write(ROOT/"demo/data/unified_coverage1280.json",result)
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == "__main__":
    main()
