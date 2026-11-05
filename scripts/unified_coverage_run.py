#!/usr/bin/env python3
"""Versioned dense-scene run; no old files, platform events or ledger writes."""
from collections import Counter
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
sys.path.insert(0, str(ROOT / "src"))
from st_memory.person_coverage import predict_people
from st_memory.unified_rules import UnifiedRules


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024*1024), b""):
            h.update(b)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"))+"\n")


def main():
    import cv2
    import numpy as np
    import torch
    import supervision as sv
    from ultralytics import YOLO
    if not torch.cuda.is_available():
        raise SystemExit("Run in the GPU-enabled host context")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="unified-mot20-05-coverage1280-20261105-001")
    parser.add_argument("--track-activation", type=float, default=.25)
    args = parser.parse_args()
    run_id = args.run_id
    run = ROOT / "runs" / run_id
    run.mkdir(exist_ok=False)
    baseline = json.loads((ROOT / "demo/data/unified.json").read_text())
    cfg = copy.deepcopy(baseline["config"])
    cfg.update(imgsz=1280, detector_profile="full1280", confidence=.25, nms_iou=.7, max_det=1000,
               detector_change="YOLOv8n input 640 -> 1280; same weights/confidence/tracker/5 FPS/rules/ROI",
               pose_imgsz=640, overlay="canvas_source_coordinates",
               track_activation_threshold=args.track_activation,
               new_track_detection_threshold=round(args.track_activation+.1, 6),
               minimum_consecutive_frames=2)
    source = ROOT / baseline["source"]["path"]
    assert digest(source) == cfg["source_sha256"]
    assert digest(ROOT / "models/yolov8n.pt") == cfg["detector_weights_sha256"]
    write(run / "config.json", cfg)
    detector, pose_model = YOLO(str(ROOT / "models/yolov8n.pt")), YOLO(str(ROOT / "models/yolov8n-pose.pt"))
    tracker = sv.ByteTrack(frame_rate=5, minimum_consecutive_frames=2,
                           track_activation_threshold=args.track_activation)
    engine = UnifiedRules(cfg, run_id)
    cap = cv2.VideoCapture(str(source))
    width, height, fps = baseline["source"]["width"], baseline["source"]["height"], baseline["source"]["fps"]
    frames, raw_counts, all_ids = [], [], set()
    started = time.perf_counter()
    index = 0
    with (run / "observations.jsonl").open("w") as out:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % 5 == 0:
                det, _ = predict_people(detector, frame, "full1280", "0")
                pose_result = pose_model.predict(frame, imgsz=640, conf=.25, classes=[0], device="0", verbose=False)[0]
                poses = np.zeros((len(det), 17, 3), dtype=np.float32)
                if len(det) and len(pose_result.boxes):
                    xy = pose_result.keypoints.xy.detach().cpu().numpy()
                    conf = pose_result.keypoints.conf.detach().cpu().numpy()
                    kp = np.concatenate((xy, conf[:,:,None]), axis=2)
                    ious = sv.box_iou_batch(det.xyxy, pose_result.boxes.xyxy.detach().cpu().numpy())
                    pairs = sorted([(float(ious[a,b]),a,b) for a in range(len(det)) for b in range(len(kp)) if ious[a,b]>=.5], reverse=True)
                    used_a, used_b = set(), set()
                    for _, a, b in pairs:
                        if a not in used_a and b not in used_b:
                            poses[a] = kp[b]
                            used_a.add(a); used_b.add(b)
                det.data["pose"] = poses
                tracked = tracker.update_with_detections(det)
                people = []
                normalization = np.array([width,height,width,height])
                for i, box in enumerate(tracked.xyxy):
                    tid = int(tracked.tracker_id[i]); all_ids.add(tid)
                    person = {"id":tid,"box":[round(float(v),6) for v in box/normalization],
                              "confidence":round(float(tracked.confidence[i]),4),"pose":None}
                    points = tracked.data["pose"][i]
                    if points[[5,6,11,12],2].max() > 0:
                        vector = (points[5,:2]+points[6,:2]-points[11,:2]-points[12,:2])/2
                        person["pose"] = {"angle":float(np.degrees(np.arctan2(abs(vector[1]),abs(vector[0])+1e-6))),
                                          "confidence":float(np.mean(points[[5,6,11,12],2]))}
                    people.append(person)
                t = index/fps
                row = engine.update(t, people, min(.2, baseline["source"]["duration"]-t))
                assigned = {}
                if len(det) and len(tracked):
                    ious = sv.box_iou_batch(det.xyxy, tracked.xyxy)
                    for i in range(len(det)):
                        j = int(ious[i].argmax())
                        if ious[i,j] > .999:
                            assigned[i] = int(tracked.tracker_id[j])
                row["detections"] = [{"box":[round(float(v),6) for v in box/normalization],
                                       "confidence":round(float(det.confidence[i]),4),"track_id":assigned.get(i)} for i,box in enumerate(det.xyxy)]
                row["detected"] = len(det)
                row["pending_tracks"] = len(det)-len(tracked)
                row["outside_zone"] = len(tracked)-row["count"]
                raw_counts.append(len(det)); frames.append(row)
                out.write(json.dumps(row,separators=(",",":"))+"\n")
                if len(frames) % 100 == 0:
                    print(json.dumps({"sample":len(frames),"time":t,"detected":len(det),"tracked":len(tracked),"candidates":len(engine.events)}),flush=True)
            index += 1
    cap.release()
    assert index == baseline["source"]["frame_count"] and len(frames) == 663
    counts = Counter(e["kind"] for e in engine.events)
    result = copy.deepcopy(baseline)
    result.update(run_id=run_id,created_at_utc=datetime.now(timezone.utc).isoformat(),config=cfg,
                  config_sha256=digest(run/"config.json"),sample_count=len(frames),track_count=len(all_ids),
                  wall_seconds=time.perf_counter()-started,frames=frames,candidates=engine.events,
                  candidate_counts={kind:counts[kind] for kind in ("stay","fall","crowd")},
                  platform_status="new_detection_run_not_submitted",overlay_mode="canvas",
                  detector_coverage={"mean_detected":sum(raw_counts)/len(frames),"mean_tracked":sum(r["tracked"] for r in frames)/len(frames),
                                     "mean_inside":sum(r["count"] for r in frames)/len(frames),
                                     "baseline_mean_tracked":sum(r["tracked"] for r in baseline["frames"])/len(baseline["frames"]),
                                     "not_a_recall_measurement":True})
    # The existing original file is a frame-aligned H.264 derivative, not a
    # second scene. Canvas draws actual observations at source coordinates.
    result["files"]["analysis"] = copy.deepcopy(baseline["files"]["original_h264"])
    result["limits"] += ["1280 improves visible coverage but is not a guarantee of all people or accurate identities.",
                          "New candidates are not the old 18 platform events; choose baseline to inspect the accepted platform evidence.",
                          "Pose branch remains 640; extra person boxes do not imply usable fall poses."]
    write(run/"candidates.json",engine.events)
    write(run/"result.json",result)
    # Publishing is a separate, validated rules-v3 step. A new perception
    # experiment must never silently replace the active accepted view.
    print(json.dumps({"run_id":run_id,"candidate_counts":result["candidate_counts"],"coverage":result["detector_coverage"],
                      "wall_seconds":result["wall_seconds"]},ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
