"""Real-time video replay. Raw frames stay in this process and are never saved."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .frame_selector import FrameSelector
from .publisher import SnapshotPublisher
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
        if not math.isfinite(self.fps) or not math.isfinite(frames) or self.fps <= 0 or frames <= 0:
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
            x1, y1, x2, y2 = (float(x) for x in box.xyxy[0].tolist())
            detections.append(
                Detection(
                    str(result.names[int(box.cls.item())]),
                    float(box.conf.item()),
                    (x1, y1, x2, y2),
                    track_id,
                )
            )
        return detections


def recognition_message(snapshot: dict, screen_id: str, seq: int, started_at: datetime) -> dict:
    def person(item: dict) -> dict:
        attributes = [
            {
                "name": name,
                "value": str(value["value"]),
                "source": value["source"],
                "observed_at_s": value["observed_at"],
                "evidence": value["evidence"],
                "uncertainty": value.get("uncertainty", "unknown"),
                "model": value.get("model", ""),
            }
            for name, value in item["attributes"].items()
        ]
        return {
            "track_id": str(item["track_id"]),
            "first_seen_s": item["first_seen"],
            "last_seen_s": item["last_seen"],
            "box": item["box"],
            "confidence": item["confidence"],
            "direction": item["direction"],
            "currently_visible": item["currently_visible"],
            "attributes": attributes[:64],
        }

    timestamp = snapshot["timestamp"]
    objects = [
        {"label": label, "count": count, "last_seen_s": timestamp, "currently_visible": True}
        for label, count in snapshot["scene"]["currently_visible"].items()
    ]
    objects += [
        {
            "label": label,
            "count": item["count"],
            "last_seen_s": item["last_seen"],
            "currently_visible": False,
        }
        for label, item in snapshot["scene"]["recently_observed"].items()
    ]
    latency = snapshot["latency_ms"]
    return {
        "type": "recognition.snapshot",
        "v": 1,
        "screen_id": screen_id,
        "session_id": snapshot["session_id"],
        "session_started_at": started_at.isoformat(),
        "seq": seq,
        "measured_at": (started_at + timedelta(seconds=timestamp)).isoformat(),
        "video_time_s": timestamp,
        "finished": snapshot["finished"],
        "people": [person(p) for p in snapshot["people"][-128:]],
        "history": [person(p) for p in snapshot["history"][-256:]],
        "detections": [
            {
                "label": d["label"],
                "confidence": d["confidence"],
                "box": d["box"],
                "track_id": str(d["track_id"]) if d["track_id"] is not None else None,
                "observed_at_s": timestamp,
            }
            for d in snapshot["detections"][:256]
        ],
        "objects": objects[:128],
        "audio_events": [],
        "latency": {
            "decode_ms": latency["decode"],
            "detection_tracking_ms": latency["detection_and_tracking"],
            "publication_delay_ms": latency["publication_delay"],
            "first_observation_ms": latency["first_observation"],
        },
    }


def run(
    source: VideoSource,
    detector: VisualDetector,
    fps: float = 3.0,
    *,
    publisher: SnapshotPublisher | None = None,
    screen_id: str = "demo-01",
    vlm: bool = False,
    print_snapshots: bool = False,
) -> None:
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be positive")
    state = AudienceState(str(uuid.uuid4()))
    started_at = datetime.now(UTC)
    started = time.monotonic()
    selector = (
        FrameSelector(publisher.url, screen_id, state.session_id, started_at)
        if vlm and publisher
        else None
    )
    first_observation = None
    seq = 0
    last_latency = {
        "decode": 0.0,
        "detection_and_tracking": 0.0,
        "publication_delay": 0.0,
        "first_observation": None,
    }
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
            if selector is not None:
                try:
                    selector.offer(frame, detections, measured_at)
                except Exception:
                    pass  # The optional warm path must not stop detection.
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
            last_latency = result["latency_ms"]
            seq += 1
            message = recognition_message(result, screen_id, seq, started_at)
            if print_snapshots:
                print(json.dumps(message), flush=True)
            if publisher is not None:
                publisher.publish(message)
            # Never catch up by processing old frames: next read seeks to playback time.
            time.sleep(max(0.0, 1 / fps - (time.monotonic() - decode_start)))
        state.finish(source.duration)
        result = state.snapshot()
        result["latency_ms"] = last_latency
        seq += 1
        message = recognition_message(result, screen_id, seq, started_at)
        if print_snapshots:
            print(json.dumps(message), flush=True)
        if publisher is not None:
            publisher.publish(message)
    finally:
        source.close()
        if selector is not None:
            selector.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a video as an anonymous live camera")
    parser.add_argument("video", help="Local MP4 or MOV file")
    parser.add_argument("--fps", type=float, default=3.0)
    parser.add_argument("--model", default="yolo26n.pt")
    parser.add_argument(
        "--backend", help="Backend WebSocket URL, e.g. ws://localhost:8000/ws/agent"
    )
    parser.add_argument("--screen-id", default="demo-01")
    parser.add_argument(
        "--recognition-only",
        action="store_true",
        help="Publish only the debug recognition feed; do not drive ads",
    )
    parser.add_argument("--vlm", action="store_true", help="Enable privacy-filtered VLM crops")
    parser.add_argument(
        "--print-snapshots", action="store_true", help="Print debug snapshots to stdout"
    )
    args = parser.parse_args()
    if args.vlm and not args.backend:
        parser.error("--vlm requires --backend")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", args.screen_id):
        parser.error("--screen-id must contain 1–64 letters, digits, or _ . : -")
    if args.backend and not args.recognition_only:
        # Ads require pose-based attention and minor safety. Reuse the canonical
        # pipeline rather than inventing those facts from detection-only tracks.
        from .__main__ import argument_parser
        from .__main__ import run as run_live

        live_args = argument_parser().parse_args(
            [
                "--screen",
                args.screen_id,
                "--backend",
                args.backend,
                "--video",
                args.video,
            ]
        )
        asyncio.run(run_live(live_args))
        return
    detector = LocalDetector(args.model)
    detector.warmup()
    publisher = SnapshotPublisher(args.backend) if args.backend else None
    try:
        run(
            FileVideoSource(args.video),
            detector,
            args.fps,
            publisher=publisher,
            screen_id=args.screen_id,
            vlm=args.vlm,
            print_snapshots=args.print_snapshots,
        )
    finally:
        if publisher is not None:
            publisher.close()


if __name__ == "__main__":
    main()
