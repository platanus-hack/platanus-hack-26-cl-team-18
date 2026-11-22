"""Detector output in our own terms (the anti-corruption layer for YOLO, ArUco, remote GPU).

Every detector adapter (local Ultralytics, the remote GPU service, a replay
fixture) returns `FrameDetections`. Nothing downstream sees a provider type.
Coordinates are pixels in the frame the detector was given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


class Kp(IntEnum):
    """COCO-17 keypoint indexes (YOLO pose models use this order)."""

    NOSE = 0
    LEFT_EYE = 1
    RIGHT_EYE = 2
    LEFT_EAR = 3
    RIGHT_EAR = 4
    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_WRIST = 9
    RIGHT_WRIST = 10


@dataclass(frozen=True, slots=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError("box corners are inverted")

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def expanded(self, fraction: float) -> Box:
        dx, dy = self.width * fraction, self.height * fraction
        return Box(self.x1 - dx, self.y1 - dy, self.x2 + dx, self.y2 + dy)

    def contains(self, point: tuple[float, float]) -> bool:
        x, y = point
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass(frozen=True, slots=True)
class Keypoint:
    x: float
    y: float
    confidence: float


@dataclass(frozen=True, slots=True)
class PersonDetection:
    box: Box
    score: float
    keypoints: tuple[Keypoint, ...] = ()  # COCO-17 order, or empty when the model has no pose

    def kp(self, index: Kp, min_confidence: float) -> Keypoint | None:
        if index >= len(self.keypoints):
            return None
        point = self.keypoints[index]
        return point if point.confidence >= min_confidence else None


@dataclass(frozen=True, slots=True)
class ObjectDetection:
    label: str  # normalized COCO name, e.g. "cup", "bottle", "laptop"
    score: float
    box: Box


@dataclass(frozen=True, slots=True)
class MarkerDetection:
    """A fiducial marker (ArUco) printed on a demo prop. Objects only, never people."""

    marker_id: int
    box: Box


@dataclass(frozen=True, slots=True)
class FrameDetections:
    measured_at: datetime  # when the frame was captured (device clock)
    width: int
    height: int
    persons: tuple[PersonDetection, ...] = ()
    objects: tuple[ObjectDetection, ...] = ()
    markers: tuple[MarkerDetection, ...] = ()
    detector: str = "local"  # "local" | "remote" | "replay": shown in the latency trace
    inference_ms: float = 0.0
    extras: dict[str, float] = field(default_factory=dict)
