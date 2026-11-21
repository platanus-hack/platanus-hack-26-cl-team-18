"""`dev.perception`: what YOLO saw on a frame, for the backend's `/dev` QA console.

A lossy, rate-limited projection of state the pipeline already has (tracks,
objects, markers, held products): normalized box geometry and labels only. No
pixels, no keypoints, no identity; track ids stay anonymous and session-local.
Opt-in on the agent (`--dev-stream` / EDGE_DEV_STREAM=1); the backend drops it
in prod and when no `/dev` page is open.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from agent.contract import DevBox, DevMarker, DevObject, DevPerception, DevPerson
from agent.vision.held import HeldProductView
from agent.vision.tracking import TrackView
from agent.vision.types import Box, FrameDetections

MAX_ITEMS = 64
MAX_MARKERS = 16


@dataclass(frozen=True, slots=True)
class DevViewConfig:
    min_interval: timedelta = timedelta(milliseconds=200)  # <= 5 Hz
    object_person_pad: float = 0.15  # same reach rule as the snapshot builder


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _norm(box: Box, width: int, height: int) -> DevBox:
    x1, x2 = sorted((_unit(box.x1 / width), _unit(box.x2 / width)))
    y1, y2 = sorted((_unit(box.y1 / height), _unit(box.y2 / height)))
    return DevBox(x1=x1, y1=y1, x2=x2, y2=y2)


class DevViewBuilder:
    def __init__(
        self,
        screen_id: str,
        *,
        brand_for: Callable[[int], str | None] | None = None,
        config: DevViewConfig | None = None,
    ) -> None:
        self._screen_id = screen_id
        self._brand_for = brand_for
        self._config = config or DevViewConfig()
        self._last_at: datetime | None = None
        self._seq = 0

    def build(
        self,
        detections: FrameDetections,
        tracks: Sequence[TrackView],
        held: Sequence[HeldProductView],
        *,
        frames: int = 0,
        stale_frames: int = 0,
    ) -> DevPerception | None:
        """A view at most every `min_interval` of device time; None in between."""
        at = detections.measured_at
        if self._last_at is not None and at - self._last_at < self._config.min_interval:
            return None
        self._last_at = at
        width, height = max(detections.width, 1), max(detections.height, 1)
        scores = {person.box: person.score for person in detections.persons}
        reach = [t.box.expanded(self._config.object_person_pad) for t in tracks]
        view = DevPerception(
            screen_id=self._screen_id,
            seq=self._seq,
            measured_at=at,
            frame_w=width,
            frame_h=height,
            detector=detections.detector[:64] or "unknown",
            inference_ms=max(detections.inference_ms, 0.0),
            round_trip_ms=detections.extras.get("round_trip_ms"),
            raw_people=len(detections.persons),
            people=[
                DevPerson(
                    track_id=track.id[:32],
                    box=_norm(track.box, width, height),
                    confidence=_unit(scores.get(track.box, 0.0)),
                    looking=track.looking,
                    distance_band=track.distance,
                    dwell_s=max(track.dwell_s, 0.0),
                )
                for track in tracks
            ][:MAX_ITEMS],
            objects=[
                DevObject(
                    label=obj.label[:64],
                    confidence=_unit(obj.score),
                    box=_norm(obj.box, width, height),
                    near_person=any(
                        area.iou(obj.box) > 0 or area.contains(obj.box.center) for area in reach
                    ),
                )
                for obj in detections.objects
            ][:MAX_ITEMS],
            markers=[
                DevMarker(
                    marker_id=marker.marker_id,
                    brand_ref=(self._brand_for(marker.marker_id) if self._brand_for else None),
                    box=_norm(marker.box, width, height),
                )
                for marker in detections.markers
            ][:MAX_MARKERS],
            held=[product.brand_id[:64] for product in held][:MAX_MARKERS],
            frames=frames,
            stale_frames=stale_frames,
        )
        self._seq += 1
        return view

    def reset(self) -> None:
        self._last_at = None
