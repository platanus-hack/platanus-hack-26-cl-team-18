"""Fuse per-source evidence into one estimate per segment."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from app.modules.audience.domain.value_objects.evidence import SegmentEvidence
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.primitives.policy import Policy
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

ZERO = Decimal(0)
ONE = Decimal(1)


ENTER_AT = Percentage.create(50)
EXIT_AT = Percentage.create(40)


@dataclass(frozen=True, slots=True, kw_only=True)
class FusionInput:
    evidence: tuple[SegmentEvidence, ...]
    looking: int
    now: UtcTimestamp
    # Segments that were biddable last time: they stay until they drop below `exit_at`.
    previously_biddable: frozenset[SegmentId] = frozenset()
    enter_at: Percentage = ENTER_AT
    exit_at: Percentage = EXIT_AT
    # One talker counts as one viewer, so a loud person can't outweigh the group.
    speech_viewer_cap: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class FusionResult:
    biddable: tuple[SegmentEstimate, ...]
    below_threshold: tuple[SegmentEstimate, ...]


class SegmentFusionPolicy(Policy[FusionInput, FusionResult]):
    """Noisy-OR across sources, strongest claim within each source.

    Sources are treated as independent detectors: a segment seen by vision at
    0.6 and heard at 0.5 is 1 - 0.4 * 0.5 = 0.8. Two cues from the same source
    are not independent (one camera, one frame), so only the strongest counts.
    Viewer count is the largest any source claims, capped by who is looking.
    Hysteresis keeps a segment from flapping around the threshold.
    """

    name: ClassVar[str] = "audience.segment_fusion"
    version: ClassVar[int] = 1

    def apply(self, input: FusionInput) -> FusionResult:
        if input.exit_at.value > input.enter_at.value:
            raise ValueError("exit threshold cannot exceed the enter threshold")
        if input.looking <= 0:
            return FusionResult(biddable=(), below_threshold=())

        weights: dict[SegmentId, dict[SegmentSource, Decimal]] = {}
        viewers: dict[SegmentId, int] = {}
        intents: dict[SegmentId, Decimal] = {}
        for item in input.evidence:
            weight = item.weight_at(input.now)
            if weight <= ZERO:
                continue
            per_source = weights.setdefault(item.segment_id, {})
            per_source[item.source] = max(per_source.get(item.source, ZERO), weight)
            count = item.viewer_count
            if item.source is SegmentSource.SPEECH:
                count = min(count, input.speech_viewer_cap)
                if item.speech_intent is not None:
                    intents[item.segment_id] = max(
                        intents.get(item.segment_id, ZERO), item.speech_intent.value
                    )
            viewers[item.segment_id] = max(viewers.get(item.segment_id, 0), count)

        biddable: list[SegmentEstimate] = []
        below: list[SegmentEstimate] = []
        for segment_id in sorted(weights, key=lambda segment_id: segment_id.value):
            per_source = weights[segment_id]
            miss = ONE
            for weight in per_source.values():
                miss *= ONE - min(weight, ONE)
            confidence = Percentage.create((ONE - miss) * 100)
            intent = intents.get(segment_id)
            estimate = SegmentEstimate.create(
                segment_id=segment_id,
                viewer_count=min(viewers[segment_id], input.looking),
                confidence=confidence,
                sources=frozenset(per_source),
                speech_intent=Percentage.create(intent) if intent is not None else None,
            )
            threshold = input.exit_at if segment_id in input.previously_biddable else input.enter_at
            (biddable if confidence.value >= threshold.value else below).append(estimate)
        return FusionResult(biddable=tuple(biddable), below_threshold=tuple(below))
