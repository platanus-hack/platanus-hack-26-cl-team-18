"""Builders for audience classifier tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.modules.audience.domain.value_objects.evidence import SegmentEvidence
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
COFFEE = SegmentId.create("977")
FITNESS = SegmentId.create("408")
SNACKS = SegmentId.create("1115")


def at(seconds: float) -> UtcTimestamp:
    return UtcTimestamp.create(T0 + timedelta(seconds=seconds))


def evidence(
    segment_id: SegmentId,
    source: SegmentSource,
    confidence: int,
    *,
    viewers: int = 1,
    observed: float = 0,
    ttl: float = 3,
    intent: int | None = None,
) -> SegmentEvidence:
    return SegmentEvidence.create(
        segment_id=segment_id,
        source=source,
        confidence=Percentage.create(confidence),
        viewer_count=viewers,
        observed_at=at(observed),
        expires_at=at(observed + ttl),
        speech_intent=Percentage.create(intent) if intent is not None else None,
    )


class MomentIds:
    def __init__(self) -> None:
        self.minted = 0

    def __call__(self) -> MomentId:
        self.minted += 1
        return MomentId.create(UUID(int=self.minted))
