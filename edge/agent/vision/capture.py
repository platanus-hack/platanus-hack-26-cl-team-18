"""Camera capture with a single latest-frame slot, a video-file replay source, and face blur.

Frames are never queued: the capture thread overwrites the slot, so inference
always gets the newest frame and a slow detector drops frames instead of
falling behind (docs/stack.md §3). OpenCV is imported lazily.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.vision.types import Box


class LatestFrameCamera:
    """UVC webcam: 1280x720 @ 30 fps MJPG, buffer of 1, newest frame only."""

    def __init__(
        self, index: int = 0, *, width: int = 1280, height: int = 720, fps: int = 30
    ) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(index)
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self._cap.isOpened():
            raise RuntimeError(f"camera {index} could not be opened")
        self._lock = threading.Lock()
        self._latest: tuple[Any, datetime] | None = None
        self._serial = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._lock:
                self._latest = (frame, datetime.now(UTC))
                self._serial += 1

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        """The newest frame if it is newer than `after_serial`, else None."""
        with self._lock:
            if self._latest is None or self._serial <= after_serial:
                return None
            frame, measured_at = self._latest
            return frame, measured_at, self._serial

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._cap.release()


class VideoFileSource:
    """Replay mode (docs/demo.md §4): a recorded video file stands in for the webcam.

    Paced at the file's frame rate and looped, with `measured_at` stamped at
    read time, so the backend sees exactly what a live camera would send.
    """

    def __init__(self, path: str, *, loop: bool = True) -> None:
        import cv2

        self._cv2 = cv2
        self._path = path
        self._loop = loop
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"video {path} could not be opened")
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._interval = 1.0 / fps
        self._next_at = time.monotonic()
        self._serial = 0

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        now = time.monotonic()
        if now < self._next_at:
            return None
        ok, frame = self._cap.read()
        if not ok:
            if not self._loop:
                return None
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            if not ok:
                return None
        self._next_at = max(self._next_at + self._interval, now)
        self._serial += 1
        return frame, datetime.now(UTC), self._serial

    def close(self) -> None:
        self._cap.release()


class ClipSource:
    """Eval/record mode: a video file read as fast as detection allows, on clip time.

    Unlike `VideoFileSource`, nothing is paced and nothing reads the wall
    clock: frame `n` is sampled at `--sample-fps` and stamped
    `clip_start + n / fps`, so a recording is reproducible and its `t_s`
    lines up with the labels (docs/evals.md §4).
    """

    def __init__(
        self,
        path: str,
        *,
        sample_fps: float = 5.0,
        start_s: float = 0.0,
        duration_s: float | None = None,
        clip_start: datetime = datetime(2026, 11, 21, tzinfo=UTC),
    ) -> None:
        import cv2

        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"video {path} could not be opened")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._cap.set(cv2.CAP_PROP_POS_MSEC, start_s * 1000)
        self._step = max(1, round(self._fps / sample_fps))
        self._duration = duration_s
        self.clip_start = clip_start
        self._index = 0
        self._serial = 0
        self.finished = False

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        while not self.finished:
            ok, frame = self._cap.read()
            if not ok:
                self.finished = True
                return None
            t = self._index / self._fps
            self._index += 1
            if self._duration is not None and t > self._duration:
                self.finished = True
                return None
            if (self._index - 1) % self._step:
                continue
            self._serial += 1
            return frame, self.clip_start + timedelta(seconds=t), self._serial
        return None

    def close(self) -> None:
        self._cap.release()


def blurred_keyframe(
    frame: Any, heads: Sequence[Box], *, long_side: int = 512, quality: int = 80
) -> bytes:
    """Blur every head box, downscale and JPEG-encode: the only frame a third party sees."""
    import cv2

    out = frame.copy()
    height, width = out.shape[:2]
    for box in heads:
        x1, y1 = max(0, int(box.x1)), max(0, int(box.y1))
        x2, y2 = min(width, int(box.x2) + 1), min(height, int(box.y2) + 1)
        if x2 <= x1 or y2 <= y1:
            continue
        region = out[y1:y2, x1:x2]
        k = max(15, (max(x2 - x1, y2 - y1) // 3) | 1)
        out[y1:y2, x1:x2] = cv2.GaussianBlur(region, (k, k), 0)
    scale = long_side / max(height, width)
    if scale < 1:
        out = cv2.resize(
            out, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA
        )
    ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return bytes(encoded)
