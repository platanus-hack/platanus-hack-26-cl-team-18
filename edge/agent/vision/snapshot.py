"""Turn each frame's tracks, objects and held products into `audience.snapshot`s.

Two jobs, both deterministic (time comes from `measured_at`, never the clock):

1. **Debounce the facts.** A frame's facts (people, looking, nearby objects,
   held products) are only *published* once they have held for `debounce`
   (500 ms by default, docs/architecture.md §3). YOLO flickers between 2 and 3
   people from frame to frame; the backend has no debounce of its own, so this
   is where a passer-by or a missed detection stops triggering a re-auction.
2. **Rate-limit the sends.** A snapshot goes out at once when the published
   facts change, otherwise as a heartbeat, never faster than `max_rate_hz`.

Counts and objects in a snapshot are the **published** (debounced) facts.
Tracklets are the **live** per-person samples of the current frame, because
the backend joins them with the playback window to measure attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from agent.contract import AudienceSnapshot, DetectedObject, MinorLikely, Tracklet
from agent.vision.held import HELD_LABEL_PREFIX, HeldProductView
from agent.vision.tracking import TrackView
from agent.vision.types import ObjectDetection

# Objects the demo and the seed catalog care about (docs/stack.md §3). Others are noise.
DEMO_OBJECT_LABELS = frozenset(
    {
        "bottle",
        "cup",
        "wine glass",
        "backpack",
        "handbag",
        "laptop",
        "cell phone",
        "dog",
        "skateboard",
        "sports ball",
        "teddy bear",  # kid-related prop for the safety beat; the backend decides what it implies
    }
)


@dataclass(frozen=True, slots=True)
class SnapshotConfig:
    min_object_score: float = 0.35
    # An object counts only if it overlaps a tracked (near) person, padded by this
    # fraction of the person box: held or carried, not on a shelf behind them.
    object_person_pad: float = 0.15
    debounce: timedelta = timedelta(milliseconds=500)
    max_rate_hz: float = 10.0
    heartbeat: timedelta = timedelta(seconds=1)


@dataclass(frozen=True, slots=True)
class Facts:
    people: int
    looking: int
    objects: tuple[tuple[str, float], ...]  # (label, best score), sorted by label
    held: tuple[HeldProductView, ...]

    @property
    def key(self) -> tuple[int, int, frozenset[str], frozenset[str]]:
        """What counts as a change: scores may wobble without it being news."""
        return (
            self.people,
            self.looking,
            frozenset(label for label, _ in self.objects),
            frozenset(h.brand_id for h in self.held),
        )


def frame_facts(
    tracks: list[TrackView],
    objects: tuple[ObjectDetection, ...],
    held: list[HeldProductView],
    config: SnapshotConfig,
) -> Facts:
    reach = [t.box.expanded(config.object_person_pad) for t in tracks]
    best: dict[str, float] = {}
    for obj in objects:
        if obj.label not in DEMO_OBJECT_LABELS or obj.score < config.min_object_score:
            continue
        if not any(area.iou(obj.box) > 0 or area.contains(obj.box.center) for area in reach):
            continue
        best[obj.label] = max(best.get(obj.label, 0.0), round(obj.score, 3))
    return Facts(
        people=len(tracks),
        looking=sum(t.looking for t in tracks),
        objects=tuple(sorted(best.items())),
        held=tuple(held),
    )


class SnapshotBuilder:
    def __init__(self, screen_id: str, config: SnapshotConfig | None = None) -> None:
        self._screen_id = screen_id
        self._config = config or SnapshotConfig()
        self._seq = 0
        self._published: Facts | None = None
        self._candidate: Facts | None = None
        self._candidate_since: datetime | None = None
        self._last_sent_at: datetime | None = None
        self._last_sent_key: tuple[int, int, frozenset[str], frozenset[str]] | None = None

    @property
    def published(self) -> Facts | None:
        return self._published

    def build(
        self,
        *,
        measured_at: datetime,
        tracks: list[TrackView],
        objects: tuple[ObjectDetection, ...],
        held: list[HeldProductView],
    ) -> AudienceSnapshot | None:
        """Feed one frame. Returns the snapshot to send now, or None."""
        facts = frame_facts(tracks, objects, held, self._config)
        self._observe(facts, measured_at)
        published = self._published
        assert published is not None
        if not self._due(measured_at, published):
            return None
        detected = [DetectedObject(label=label, score=score) for label, score in published.objects]
        detected += [
            DetectedObject(label=f"{HELD_LABEL_PREFIX}{h.brand_id}", score=h.confidence)
            for h in published.held
        ]
        snapshot = AudienceSnapshot(
            screen_id=self._screen_id,
            seq=self._seq,
            measured_at=measured_at,
            people=published.people,
            looking=published.looking,
            tracklets=[
                Tracklet(id=t.id, dwell_s=t.dwell_s, looking=t.looking, distance_band=t.distance)
                for t in tracks[:64]
            ],
            objects=detected[:64],
            # The coarse minor-likely flag comes from the warm VLM path, never from here.
            minor_likely=MinorLikely(value=False, confidence=0.0),
        )
        self._seq += 1
        self._last_sent_at = measured_at
        self._last_sent_key = published.key
        return snapshot

    def _observe(self, facts: Facts, now: datetime) -> None:
        if self._candidate is None or facts.key != self._candidate.key:
            self._candidate, self._candidate_since = facts, now
        else:
            self._candidate = facts  # same facts, fresher scores
        if self._published is None:
            self._published = facts
            return
        assert self._candidate_since is not None
        stable = now - self._candidate_since >= self._config.debounce
        if self._candidate.key == self._published.key or stable:
            self._published = self._candidate

    def _due(self, now: datetime, published: Facts) -> bool:
        if self._last_sent_at is None:
            return True
        elapsed = now - self._last_sent_at
        if elapsed < timedelta(seconds=1 / self._config.max_rate_hz):
            return False
        return published.key != self._last_sent_key or elapsed >= self._config.heartbeat
