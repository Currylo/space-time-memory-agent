"""Same-model dense-scene detection with overlapping crops and global NMS.

Boxes are observations, never interpolated people. The full-frame branch is
retained so a person spanning crop boundaries can still have a complete box.
"""
from __future__ import annotations


def tile_windows(width: int, height: int, overlap: float = .25) -> list[tuple[int, int, int, int]]:
    if width < 2 or height < 2 or not 0 <= overlap < 1:
        raise ValueError("Invalid image dimensions or overlap")
    tw = min(width, round(width / (2 - overlap)))
    th = min(height, round(height / (2 - overlap)))
    return [(x, y, x + tw, y + th) for y in (0, height - th) for x in (0, width - tw)]


def suppress_duplicates(boxes, scores, iou_threshold: float = .6) -> list[int]:
    """Greedy global IoU NMS after restoring crops to source coordinates."""
    if not 0 < iou_threshold <= 1:
        raise ValueError("Invalid IoU threshold")
    order = sorted(range(len(boxes)), key=lambda i: (-float(scores[i]), i))
    keep = []
    while order:
        chosen, *rest = order
        keep.append(chosen)
        a = boxes[chosen]
        area_a = max(0., a[2] - a[0]) * max(0., a[3] - a[1])
        order = []
        for i in rest:
            b = boxes[i]
            intersection = max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))
            union = area_a + max(0., b[2] - b[0]) * max(0., b[3] - b[1]) - intersection
            if union <= 0 or intersection / union <= iou_threshold:
                order.append(i)
    return keep


def predict_people(model, frame, profile: str, device: str):
    import numpy as np
    import supervision as sv

    if profile not in ("baseline640", "full1280", "tiled960"):
        raise ValueError(f"Unknown detector profile: {profile}")
    full_size = 640 if profile == "baseline640" else 1280
    result = model.predict(frame, imgsz=full_size, conf=.25, iou=.7, classes=[0],
                           max_det=1000, device=device, verbose=False)[0]
    boxes = result.boxes.xyxy.detach().cpu().numpy()
    scores = result.boxes.conf.detach().cpu().numpy()
    before_merge = len(boxes)
    if profile == "tiled960":
        height, width = frame.shape[:2]
        parts, probabilities = [boxes], [scores]
        # Individual calls preserve each crop's exact rectangular preprocessing.
        for x1, y1, x2, y2 in tile_windows(width, height):
            crop = frame[y1:y2, x1:x2]
            prediction = model.predict(crop, imgsz=960, conf=.25, iou=.7, classes=[0],
                                       max_det=1000, device=device, verbose=False)[0]
            xyxy = prediction.boxes.xyxy.detach().cpu().numpy().copy()
            xyxy += np.array([x1, y1, x1, y1], dtype=np.float32)
            parts.append(xyxy)
            probabilities.append(prediction.boxes.conf.detach().cpu().numpy())
        boxes, scores = np.concatenate(parts), np.concatenate(probabilities)
        before_merge = len(boxes)
        keep = suppress_duplicates(boxes, scores)
        boxes, scores = boxes[keep], scores[keep]
    return sv.Detections(xyxy=boxes, confidence=scores, class_id=np.zeros(len(boxes), dtype=int)), before_merge
