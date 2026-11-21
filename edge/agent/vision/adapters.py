"""Concrete detectors: Ultralytics YOLO (local) and OpenCV ArUco markers.

Heavy imports (ultralytics/torch, cv2) happen inside the constructors, so the
rest of the agent and every unit test run without them. Install them with
`uv sync --extra vision` in edge/.

Provider output is translated to `agent.vision.types` here and nowhere else.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from agent.vision.detector import Frame
from agent.vision.types import (
    Box,
    FrameDetections,
    Keypoint,
    MarkerDetection,
    ObjectDetection,
    PersonDetection,
)


class UltralyticsDetector:
    """YOLO26 pose every frame + YOLO26 objects every `object_every` frames (docs/stack.md §3).

    Pass exported weights (TensorRT `.engine`, CoreML `.mlpackage`, OpenVINO
    dir) for speed; plain `.pt` works everywhere but slower.
    """

    def __init__(
        self,
        *,
        pose_model: str = "yolo26n-pose.pt",
        object_model: str | None = "yolo26s.pt",
        object_every: int = 6,
        object_interval: timedelta | None = None,
        imgsz: int = 640,
        conf: float = 0.35,
        device: str | None = None,
        name: str = "local",
        cpu_threads: int | None = None,
    ) -> None:
        from ultralytics import YOLO  # local import: optional dependency

        self._cpu_threads = cpu_threads
        self._pose = YOLO(pose_model)
        self._objects = YOLO(object_model) if object_model else None
        self._object_every = max(1, object_every)
        self._object_interval = object_interval
        self._objects_at: datetime | None = None
        self._imgsz = imgsz
        self._conf = conf
        self._device = device
        self._frame_index = 0
        self._last_objects: tuple[ObjectDetection, ...] = ()
        self._object_shape: tuple[int, int] | None = None
        self.name = name

    def warmup(self) -> None:
        """Initialize both model predictors using a synthetic 720p frame, never a camera."""
        from datetime import UTC

        import numpy as np

        self.detect(np.zeros((720, 1280, 3), dtype=np.uint8), datetime.now(UTC))
        # Warmup must not seed object facts or alter the first live scan deadline.
        self._last_objects = ()
        self._objects_at = None
        self._object_shape = None
        self._frame_index = 0
        self._set_cpu_threads()

    def _set_cpu_threads(self) -> None:
        if self._cpu_threads is not None and self._device in (None, "cpu"):
            import torch

            budget = max(1, self._cpu_threads)
            if torch.get_num_threads() != budget:
                torch.set_num_threads(budget)

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        started = time.perf_counter()
        height, width = frame.shape[:2]
        pose = self._pose.predict(
            frame,
            imgsz=self._imgsz,
            conf=self._conf,
            classes=[0],
            device=self._device,
            verbose=False,
        )[0]
        persons = tuple(_persons(pose))
        due = (
            (self._objects_at is None or measured_at - self._objects_at >= self._object_interval)
            if self._object_interval is not None
            else self._frame_index % self._object_every == 0
        )
        due = due or self._object_shape != (height, width)
        if self._objects is not None and due:
            result = self._objects.predict(
                frame, imgsz=self._imgsz, conf=self._conf, device=self._device, verbose=False
            )[0]
            self._last_objects = tuple(_objects(result))
            self._objects_at = measured_at
            self._object_shape = (height, width)
        self._set_cpu_threads()
        self._frame_index += 1
        return FrameDetections(
            measured_at=measured_at,
            width=int(width),
            height=int(height),
            persons=persons,
            objects=self._last_objects,
            detector=self.name,
            inference_ms=(time.perf_counter() - started) * 1000,
        )


def _persons(result: Any) -> list[PersonDetection]:
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    scores = boxes.conf.cpu().numpy()
    kps = result.keypoints
    kp_xy = kps.xy.cpu().numpy() if kps is not None else None
    kp_conf = kps.conf.cpu().numpy() if kps is not None and kps.conf is not None else None
    people: list[PersonDetection] = []
    for i, (x1, y1, x2, y2) in enumerate(xyxy):
        points: tuple[Keypoint, ...] = ()
        if kp_xy is not None:
            confs = kp_conf[i] if kp_conf is not None else [1.0] * len(kp_xy[i])
            points = tuple(
                Keypoint(float(x), float(y), float(c))
                for (x, y), c in zip(kp_xy[i], confs, strict=False)
            )
        people.append(
            PersonDetection(
                box=Box(float(x1), float(y1), float(x2), float(y2)),
                score=float(scores[i]),
                keypoints=points,
            )
        )
    return people


def _objects(result: Any) -> list[ObjectDetection]:
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    names = result.names
    out: list[ObjectDetection] = []
    for (x1, y1, x2, y2), score, cls in zip(
        boxes.xyxy.cpu().numpy(),
        boxes.conf.cpu().numpy(),
        boxes.cls.cpu().numpy(),
        strict=False,
    ):
        label = str(names[int(cls)])
        if label == "person":
            continue
        out.append(
            ObjectDetection(
                label=label,
                score=float(score),
                box=Box(float(x1), float(y1), float(x2), float(y2)),
            )
        )
    return out


class ArucoMarkerDetector:
    """Detects the printed ArUco markers on demo props (DICT_4X4_50 by default)."""

    def __init__(self, dictionary: str = "DICT_4X4_50") -> None:
        import cv2  # local import: optional dependency

        self._cv2 = cv2
        aruco = cv2.aruco
        self._detector = aruco.ArucoDetector(
            aruco.getPredefinedDictionary(getattr(aruco, dictionary)),
            aruco.DetectorParameters(),
        )
        self.name = "aruco"

    def detect(self, frame: Frame, measured_at: datetime) -> FrameDetections:
        started = time.perf_counter()
        gray = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)
        markers: list[MarkerDetection] = []
        if ids is not None:
            for quad, marker_id in zip(corners, ids.flatten(), strict=False):
                xs = quad[0][:, 0]
                ys = quad[0][:, 1]
                markers.append(
                    MarkerDetection(
                        marker_id=int(marker_id),
                        box=Box(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())),
                    )
                )
        height, width = frame.shape[:2]
        return FrameDetections(
            measured_at=measured_at,
            width=int(width),
            height=int(height),
            markers=tuple(markers),
            detector=self.name,
            inference_ms=(time.perf_counter() - started) * 1000,
        )
