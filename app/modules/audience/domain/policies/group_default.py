"""A watching group with nothing else to go on gets an honest, low-confidence default.

Real footage (queues, families, the cashier) showed groups looking at the screen while
nothing in view mapped to an interest: the house ad, a sold moment lost. When at least
`min_looking` people look and no segment is biddable, this proposes one default segment
(coffee at a convenience counter) at `confidence`:

* low confidence, so it prices well below real evidence and any real signal outranks it;
* a need, not a claim: the coffee angle says nothing about what the people did (unlike
  fitness, whose copy would claim a workout);
* never for a lone passer-by (one glance is not an audience), and never age-restricted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage

DEFAULT_CONFIDENCE = Percentage.create(40)


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupDefault:
    segment_id: SegmentId
    confidence: Percentage = DEFAULT_CONFIDENCE
    min_looking: int = 2

    def apply(
        self,
        *,
        segments: tuple[SegmentEstimate, ...],
        looking: int,
        has_eligible_bid: bool | None = None,
    ) -> tuple[SegmentEstimate, ...]:
        if looking < self.min_looking or (segments and has_eligible_bid is not False):
            return segments
        if any(segment.segment_id == self.segment_id for segment in segments):
            return segments
        return (
            *segments,
            SegmentEstimate.create(
                segment_id=self.segment_id,
                viewer_count=looking,
                confidence=self.confidence,
                # Derived from people seen, not from any object: vision, at low confidence.
                sources=frozenset({SegmentSource.VISION}),
            ),
        )
