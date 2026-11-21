"""Speech topics with a TTL, merged into a moment's segments (docs/architecture.md §7.1).

Only classified topics live here: never the utterance text (ADR 0005). A topic
expires `ttl` after it was heard. When it expires, the segment reverts to its
visual evidence, or disappears if speech was its only source.

Pure: time comes in as arguments.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

DEFAULT_TTL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechTopic:
    segment_id: SegmentId
    intent: Percentage
    heard_at: UtcTimestamp
    expires_at: UtcTimestamp

    def __post_init__(self) -> None:
        if self.expires_at.value <= self.heard_at.value:
            raise ValueError("a speech topic must expire after it was heard")

    def active_at(self, at: UtcTimestamp) -> bool:
        return self.heard_at.value <= at.value < self.expires_at.value


@dataclass(frozen=True, slots=True)
class SpeechEvidence:
    """Strongest live topic per segment."""

    topics: tuple[SpeechTopic, ...] = ()

    def heard(self, new: Iterable[SpeechTopic]) -> SpeechEvidence:
        by_segment = {t.segment_id: t for t in self.topics}
        for topic in new:
            current = by_segment.get(topic.segment_id)
            if current is None or _stronger_or_fresher(topic, current):
                by_segment[topic.segment_id] = topic
        return SpeechEvidence(tuple(sorted(by_segment.values(), key=lambda t: t.segment_id.value)))

    def active(self, at: UtcTimestamp) -> SpeechEvidence:
        live = tuple(t for t in self.topics if t.active_at(at))
        return self if len(live) == len(self.topics) else SpeechEvidence(live)

    def next_expiry(self) -> UtcTimestamp | None:
        return min((t.expires_at for t in self.topics), key=lambda ts: ts.value, default=None)


def _stronger_or_fresher(new: SpeechTopic, old: SpeechTopic) -> bool:
    # A repeat of the same intent refreshes the TTL; a stronger one replaces it.
    return new.intent.value >= old.intent.value or new.expires_at.value > old.expires_at.value


def merge_speech(
    segments: tuple[SegmentEstimate, ...],
    evidence: SpeechEvidence,
    *,
    at: UtcTimestamp,
    looking: int,
) -> tuple[SegmentEstimate, ...]:
    """Segments with live speech merged in. The speaker counts as one looking viewer.

    Speech can't add viewers nobody is looking (`looking == 0`): an empty hall
    isn't an audience, whatever the microphone heard.
    """
    live = {t.segment_id: t for t in evidence.active(at).topics}
    merged: list[SegmentEstimate] = []
    for estimate in segments:
        topic = live.pop(estimate.segment_id, None)
        if topic is None:
            merged.append(estimate)
            continue
        merged.append(
            replace(
                estimate,
                confidence=_max(estimate.confidence, topic.intent),
                sources=estimate.sources | {SegmentSource.SPEECH},
                speech_intent=topic.intent,
            )
        )
    if looking > 0:
        merged.extend(
            SegmentEstimate.create(
                segment_id=topic.segment_id,
                viewer_count=1,
                confidence=topic.intent,
                sources=frozenset({SegmentSource.SPEECH}),
                speech_intent=topic.intent,
            )
            for topic in live.values()
        )
    return tuple(sorted(merged, key=lambda s: s.segment_id.value))


def _max(a: Percentage, b: Percentage) -> Percentage:
    return a if a.value >= b.value else b


def topics_from_keyterms(
    keyterms: Iterable[str],
    catalog: dict[str, SegmentId] | None,
    *,
    heard_at: UtcTimestamp,
    intent: Percentage | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> tuple[SpeechTopic, ...]:
    """Instant path: a keyterm hit maps straight to its segment (no model call)."""
    if not catalog:
        return ()
    strength = intent or Percentage.create(Decimal(90))
    expires = UtcTimestamp.create(heard_at.value + ttl)
    seen: dict[SegmentId, SpeechTopic] = {}
    for term in keyterms:
        segment = catalog.get(term)
        if segment is not None:
            seen[segment] = SpeechTopic(
                segment_id=segment, intent=strength, heard_at=heard_at, expires_at=expires
            )
    return tuple(seen.values())
