"""Pose-keypoint geometry: looking, distance band and the face-blur box (docs/stack.md §3).

One pose pass gives everything; there is no separate face or head-pose model.
All functions are pure and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.contract import DistanceBand
from agent.vision.types import Box, Kp, PersonDetection


@dataclass(frozen=True, slots=True)
class GeometryConfig:
    min_kp_confidence: float = 0.5
    # Nose offset from the eye midpoint, as a fraction of the eye distance.
    # Facing the camera ≈ 0; a 45° head turn ≈ 0.5.
    max_nose_offset: float = 0.35
    # Shoulder width as a fraction of frame width (720p, ~90° FOV webcam).
    near_shoulder_fraction: float = 0.22  # ≲ 1.2 m
    far_shoulder_fraction: float = 0.10  # ≳ 3 m
    # Fallback when shoulders aren't visible: box height as a fraction of frame height.
    near_height_fraction: float = 0.85
    far_height_fraction: float = 0.40


DEFAULT_GEOMETRY = GeometryConfig()


def is_looking(person: PersonDetection, config: GeometryConfig = DEFAULT_GEOMETRY) -> bool:
    """True when the face points at the screen: nose and both eyes visible, nose centered."""
    nose = person.kp(Kp.NOSE, config.min_kp_confidence)
    left = person.kp(Kp.LEFT_EYE, config.min_kp_confidence)
    right = person.kp(Kp.RIGHT_EYE, config.min_kp_confidence)
    if nose is None or left is None or right is None:
        return False
    eye_distance = abs(left.x - right.x)
    if eye_distance <= 1e-6:
        return False
    midpoint = (left.x + right.x) / 2
    return abs(nose.x - midpoint) / eye_distance <= config.max_nose_offset


def distance_band(
    person: PersonDetection,
    frame_width: int,
    frame_height: int,
    config: GeometryConfig = DEFAULT_GEOMETRY,
) -> DistanceBand:
    left = person.kp(Kp.LEFT_SHOULDER, config.min_kp_confidence)
    right = person.kp(Kp.RIGHT_SHOULDER, config.min_kp_confidence)
    if left is not None and right is not None and frame_width > 0:
        fraction = abs(left.x - right.x) / frame_width
        if fraction >= config.near_shoulder_fraction:
            return DistanceBand.NEAR
        if fraction <= config.far_shoulder_fraction:
            return DistanceBand.FAR
        return DistanceBand.MID
    height = person.box.height / frame_height if frame_height > 0 else 0.0
    if height >= config.near_height_fraction:
        return DistanceBand.NEAR
    if height <= config.far_height_fraction:
        return DistanceBand.FAR
    return DistanceBand.MID


def head_box(person: PersonDetection, config: GeometryConfig = DEFAULT_GEOMETRY) -> Box:
    """The region to blur before a keyframe leaves the device.

    Built from the face keypoints when visible, padded generously. Without
    keypoints it falls back to the top quarter of the person box, so a face is
    never left unblurred just because pose was uncertain.
    """
    face = [
        point
        for index in (Kp.NOSE, Kp.LEFT_EYE, Kp.RIGHT_EYE, Kp.LEFT_EAR, Kp.RIGHT_EAR)
        if (point := person.kp(index, 0.2)) is not None
    ]
    box = person.box
    if len(face) >= 2:
        xs = [p.x for p in face]
        ys = [p.y for p in face]
        span = max(max(xs) - min(xs), box.width * 0.25)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        half = span * 0.9
        x1, y1 = max(box.x1, cx - half), max(box.y1, cy - half * 1.3)
        x2, y2 = min(box.x2, cx + half), min(box.y2, cy + half * 1.1)
        if x1 < x2 and y1 < y2:
            return Box(x1, y1, x2, y2)
        # Stray face keypoints outside the person box: blur the top of the box instead.
    return Box(box.x1, box.y1, box.x2, box.y1 + box.height * 0.28)
