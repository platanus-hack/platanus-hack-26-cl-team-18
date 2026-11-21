"""Vision loop: latest frame → detect → track → held products → snapshot (+ keyframe).

Detection runs in a worker thread (`asyncio.to_thread`) so the backend link
stays responsive. Each iteration takes the newest frame only; if detection is
slower than the camera, frames are skipped, never queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from agent.contract import AudienceKeyframe, AudienceSnapshot
from agent.sound.sources import SoundSceneSource
from agent.vision.detector import Detector, Frame
from agent.vision.geometry import head_box
from agent.vision.held import HeldProductTracker
from agent.vision.minor import MinorVote
from agent.vision.snapshot import SnapshotBuilder
from agent.vision.tracking import PersonTracker
from agent.vision.types import FrameDetections

logger = logging.getLogger(__name__)


class FrameSource(Protocol):
    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None: ...


class Link(Protocol):
    def send(self, message: BaseModel, *, binary: bytes | None = None) -> None: ...


type KeyframeEncoder = Callable[[Frame, FrameDetections], bytes]


@dataclass(frozen=True, slots=True)
class VisionConfig:
    keyframe_interval: timedelta = timedelta(milliseconds=1500)
    idle_sleep_s: float = 0.005


@dataclass(slots=True)
class VisionStats:
    frames: int = 0
    snapshots: int = 0
    keyframes: int = 0
    last_inference_ms: float = 0.0
    detector: str = ""


class VisionPipeline:
    def __init__(
        self,
        *,
        screen_id: str,
        source: FrameSource,
        detector: Detector,
        link: Link,
        held: HeldProductTracker | None = None,
        tracker: PersonTracker | None = None,
        snapshots: SnapshotBuilder | None = None,
        keyframes: KeyframeEncoder | None = None,
        config: VisionConfig | None = None,
        minor: MinorVote | None = None,
        sound: SoundSceneSource | None = None,
    ) -> None:
        self._screen_id = screen_id
        self._source = source
        self._detector = detector
        self._link = link
        self._held = held
        self._tracker = tracker or PersonTracker()
        self._snapshots = snapshots or SnapshotBuilder(screen_id)
        self._keyframes = keyframes
        self._config = config or VisionConfig()
        self._minor = minor or MinorVote()
        self._sound = sound
        self._serial = 0
        self._keyframe_seq = 0
        self._last_keyframe_at: datetime | None = None
        self.stats = VisionStats()
        self._keyframe_task: asyncio.Task[None] | None = None
        self._latest_at: datetime | None = None

    def process(self, frame: Frame, detections: FrameDetections) -> AudienceSnapshot | None:
        """Process a detected frame and publish timestamped aggregate facts."""
        self._latest_at = detections.measured_at
        detected_at = datetime.now(UTC)
        tracks = self._tracker.update(detections)
        held = self._held.update(detections.markers, tracks) if self._held else []
        # The tracked (near) people of this frame: a refreshed track holds its person's box.
        boxes = [t.box for t in tracks]
        tracked = [p for p in detections.persons if p.box in boxes]
        minor = self._minor.update(tracked, detections.height)
        sound = self._sound.current(detections.measured_at) if self._sound else ()
        snapshot = self._snapshots.build(
            measured_at=detections.measured_at,
            tracks=tracks,
            objects=detections.objects,
            held=held,
            minor=minor,
            sound=sound,
        )
        self.stats.frames += 1
        self.stats.last_inference_ms = detections.inference_ms
        self.stats.detector = detections.detector
        if snapshot is not None:
            snapshot = snapshot.model_copy(
                update={"detected_at": detected_at, "published_at": datetime.now(UTC)}
            )
            self._link.send(snapshot)
            self.stats.snapshots += 1
        if self._keyframes is not None and tracked and self._keyframe_due(detections.measured_at):
            self._schedule_keyframe(frame, detections)
        return snapshot

    async def run(self) -> None:
        """Loop until cancelled, or until a finite source (a clip) reports `finished`."""
        try:
            while True:
                item = self._source.latest(self._serial)
                if item is None:
                    if getattr(self._source, "finished", False):
                        return
                    await asyncio.sleep(self._config.idle_sleep_s)
                    continue
                frame, measured_at, self._serial = item
                try:
                    detections = await asyncio.to_thread(self._detector.detect, frame, measured_at)
                except Exception:
                    logger.exception("detector failed; skipping frame")
                    continue
                self.process(frame, detections)
        finally:
            if self._keyframe_task is not None:
                self._keyframe_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._keyframe_task

    def _keyframe_due(self, at: datetime) -> bool:
        last = self._last_keyframe_at
        return last is None or at - last >= self._config.keyframe_interval

    def _schedule_keyframe(self, frame: Frame, detections: FrameDetections) -> None:
        # One active encode, no raw-frame queue. Busy warm work drops this offer;
        # the next iteration uses a fresh frame, never a saved backlog.
        if self._keyframe_task is not None and not self._keyframe_task.done():
            return
        self._last_keyframe_at = detections.measured_at
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Synchronous process() is also used by finite offline replays.
            self._send_keyframe(frame, detections, detections.measured_at)
        else:
            self._keyframe_task = loop.create_task(
                self._encode_keyframe(frame, detections, time.monotonic())
            )

    async def _encode_keyframe(
        self, frame: Frame, detections: FrameDetections, started: float
    ) -> None:
        assert self._keyframes is not None
        try:
            jpeg = await asyncio.to_thread(self._keyframes, frame, detections)
        except Exception:
            logger.exception("keyframe encoding failed")
            return
        if (
            time.monotonic() - started <= self._config.keyframe_interval.total_seconds()
            and self._latest_at is not None
            and self._latest_at - detections.measured_at <= self._config.keyframe_interval
        ):
            self._deliver_keyframe(jpeg, detections.measured_at)

    def _send_keyframe(self, frame: Frame, detections: FrameDetections, at: datetime) -> None:
        assert self._keyframes is not None
        try:
            jpeg = self._keyframes(frame, detections)
        except Exception:
            logger.exception("keyframe encoding failed")
            return
        self._deliver_keyframe(jpeg, at)

    def _deliver_keyframe(self, jpeg: bytes, at: datetime) -> None:
        header = AudienceKeyframe(
            screen_id=self._screen_id,
            seq=self._keyframe_seq,
            measured_at=at,
            byte_length=len(jpeg),
        )
        self._link.send(header, binary=jpeg)
        self._keyframe_seq += 1
        self.stats.keyframes += 1


def blur_encoder(long_side: int = 512, quality: int = 80) -> KeyframeEncoder:
    """Keyframe encoder that blurs every detected head before encoding (needs OpenCV)."""
    from agent.vision.capture import blurred_keyframe

    def encode(frame: Frame, detections: FrameDetections) -> bytes:
        heads = [head_box(person) for person in detections.persons]
        return blurred_keyframe(frame, heads, long_side=long_side, quality=quality)

    return encode


class JsonlRecorder:
    """A `Link` that writes the eval replay contract instead of (or besides) sending.

    One line per snapshot: `{"t_s": <seconds since clip start>, "snapshot": ...}`
    (docs/evals.md §4). Keyframes are never written: a recording holds facts,
    not images.
    """

    def __init__(self, path: Path, clip_start: datetime, forward: Link | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._sink = path.open("w", encoding="utf-8")
        self._clip_start = clip_start
        self._forward = forward
        self.lines = 0

    def send(self, message: BaseModel, *, binary: bytes | None = None) -> None:
        if isinstance(message, AudienceSnapshot):
            t_s = (message.measured_at - self._clip_start).total_seconds()
            # Offline clips use clip time; wall-clock inference/publication
            # instants cannot be mixed into that timeline. Keep candidate duration.
            recorded = message.model_copy(update={"detected_at": None, "published_at": None})
            line = {"t_s": round(t_s, 3), "snapshot": recorded.model_dump(mode="json")}
            self._sink.write(json.dumps(line, ensure_ascii=False) + "\n")
            self.lines += 1
        if self._forward is not None:
            self._forward.send(message, binary=binary)

    def close(self) -> None:
        self._sink.close()
