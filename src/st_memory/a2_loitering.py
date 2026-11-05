"""A2 Loitering: trajectory-level feature extraction and rule baselines.

The RS-WACV24 Loitering release provides one row per annotated frame and one
trajectory-level binary label per track id.  Frames were sampled every 25th
frame from 25 fps clips, i.e. approximately one annotation per second.  This
module therefore treats a frame index difference of 1 as 1 second and never
claims event start/end localization from the trajectory-level label.

Primary coordinates are bounding-box centres.  The original paper-style
methods are also computed on the released top-left coordinates so the
difference is explicit.  No IPM coordinates are used for the primary result:
the released `x_transform`/`y_transform` columns contain extreme values (up to
2.0e6 / 1.9e7) that cannot be accepted without a separate calibration audit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull


@dataclass(frozen=True)
class Trajectory:
    track_id: int
    folder: int
    clip: str
    label: int
    frames: np.ndarray
    x: np.ndarray
    y: np.ndarray
    width: np.ndarray
    height: np.ndarray
    occlusion: np.ndarray

    @property
    def center(self) -> np.ndarray:
        return np.column_stack((self.x + self.width / 2.0, self.y + self.height / 2.0))

    @property
    def top_left(self) -> np.ndarray:
        return np.column_stack((self.x, self.y))


def _finite_points(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("points must have shape (n, 2)")
    mask = np.isfinite(points).all(axis=1)
    return points[mask]


def _smooth(points: np.ndarray, window: int = 11, poly: int = 3) -> np.ndarray:
    points = _finite_points(points)
    if len(points) < window:
        return points
    try:
        from scipy.signal import savgol_filter

        return np.column_stack(
            (
                savgol_filter(points[:, 0], window_length=window, polyorder=poly),
                savgol_filter(points[:, 1], window_length=window, polyorder=poly),
            )
        )
    except Exception:
        return points


def _hull_area(points: np.ndarray) -> float:
    points = _finite_points(points)
    if len(points) < 3 or np.ptp(points[:, 0]) == 0 or np.ptp(points[:, 1]) == 0:
        return 0.0
    try:
        return float(ConvexHull(points).volume)
    except Exception:
        return 0.0


def _bbox_area(points: np.ndarray) -> float:
    points = _finite_points(points)
    if len(points) == 0:
        return 0.0
    return float(np.ptp(points[:, 0]) * np.ptp(points[:, 1]))


def _ellipse_area(points: np.ndarray) -> float:
    """Least-squares fitted ellipse area; 0 when the fit is not defined."""

    points = _finite_points(points)
    if len(points) < 5 or np.ptp(points[:, 0]) == 0 or np.ptp(points[:, 1]) == 0:
        return 0.0
    try:
        import cv2

        (_, _), (major, minor), _ = cv2.fitEllipse(points.astype(np.float32))
        if not (math.isfinite(major) and math.isfinite(minor)):
            return 0.0
        if major <= 0 or minor <= 0 or max(major, minor) > 1e4:
            return 0.0
        area = float(math.pi * (major / 2.0) * (minor / 2.0))
        # A fitted ellipse far larger than the 384x288 source frame is a
        # degenerate fit, not a meaningful activity area.
        return area if math.isfinite(area) and area <= 1e6 else 0.0
    except Exception:
        return 0.0


def _angles(points: np.ndarray) -> np.ndarray:
    if len(points) < 3:
        return np.array([], dtype=float)
    vectors = np.diff(points, axis=0)
    norms = np.linalg.norm(vectors, axis=1)
    valid = norms[:-1] > 1e-9
    if not np.any(valid):
        return np.array([], dtype=float)
    cos = np.einsum("ij,ij->i", vectors[:-1], vectors[1:]) / (
        norms[:-1] * norms[1:] + 1e-12
    )
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))[valid]


def _direction_change_count(points: np.ndarray, threshold: float = 25.0) -> int:
    return int(np.count_nonzero(_angles(points) > threshold))


def _reversal_count(points: np.ndarray, angle_threshold: float = 120.0, min_leg_px: float = 10.0) -> int:
    if len(points) < 3:
        return 0
    vectors = np.diff(points, axis=0)
    norms = np.linalg.norm(vectors, axis=1)
    angles = _angles(points)
    count = 0
    for index, angle in enumerate(angles):
        if angle > angle_threshold and norms[index] >= min_leg_px and norms[index + 1] >= min_leg_px:
            count += 1
    return count


def _self_intersections(points: np.ndarray) -> int:
    """Count proper intersections between non-adjacent open-polyline segments."""

    points = _finite_points(points)
    n = len(points)
    if n < 4:
        return 0

    def cross(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
        return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (
            b[..., 1] - a[..., 1]
        ) * (c[..., 0] - a[..., 0])

    count = 0
    for i in range(n - 1):
        a = points[i]
        b = points[i + 1]
        js = np.arange(i + 2, n - 1)
        if len(js) == 0:
            continue
        c = points[js]
        d = points[js + 1]
        o1 = cross(a, b, c)
        o2 = cross(a, b, d)
        o3 = cross(c, d, a)
        o4 = cross(c, d, b)
        intersects = (o1 * o2 < 0) & (o3 * o4 < 0)
        count += int(np.count_nonzero(intersects))
    return count


def _no_motion(points: np.ndarray, radius: float = 5.0, std_threshold: float = 0.1) -> bool:
    points = _finite_points(points)
    if len(points) < 2:
        return False
    center = points.mean(axis=0)
    distances = np.linalg.norm(points - center, axis=1)
    return bool(np.all(distances <= radius) and np.std(distances) <= std_threshold)


def _no_motion_short(
    points: np.ndarray,
    window: int = 60,
    radius: float = 5.0,
    std_threshold: float = 0.1,
) -> bool:
    points = _finite_points(points)
    if len(points) < window:
        return False
    step = max(1, window // 2)
    for start in range(0, len(points) - window + 1, step):
        subset = points[start : start + window]
        center = subset.mean(axis=0)
        distances = np.linalg.norm(subset - center, axis=1)
        if np.all(distances <= radius) and np.std(distances) <= std_threshold:
            return True
    return False


def _stationary_window(points: np.ndarray, window: int = 60, radius: float = 10.0) -> bool:
    points = _finite_points(points)
    if len(points) < window:
        return False
    step = max(1, window // 2)
    for start in range(0, len(points) - window + 1, step):
        subset = points[start : start + window]
        center = subset.mean(axis=0)
        if np.all(np.linalg.norm(subset - center, axis=1) <= radius):
            return True
    return False


def _rectangle_feature_area(points: np.ndarray, angle_threshold: float = 50.0) -> float:
    """Bounding box area of direction-change feature points (repo-style)."""

    points = _finite_points(points)
    if len(points) < 3:
        return 0.0
    angles = _angles(points)
    feature_indices = np.where(angles > angle_threshold)[0] + 1
    if len(feature_indices) < 5:
        return 0.0
    feature_points = points[feature_indices]
    return _bbox_area(feature_points)


def extract_features(trajectory: Trajectory) -> dict:
    points = _finite_points(trajectory.center)
    if len(points) == 0:
        raise ValueError(f"trajectory {trajectory.track_id} has no finite points")
    frames = np.asarray(trajectory.frames, dtype=int)
    order = np.argsort(frames)
    frames = frames[order]
    points = points[order]
    top_left = trajectory.top_left[order]

    duration = float(frames[-1] - frames[0] + 1)
    observed = int(len(frames))
    gaps = float(max(0.0, duration - observed))
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    path_length = float(steps.sum())
    net_displacement = float(np.linalg.norm(points[-1] - points[0]))
    path_efficiency = net_displacement / path_length if path_length > 1e-9 else 0.0
    mean_speed = path_length / duration if duration > 0 else 0.0
    radius = np.linalg.norm(points - points.mean(axis=0), axis=1)

    smoothed = _smooth(points)
    angles = _angles(smoothed)
    direction_changes = int(np.count_nonzero(angles > 25.0))
    reversals = _reversal_count(smoothed)
    self_intersections = _self_intersections(points)

    features = {
        "track_id": int(trajectory.track_id),
        "folder": int(trajectory.folder),
        "clip": str(trajectory.clip),
        "label": int(trajectory.label),
        "n_points": observed,
        "duration_seconds": duration,
        "gap_seconds": gaps,
        "gap_ratio": gaps / duration if duration > 0 else 0.0,
        "path_length_px": path_length,
        "net_displacement_px": net_displacement,
        "path_efficiency": path_efficiency,
        "mean_speed_px_s": mean_speed,
        "step_median_px": float(np.median(steps)) if len(steps) else 0.0,
        "step_p95_px": float(np.percentile(steps, 95)) if len(steps) else 0.0,
        "radius_std_px": float(np.std(radius)),
        "radius_max_px": float(np.max(radius)),
        "radius_p95_px": float(np.percentile(radius, 95)),
        "stay_ratio_5px": float(np.mean(radius <= 5.0)),
        "direction_changes_25deg": direction_changes,
        "reversals_120deg": reversals,
        "self_intersections": self_intersections,
        "bbox_area_px2": _bbox_area(points),
        "hull_area_px2": _hull_area(points),
        "ellipse_area_px2": _ellipse_area(points),
        "rectangle_feature_area_px2": _rectangle_feature_area(smoothed),
        "no_motion_long": int(_no_motion(points)),
        "no_motion_short_60s": int(_no_motion_short(points)),
        "stationary_60s_r10": int(_stationary_window(points)),
        "occlusion_mean": float(np.mean(trajectory.occlusion)),
        "occlusion_max": int(np.max(trajectory.occlusion)),
        "box_area_mean_px2": float(
            np.mean(trajectory.width * trajectory.height)
        ),
        "top_left_hull_area_px2": _hull_area(top_left),
        "top_left_bbox_area_px2": _bbox_area(top_left),
        "top_left_rectangle_feature_area_px2": _rectangle_feature_area(
            _smooth(top_left)
        ),
    }
    return features


RULES = {
    "dwell_60s": {
        "feature": "duration_seconds",
        "direction": "high",
        "fixed_threshold": 60.0,
        "description": "轨迹覆盖时长 >= 60 秒（轨迹级标签，不是事件起止）。",
    },
    "low_path_efficiency_0.3": {
        "feature": "path_efficiency",
        "direction": "low",
        "fixed_threshold": 0.3,
        "description": "路径效率 <= 0.3（净位移/路径长度）。",
    },
    "reversal_3": {
        "feature": "reversals_120deg",
        "direction": "high",
        "fixed_threshold": 3.0,
        "description": "有效方向反转 >= 3 次（转角 > 120°，两腿 >= 10 px）。",
    },
    "hull_area_200": {
        "feature": "hull_area_px2",
        "direction": "low",
        "fixed_threshold": 200.0,
        "description": "轨迹凸包面积 <= 200 px²（徘徊=活动范围小；仓库不同版本书写方向不一致，本实验按语义修正并记录）。",
    },
    "bbox_area_200": {
        "feature": "bbox_area_px2",
        "direction": "low",
        "fixed_threshold": 200.0,
        "description": "轨迹包围盒面积 <= 200 px²（徘徊=活动范围小）。",
    },
    "ellipse_area_200": {
        "feature": "ellipse_area_px2",
        "direction": "low",
        "fixed_threshold": 200.0,
        "description": "拟合椭圆面积 <= 200 px²（徘徊=活动范围小；仓库不同版本书写方向不一致）。",
    },
    "rectangle_feature_area_200": {
        "feature": "rectangle_feature_area_px2",
        "direction": "high",
        "fixed_threshold": 200.0,
        "description": "方向变化特征点包围盒面积 >= 200 px²（论文矩形方法）。",
    },
    "no_motion_long": {
        "feature": "no_motion_long",
        "direction": "high",
        "fixed_threshold": 0.5,
        "binary": True,
        "description": "整条轨迹位于半径 5 px 内且距离标准差 <= 0.1（论文静止方法）。",
    },
    "no_motion_short_60s": {
        "feature": "no_motion_short_60s",
        "direction": "high",
        "fixed_threshold": 0.5,
        "binary": True,
        "description": "60 秒窗口内位于半径 5 px 内且标准差 <= 0.1（论文静止方法）。",
    },
    "stationary_60s_r10": {
        "feature": "stationary_60s_r10",
        "direction": "high",
        "fixed_threshold": 0.5,
        "binary": True,
        "description": "放宽的 60 秒静止窗口：窗口内所有点到中心 <= 10 px。",
    },
    "self_intersection": {
        "feature": "self_intersections",
        "direction": "high",
        "fixed_threshold": 1.0,
        "description": "轨迹存在自交（论文 closed-areas 方法的必要条件）。",
    },
    "closed_area_200": {
        "feature": "self_intersections",
        "direction": "high",
        "fixed_threshold": 1.0,
        "description": "轨迹自交且凸包面积 <= 200 px²（小范围闭环；论文 closed-areas 思路的修正版）。",
    },
}


def apply_composite_rule(frame, rule_name: str) -> int:
    if rule_name == "closed_area_200":
        return int(frame["self_intersections"] >= 1 and frame["hull_area_px2"] <= 200.0)
    if rule_name == "stay_and_low_efficiency":
        return int(frame["duration_seconds"] >= 60 and frame["path_efficiency"] <= 0.3)
    if rule_name == "low_efficiency_and_reversal":
        return int(frame["path_efficiency"] <= 0.3 and frame["reversals_120deg"] >= 3)
    if rule_name == "hull_and_dwell":
        return int(frame["hull_area_px2"] <= 200 and frame["duration_seconds"] >= 60)
    raise KeyError(rule_name)
