#!/usr/bin/env python3
"""Fixed 12-frame engineering comparison; does not claim recall without GT."""
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


def main():
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO
    if not torch.cuda.is_available():
        raise SystemExit("GPU unavailable in this execution context")
    output = ROOT / "reports/person-coverage-20261105/pilot"
    output.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(ROOT / "models/yolov8n.pt"))
    source = cv2.VideoCapture(str(ROOT / "data/a2/unified/MOT20-05-raw.mp4"))
    profiles = ("baseline640", "full1280", "tiled960")
    rows = []
    # Fixed before execution; distributed across the entire sequence.
    times = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 110, 130]
    for t in times:
        source.set(cv2.CAP_PROP_POS_FRAMES, t * 25)
        ok, frame = source.read()
        if not ok:
            raise RuntimeError(f"Cannot decode {t}")
        panels = []
        for profile in profiles:
            started = time.perf_counter()
            det, raw_count = predict_people(model, frame, profile, "0")
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            panel = frame.copy()
            for box in det.xyxy:
                x1, y1, x2, y2 = map(int, box)
                cv2.rectangle(panel, (x1, y1), (x2, y2), (70, 230, 100), 1)
            cv2.rectangle(panel, (0, 0), (826, 32), (12, 30, 40), -1)
            cv2.putText(panel, f"{profile} t={t}s count={len(det)}", (10, 23), cv2.FONT_HERSHEY_SIMPLEX, .65, (255,255,255), 1)
            panels.append(panel)
            rows.append({"time": t, "profile": profile, "detections": len(det), "before_merge": raw_count,
                         "small_boxes_under_60px": int(sum(b[3]-b[1] < 60 for b in det.xyxy)),
                         "wall_seconds": elapsed, "boxes": det.xyxy.tolist(), "scores": det.confidence.tolist()})
        cv2.imwrite(str(output / f"compare-{t:03d}.jpg"), np.concatenate(panels, axis=1))
        print(json.dumps({"time": t, "counts": [x["detections"] for x in rows[-3:]]}), flush=True)
    source.release()
    summary = {p: {"mean_detections": sum(r["detections"] for r in rows if r["profile"]==p)/len(times),
                   "mean_seconds_excluding_first": sum(r["wall_seconds"] for r in rows if r["profile"]==p and r["time"]>0)/(len(times)-1)} for p in profiles}
    (output / "pilot.json").write_text(json.dumps({"times": times, "profiles": profiles, "summary": summary, "rows": rows,
        "scope": "Detection count and visual comparison only; no manual GT or recall evaluation. Threshold .25, same YOLOv8n weights, same source."}, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
