"""Real-time video replay. Raw frames stay in this process and are never saved."""

from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from typing import Protocol

from .state import AudienceState, Detection


class VideoSource(Protocol):
    duration: float

    def frame_at(self, timestamp: float) -> tuple[object, float]: ...
    def close(self) -> None: ...


class FileVideoSource:
    def __init__(self, path: str):
        import cv2

        self.cv2 = cv2
        self.capture = cv2.VideoCapture(path)
        if not self.capture.isOpened():
            raise ValueError("Unable to open video")
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        frames = self.capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if self.fps <= 0 or frames <= 0:
            self.capture.release()
            raise ValueError("Video has no usable frame rate or duration")
        self.duration = frames / self.fps

    def frame_at(self, timestamp: float) -> tuple[object, float]:
        index = min(int(timestamp * self.fps), int(self.duration * self.fps) - 1)
        self.capture.set(self.cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok:
            raise ValueError("Could not decode video frame")
        return frame, index / self.fps

    def close(self) -> None:
        self.capture.release()


class VisualDetector(Protocol):
    def detect(self, frame: object) -> list[Detection]: ...


class LocalDetector:
    def __init__(self, model: str = "yolo26n.pt"):
        from ultralytics import YOLO

        self.model = YOLO(model)

    def warmup(self) -> None:
        """Initialize the CPU model before starting the video playback clock."""
        import numpy as np

        self.model.predict(np.zeros((640, 640, 3), dtype=np.uint8), verbose=False)

    def detect(self, frame: object) -> list[Detection]:
        result = self.model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)[0]
        detections = []
        for box in result.boxes:
            track_id = int(box.id.item()) if box.id is not None else None
            detections.append(
                Detection(
                    str(result.names[int(box.cls.item())]),
                    float(box.conf.item()),
                    tuple(float(x) for x in box.xyxy[0].tolist()),
                    track_id,
                )
            )
        return detections


def run(source: VideoSource, detector: VisualDetector, fps: float = 3.0) -> None:
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be positive")
    state = AudienceState(str(uuid.uuid4()))
    started = time.monotonic()
    first_observation = None
    try:
        while True:
            timestamp = time.monotonic() - started
            if timestamp >= source.duration:
                break
            decode_start = time.monotonic()
            frame, measured_at = source.frame_at(timestamp)
            decoded = time.monotonic()
            detections = detector.detect(frame)
            inferred = time.monotonic()
            state.observe(measured_at, detections)
            if detections and first_observation is None:
                first_observation = round((inferred - started) * 1000, 1)
            # A result from a slow inference is historical, not proof that the
            # same person is still in view at publication time.
            playback_now = min(inferred - started, source.duration)
            if playback_now - measured_at > state.exit_after:
                state.observe(playback_now, [])
            result = state.snapshot()
            result["latency_ms"] = {
                "decode": round((decoded - decode_start) * 1000, 1),
                "detection_and_tracking": round((inferred - decoded) * 1000, 1),
                "publication_delay": round((inferred - started - measured_at) * 1000, 1),
                "first_observation": first_observation,
            }
            print(json.dumps(result), flush=True)
            # Never catch up by processing old frames: next read seeks to playback time.
            time.sleep(max(0.0, 1 / fps - (time.monotonic() - decode_start)))
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a video as an anonymous live camera")
    parser.add_argument("video", help="Local MP4 or MOV file")
    parser.add_argument("--fps", type=float, default=3.0)
    parser.add_argument("--model", default="yolo26n.pt")
    args = parser.parse_args()
    detector = LocalDetector(args.model)
    detector.warmup()
    run(FileVideoSource(args.video), detector, args.fps)


if __name__ == "__main__":
    main()
