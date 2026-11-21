"""Deterministic classification of the simulator's structured observations."""

from collections.abc import Mapping
from decimal import Decimal

from app.contract.messages import AudienceSnapshot
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage


def classify_snapshot(
    snapshot: AudienceSnapshot,
    *,
    vision_catalog: Mapping[str, SegmentId],
    sound_catalog: Mapping[str, SegmentId],
) -> tuple[SegmentEstimate, ...]:
    """Each demonstrated segment represents one estimated looking viewer."""
    if snapshot.looking == 0:
        return ()

    scores: dict[SegmentId, Decimal] = {}
    sources: dict[SegmentId, set[SegmentSource]] = {}
    signals = (
        (snapshot.objects, vision_catalog, SegmentSource.VISION),
        (snapshot.sound_scene, sound_catalog, SegmentSource.SOUND),
    )
    for observations, catalog, source in signals:
        for observation in observations:
            if (segment_id := catalog.get(observation.label)) is None or not observation.score:
                continue
            confidence = Decimal(str(observation.score)) * 100
            scores[segment_id] = max(scores.get(segment_id, Decimal(0)), confidence)
            sources.setdefault(segment_id, set()).add(source)

    return tuple(
        SegmentEstimate.create(
            segment_id=segment_id,
            viewer_count=1,
            confidence=Percentage.create(scores[segment_id]),
            sources=frozenset(sources[segment_id]),
        )
        for segment_id in sorted(scores, key=lambda segment_id: segment_id.value)
    )
