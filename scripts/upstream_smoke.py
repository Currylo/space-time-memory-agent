"""Unmodified package APIs, no application adapter. Run from project root.

Reference: https://supervision.roboflow.com/0.25.1/trackers/
Only Supervision associates detections; YOLO is used in predict mode.
"""
import os
from pathlib import Path

os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(".cache/ultralytics").resolve()))
os.environ.setdefault("MPLCONFIGDIR", str(Path(".cache/matplotlib").resolve()))
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
import cv2
import supervision as sv
from ultralytics import YOLO

model = YOLO("models/yolov8n.pt")
info = sv.VideoInfo.from_video_path("data/raw/people-walking.mp4")
tracker = sv.ByteTrack(frame_rate=round(info.fps))
annotator = sv.BoxAnnotator()
labeler = sv.LabelAnnotator()
frames = 0
points = 0
Path("runs/upstream_smoke").mkdir(parents=True, exist_ok=True)
with sv.VideoSink("runs/upstream_smoke/replay.mp4", info) as sink:
    for frame in sv.get_video_frames_generator("data/raw/people-walking.mp4", end=60):
        result = model(frame, device=0, classes=[0], conf=0.1, verbose=False)[0]
        detections = tracker.update_with_detections(sv.Detections.from_ultralytics(result))
        labels = [f"#{i}" for i in detections.tracker_id]
        canvas = annotator.annotate(frame.copy(), detections)
        canvas = labeler.annotate(canvas, detections, labels)
        sink.write_frame(canvas)
        frames += 1
        points += len(detections)
print({"frames": frames, "tracked_points": points, "tracker": "supervision.ByteTrack"})
