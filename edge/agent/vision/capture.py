"""Camera capture with a single latest-frame slot, a video-file replay source, and face blur.

Frames are never queued: the capture thread overwrites the slot, so inference
always gets the newest frame and a slow detector drops frames instead of
falling behind (docs/stack.md §3). OpenCV is imported lazily.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.vision.types import Box

logger = logging.getLogger(__name__)


class LatestFrameCamera:
    """UVC webcam: 1280x720 @ 30 fps MJPG, buffer of 1, newest frame only.

    An unplugged or wedged camera is reopened after `reopen_after_s` of failed
    reads; a read error or an empty frame never kills the capture thread.
    """

    def __init__(
        self,
        index: int = 0,
        *,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        reopen_after_s: float = 2.0,
    ) -> None:
        import cv2

        self._cv2 = cv2
        self._index = index
        self._settings = (width, height, fps)
        self._reopen_after_s = reopen_after_s
        self._cap = self._open()
        if not self._cap.isOpened():
            raise RuntimeError(f"camera {index} could not be opened")
        self._lock = threading.Lock()
        self._latest: tuple[Any, datetime] | None = None
        self._serial = 0
        self._stop = threading.Event()
        self._released = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _open(self) -> Any:
        cv2 = self._cv2
        width, height, fps = self._settings
        cap = cv2.VideoCapture(self._index)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _loop(self) -> None:
        # The capture thread owns the device: it alone reads, reopens and finally
        # releases it, so `close()` can never release a handle mid-read.
        try:
            self._capture()
        finally:
            try:
                self._cap.release()
            except Exception:
                pass
            self._released.set()

    def _capture(self) -> None:
        failing_since: float | None = None
        while not self._stop.is_set():
            frame = None
            try:
                # grab() returns once the sensor frame is in; stamp it before the
                # MJPG decode in retrieve(), so decode time isn't hidden in measured_at.
                ok = bool(self._cap.grab())
                measured_at = datetime.now(UTC)
                if ok:
                    ok, frame = self._cap.retrieve()
            except Exception:  # cv2.error from a vanished device
                ok, frame = False, None
            if not ok or frame is None or getattr(frame, "size", 1) == 0:
                now = time.monotonic()
                if failing_since is None:
                    failing_since = now
                elif now - failing_since >= self._reopen_after_s:
                    logger.warning("camera %s returns no frames; reopening it", self._index)
                    self._reopen()
                    failing_since = time.monotonic()
                self._stop.wait(0.01)
                continue
            failing_since = None
            with self._lock:
                self._latest = (frame, measured_at)
                self._serial += 1

    def _reopen(self) -> None:
        try:
            self._cap.release()
        except Exception:
            pass
        try:
            self._cap = self._open()
        except Exception:  # the device is still gone: try again later
            logger.warning("camera %s could not be reopened yet", self._index)

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        """The newest frame if it is newer than `after_serial`, else None."""
        with self._lock:
            if self._latest is None or self._serial <= after_serial:
                return None
            frame, measured_at = self._latest
            return frame, measured_at, self._serial

    def close(self, timeout: float = 3.0) -> None:
        """Stop capturing. The capture thread releases the device on its way out."""
        self._stop.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            # A wedged read: releasing under it can crash OpenCV. The thread still
            # releases the device if the read ever returns; a daemon dies with us.
            logger.warning(
                "camera %s capture thread still busy after %.1fs; not releasing under it",
                self._index,
                timeout,
            )


class VideoFileSource:
    """Replay mode (docs/demo.md §4): a recorded video file stands in for the webcam.

    Paced at the file's frame rate and looped, with `measured_at` stamped at
    read time, so the backend sees exactly what a live camera would send.
    """

    def __init__(self, path: str, *, loop: bool = True) -> None:
        import cv2

        self._cv2 = cv2
        self._loop = loop
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"video {path} could not be opened")
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        frames = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or not math.isfinite(frames) or fps <= 0 or frames < 1:
            self._cap.release()
            raise RuntimeError("video has no usable frame rate or duration")
        self._fps = fps
        self._frame_count = int(frames)
        self._started: float | None = None
        self._last_target = -1
        self._next_index = 0
        self._serial = 0
        self.finished = False

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        if self.finished:
            return None
        now = time.monotonic()
        if self._started is None:
            self._started = now  # models warm up before the playback clock starts
        target = int((now - self._started) * self._fps)
        if target <= self._last_target:
            return None
        if not self._loop and target >= self._frame_count:
            self.finished = True
            return None
        index = target % self._frame_count
        if index != self._next_index:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self._cap.read()
        if not ok:
            self.finished = True
            return None
        self._last_target = target
        self._next_index = index + 1
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
