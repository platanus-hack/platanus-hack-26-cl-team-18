"""Anonymous, session-local person tracking (ADR 0007: tracking stays on the laptop).

A greedy IoU tracker, not ByteTrack: with ≤ 10 people at 10–30 fps it is
enough, it is pure Python, and it works the same whether detections come from
the remote GPU or the local fallback, so a failover never loses tracks.

Ids are a session counter (`t1`, `t2`, …), never reused and never linked
across sessions or screens. Nothing here can re-identify a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agent.contract import DistanceBand
from agent.vision.geometry import DEFAULT_GEOMETRY, GeometryConfig, distance_band, is_looking
from agent.vision.types import Box, FrameDetections, PersonDetection


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    min_iou: float = 0.3
    min_person_score: float = 0.4
    # Near-screen filter: a person counts only when their box is at least this tall
    # relative to the frame. Far passers-by in a busy hall are not the audience.
    # 0.18 matches the stand-in validated in evals/video/perceive.py.
    min_height_fraction: float = 0.18
    # A track counts only once it has been detected in this many frames, so a
    # one-frame false positive never becomes a person (it would otherwise live
    # for `max_gap` and outlast the snapshot debounce).
    min_hits: int = 2
    # A track survives this long without a detection (occlusion, a missed frame).
    max_gap: timedelta = timedelta(milliseconds=800)
    # Looking is smoothed: a track counts as looking if it looked within this window.
    looking_hold: timedelta = timedelta(milliseconds=500)
    presence_hold: timedelta = timedelta(milliseconds=300)


@dataclass(slots=True)
class Track:
    id: str
    box: Box
    first_seen: datetime
    last_seen: datetime
    last_looking: datetime | None = None
    looking_seconds: float = 0.0
    distance: DistanceBand = DistanceBand.MID
    hits: int = 0
    missing_since: datetime | None = None
    attention_candidate_at: datetime | None = None
    raw_looking: bool | None = None
    _last_update: datetime | None = field(default=None, repr=False)

    def dwell_s(self, now: datetime) -> float:
        return max(0.0, (now - self.first_seen).total_seconds())

    def looking(self, now: datetime, hold: timedelta) -> bool:
        return self.last_looking is not None and now - self.last_looking <= hold


@dataclass(frozen=True, slots=True)
class TrackView:
    """What the snapshot needs from one track at one instant."""

    id: str
    box: Box
    dwell_s: float
    looking: bool
    distance: DistanceBand
    first_seen: datetime | None = None
    missing_since: datetime | None = None
    attention_candidate_at: datetime | None = None


class PersonTracker:
    def __init__(
        self,
        config: TrackerConfig | None = None,
        geometry: GeometryConfig = DEFAULT_GEOMETRY,
    ) -> None:
        self._config = config or TrackerConfig()
        self._geometry = geometry
        self._tracks: dict[str, Track] = {}
        self._counter = 0

    def update(self, frame: FrameDetections) -> list[TrackView]:
        now = frame.measured_at
        people = [
            p
            for p in frame.persons
            if p.score >= self._config.min_person_score and self._near(p, frame.height)
        ]
        matched = self._match(people)
        for person, track in matched:
            self._refresh(track, person, frame)
        for person in people:
            if not any(person is p for p, _ in matched):
                self._refresh(self._new_track(person, now), person, frame)
        self._expire(now)
        for track in self._tracks.values():
            if track.last_seen < now and track.missing_since is None:
                track.missing_since = now
        return [
            TrackView(
                id=t.id,
                box=t.box,
                dwell_s=round(t.dwell_s(now), 2),
                looking=t.looking(now, self._config.looking_hold),
                distance=t.distance,
                first_seen=t.first_seen,
                missing_since=t.missing_since,
                attention_candidate_at=t.attention_candidate_at,
            )
            for t in sorted(self._tracks.values(), key=lambda t: t.first_seen)
            if t.hits >= self._config.min_hits and now - t.last_seen <= self._config.presence_hold
        ]

    def reset(self) -> None:
        self._tracks.clear()

    def _near(self, person: PersonDetection, frame_height: int) -> bool:
        if frame_height <= 0:
            return True
        return person.box.height / frame_height >= self._config.min_height_fraction

    def _match(self, people: list[PersonDetection]) -> list[tuple[PersonDetection, Track]]:
        pairs = sorted(
            (
                (person.box.iou(track.box), i, track.id)
                for i, person in enumerate(people)
                for track in self._tracks.values()
            ),
            reverse=True,
        )
        used_people: set[int] = set()
        used_tracks: set[str] = set()
        matched: list[tuple[PersonDetection, Track]] = []
        for iou, i, track_id in pairs:
            if iou < self._config.min_iou:
                break
            if i in used_people or track_id in used_tracks:
                continue
            used_people.add(i)
            used_tracks.add(track_id)
            matched.append((people[i], self._tracks[track_id]))
        return matched

    def _new_track(self, person: PersonDetection, now: datetime) -> Track:
        self._counter += 1
        track = Track(id=f"t{self._counter}", box=person.box, first_seen=now, last_seen=now)
        self._tracks[track.id] = track
        return track

    def _refresh(self, track: Track, person: PersonDetection, frame: FrameDetections) -> None:
        now = frame.measured_at
        looking = is_looking(person, self._geometry)
        if track.raw_looking != looking:
            track.attention_candidate_at = now
            track.raw_looking = looking
        track.missing_since = None
        if (
            looking
            and track._last_update is not None
            and track.looking(track._last_update, self._config.looking_hold)
        ):
            track.looking_seconds += max(0.0, (now - track._last_update).total_seconds())
        if looking:
            track.last_looking = now
        track.box = person.box
        track.last_seen = now
        track.hits += 1
        track.distance = distance_band(person, frame.width, frame.height, self._geometry)
        track._last_update = now

    def _expire(self, now: datetime) -> None:
        stale = [tid for tid, t in self._tracks.items() if now - t.last_seen > self._config.max_gap]
        for tid in stale:
            del self._tracks[tid]
