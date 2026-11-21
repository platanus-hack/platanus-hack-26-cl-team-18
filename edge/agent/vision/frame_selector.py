"""Bounded, fail-closed selection of anonymous crops for the warm path."""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from .state import Detection


def _intersection(a: tuple, b: tuple) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def redact_crop(frame: Any, box: tuple, poses: list[tuple[tuple, list]]) -> Any | None:
    """Keep only a single matched person, masking the entire head and upper band.

    Require three confident facial landmarks; ambiguous, overlapping or bent
    views are discarded. No image is emitted when privacy evidence is missing.
    """
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
    if x2 - x1 < 96 or y2 - y1 < 160:
        return None
    crop_box = (x1, y1, x2, y2)
    overlap = [(pb, points) for pb, points in poses if _intersection(crop_box, pb) > 0]
    if not overlap:
        return None
    area = (x2 - x1) * (y2 - y1)
    overlap.sort(key=lambda item: _intersection(crop_box, item[0]), reverse=True)
    pb, points = overlap[0]
    if _intersection(crop_box, pb) < area * 0.5 or len(points) < 3:
        return None
    if any(_intersection(crop_box, other) > area * 0.5 for other, _ in overlap[1:]):
        return None
    face = points[:3]  # nose, left eye, right eye
    if any(p[2] < 0.5 or not x1 <= p[0] < x2 or not y1 <= p[1] < y2 for p in face):
        return None
    h = y2 - y1
    cutoff = max(int(h * 0.45), int(max(p[1] for p in face) - y1 + h * 0.2))
    if cutoff > h * 0.65:
        return None
    crop = frame[y1:y2, x1:x2].copy()
    crop[:cutoff, :] = 0  # opaque redaction, not reversible blur
    for other, _ in overlap[1:]:
        # Hide every overlapping person, with a margin around the pose box.
        pad_x = (other[2] - other[0]) * 0.15
        pad_y = (other[3] - other[1]) * 0.15
        left = max(0, int(other[0] - pad_x - x1))
        top = max(0, int(other[1] - pad_y - y1))
        right = min(x2 - x1, int(other[2] + pad_x - x1))
        bottom = min(h, int(other[3] + pad_y - y1))
        crop[top:bottom, left:right] = 0
    return crop


class FrameSelector:
    def __init__(self, backend: str, screen_id: str, session_id: str, started_at: datetime):
        self.backend, self.screen_id, self.session_id, self.started_at = (
            backend,
            screen_id,
            session_id,
            started_at,
        )
        self.queue: queue.Queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.sent: dict[int, tuple[float, float, int]] = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def offer(self, frame: Any, detections: list[Detection], timestamp: float) -> None:
        import cv2

        h, w = frame.shape[:2]
        for detection in detections:
            if (
                detection.label != "person"
                or detection.track_id is None
                or detection.confidence < 0.6
            ):
                continue
            x1, y1, x2, y2 = (int(v) for v in detection.box)
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
            if x2 - x1 < 96 or y2 - y1 < 160:
                continue
            roi = frame[y1:y2, x1:x2]
            sharpness = min(
                float(cv2.Laplacian(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()), 500
            )
            quality = (x2 - x1) * (y2 - y1) * (1 + sharpness / 500)
            with self.lock:
                previous = self.sent.get(detection.track_id)
            if previous and (
                previous[2] >= 2 or timestamp - previous[1] < 1.0 or quality < previous[0] * 1.5
            ):
                continue
            item = (frame.copy(), detection, timestamp, quality, time.monotonic())
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass
                self.queue.put_nowait(item)
            break

    def _work(self) -> None:
        import cv2
        from ultralytics import YOLO
        from websockets.sync.client import connect

        try:
            pose_model = YOLO("yolo26n-pose.pt")
        except Exception:
            return
        seq = 0
        ws = None
        while not self.stop.is_set():
            try:
                frame, detection, timestamp, quality, queued = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if time.monotonic() - queued > 2:
                continue
            try:
                result = pose_model.predict(frame, verbose=False)[0]
                if result.keypoints is None or result.boxes is None:
                    continue
                poses = []
                for box, keypoints in zip(result.boxes, result.keypoints, strict=True):
                    if keypoints.conf is None:
                        continue
                    coords = keypoints.xy[0].tolist()
                    confs = keypoints.conf[0].tolist()
                    poses.append(
                        (
                            tuple(box.xyxy[0].tolist()),
                            [(p[0], p[1], c) for p, c in zip(coords, confs, strict=True)],
                        )
                    )
                crop = redact_crop(frame, detection.box, poses)
                if crop is None or time.monotonic() - queued > 3:
                    continue
                ok, jpeg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok or len(jpeg) > 2_000_000:
                    continue
                seq += 1
                header = {
                    "type": "audience.keyframe",
                    "v": 1,
                    "screen_id": self.screen_id,
                    "session_id": self.session_id,
                    "track_id": str(detection.track_id),
                    "seq": seq,
                    "measured_at": (self.started_at + timedelta(seconds=timestamp)).isoformat(),
                    "video_time_s": timestamp,
                    "content_type": "image/jpeg",
                    "byte_length": len(jpeg),
                }
                if ws is None:
                    ws = connect(self.backend, open_timeout=1, close_timeout=1)
                ws.send(json.dumps(header))
                ws.send(jpeg.tobytes())
                with self.lock:
                    old = self.sent.get(detection.track_id)
                    self.sent[detection.track_id] = (
                        quality,
                        timestamp,
                        1 if old is None else old[2] + 1,
                    )
            except Exception:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                    ws = None
                continue  # warm-path failures cannot interrupt recognition
        if ws is not None:
            ws.close()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
