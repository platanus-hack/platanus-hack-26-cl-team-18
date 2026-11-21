"""Ephemeral recognition state. Timestamps are seconds in the source video."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]
    track_id: int | None = None


@dataclass
class Person:
    track_id: int
    first_seen: float
    last_seen: float
    box: tuple[float, float, float, float]
    confidence: float
    direction: str = "unknown"
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)


class AudienceState:
    def __init__(self, session_id: str, exit_after: float = 1.0, scene_ttl: float = 2.0):
        self.session_id = session_id
        self.exit_after = exit_after
        self.scene_ttl = scene_ttl
        self.timestamp = 0.0
        self.people: dict[int, Person] = {}
        self.objects: dict[str, tuple[float, int]] = {}
        self.visible_objects: dict[str, int] = {}

    def observe(self, timestamp: float, detections: list[Detection]) -> None:
        # A slow detector must never rewind the current scene.
        if timestamp < self.timestamp:
            return
        self.timestamp = timestamp
        counts: Counter[str] = Counter()
        for detection in detections:
            if detection.label != "person":
                counts[detection.label] += 1
                continue
            if detection.track_id is None:
                continue
            person = self.people.get(detection.track_id)
            if person is None:
                self.people[detection.track_id] = Person(
                    detection.track_id, timestamp, timestamp, detection.box, detection.confidence
                )
            else:
                dx = (detection.box[0] + detection.box[2] - person.box[0] - person.box[2]) / 2
                width = max(person.box[2] - person.box[0], 1)
                person.direction = (
                    "right"
                    if dx > width * 0.05
                    else ("left" if dx < -width * 0.05 else "stationary")
                )
                person.box = detection.box
                person.last_seen = timestamp
                person.confidence = detection.confidence
        self.visible_objects = dict(counts)
        for label, count in counts.items():
            self.objects[label] = (timestamp, count)

    def enrich(
        self,
        track_id: int,
        name: str,
        value: Any,
        observed_at: float,
        source: str,
        evidence: str,
        strength: float,
    ) -> None:
        """Merge late results only into their original track, favoring stronger evidence."""
        person = self.people.get(track_id)
        if person is None or not person.first_seen <= observed_at <= person.last_seen:
            return
        old = person.attributes.get(name)
        if old and (
            old["strength"] > strength
            or (old["strength"] == strength and old["observed_at"] > observed_at)
        ):
            return
        person.attributes[name] = {
            "value": value,
            "observed_at": observed_at,
            "source": source,
            "evidence": evidence,
            "strength": strength,
        }

    def snapshot(self) -> dict[str, Any]:
        active, history = [], []
        for person in self.people.values():
            item = asdict(person)
            item["visible_duration"] = round(person.last_seen - person.first_seen, 3)
            item["currently_visible"] = person.last_seen == self.timestamp
            if self.timestamp - person.last_seen <= self.exit_after:
                active.append(item)
            else:
                history.append(item)
        recent = {
            label: {"last_seen": seen, "count": count}
            for label, (seen, count) in self.objects.items()
            if self.timestamp - seen <= self.scene_ttl and label not in self.visible_objects
        }
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "people_count": len(active),
            "people": active,
            "history": history,
            "scene": {
                "currently_visible": self.visible_objects,
                "recently_observed": recent,
                "audio_events": [],
            },
        }
