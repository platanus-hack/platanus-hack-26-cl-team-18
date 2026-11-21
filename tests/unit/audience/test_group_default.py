"""A watching group with no other signal gets a low-confidence coffee default."""

import pytest

from app.modules.audience.domain.policies.group_default import GroupDefault
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage

COFFEE = SegmentId.create("977")
BEER = SegmentId.create("369")
DEFAULT = GroupDefault(segment_id=COFFEE)


def seg(segment: SegmentId) -> SegmentEstimate:
    return SegmentEstimate.create(
        segment_id=segment,
        viewer_count=2,
        confidence=Percentage.create(90),
        sources=frozenset({SegmentSource.VISION}),
    )


@pytest.mark.parametrize("looking", [0, 1])
def test_a_passer_by_is_not_an_audience(looking: int) -> None:
    assert DEFAULT.apply(segments=(), looking=looking) == ()


def test_a_watching_queue_gets_a_low_confidence_coffee_moment() -> None:
    [default] = DEFAULT.apply(segments=(), looking=3)
    assert default.segment_id == COFFEE and default.viewer_count == 3
    assert default.confidence.value == 40


def test_any_real_signal_wins_over_the_default() -> None:
    real = (seg(BEER),)
    assert DEFAULT.apply(segments=real, looking=3) == real
