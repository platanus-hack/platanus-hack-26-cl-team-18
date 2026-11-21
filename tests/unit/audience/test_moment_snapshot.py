from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.screens.domain.value_objects.ids import ScreenId


@pytest.fixture
def moment() -> AudienceMomentSnapshot:
    return AudienceMomentSnapshot.create(
        screen_id=ScreenId.create(UUID("018f0000-0000-7000-8000-000000000001")),
        moment_id=MomentId.create(UUID("018f0000-0000-7000-8000-000000000002")),
        measured_at=UtcTimestamp.create(datetime(2026, 11, 21, 12, 0, 1, tzinfo=UTC)),
        received_at=UtcTimestamp.create(datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)),
        moment_changed_at=UtcTimestamp.create(datetime(2026, 11, 21, 12, 0, 2, tzinfo=UTC)),
        people=3,
        looking=2,
        segments=(
            SegmentEstimate.create(
                segment_id=SegmentId.create("DEMO_FITNESS"),
                viewer_count=2,
                confidence=Percentage.create(80),
                sources=frozenset({SegmentSource.VISION, SegmentSource.SPEECH}),
                speech_intent=Percentage.create(60),
            ),
        ),
        minor_likely=True,
    )


def test_preserves_fused_segment_and_device_clock_skew(moment: AudienceMomentSnapshot) -> None:
    assert moment.segments[0].sources == {SegmentSource.VISION, SegmentSource.SPEECH}
    assert moment.segments[0].speech_intent == Percentage.create(60)
    assert moment.measured_at.value > moment.received_at.value
    with pytest.raises(FrozenInstanceError):
        setattr(moment, "looking", 0)  # noqa: B010


def test_rejects_overcount_and_duplicate_segments(moment: AudienceMomentSnapshot) -> None:
    with pytest.raises(ValueError, match="cannot exceed looking"):
        replace(moment, looking=1)
    with pytest.raises(ValueError, match="unique"):
        replace(moment, segments=moment.segments * 2)


def test_rejects_speech_intent_without_speech_evidence(moment: AudienceMomentSnapshot) -> None:
    with pytest.raises(ValueError, match="requires a speech source"):
        replace(moment.segments[0], sources=frozenset({SegmentSource.VISION}))


def test_rejects_untyped_segment_payloads(moment: AudienceMomentSnapshot) -> None:
    with pytest.raises(TypeError, match="tuple of SegmentEstimate"):
        replace(moment, segments=("an ephemeral utterance",))
