"""Classify one perception reading: vision, sound and the latest VLM findings fused.

No debounce here: the agent publishes facts only after they hold for 500 ms
(ADR 0008 D10) and the auction desk holds swaps (`auction/domain/swap.py`).
Speech topics merge in afterwards (`speech_evidence.merge_speech`).

Pure: time and every input come in as arguments.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.audience.domain.policies.fusion import FusionInput, SegmentFusionPolicy
from app.modules.audience.domain.value_objects.evidence import HeldProduct, SegmentEvidence
from app.modules.audience.domain.value_objects.segment import SegmentEstimate, SegmentId
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

PCT_0 = Percentage.create(0)
PCT_50 = Percentage.create(50)
PCT_60 = Percentage.create(60)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClassifierSettings:
    # Segments below `enter_at` don't bid. The default floor is zero because the
    # auction already prices confidence (value = bid x viewers x confidence) and
    # records "below_reserve" for weak ones, which the console shows.
    enter_at: Percentage = PCT_0
    exit_at: Percentage = PCT_0
    held_product_min: Percentage = PCT_60
    minor_likely_min: Percentage = PCT_50


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyframeFindings:
    """What the VLM read from one face-blurred keyframe, valid until `expires_at`."""

    evidence: tuple[SegmentEvidence, ...]
    held_products: tuple[HeldProduct, ...]
    minor_likely: bool
    minor_likely_confidence: Percentage
    measured_at: UtcTimestamp
    expires_at: UtcTimestamp
    model_version: str

    def is_live(self, now: UtcTimestamp) -> bool:
        return now.value < self.expires_at.value


@dataclass(frozen=True, slots=True, kw_only=True)
class Classification:
    segments: tuple[SegmentEstimate, ...]
    below_threshold: tuple[SegmentEstimate, ...]
    held_products: tuple[HeldProduct, ...]
    minor_likely: bool
    minor_likely_confidence: Percentage


def classify(
    *,
    perception: tuple[SegmentEvidence, ...],
    marker_products: tuple[HeldProduct, ...],
    keyframe: KeyframeFindings | None,
    looking: int,
    minor_likely: bool,
    minor_likely_confidence: Percentage,
    now: UtcTimestamp,
    previously_biddable: frozenset[SegmentId],
    settings: ClassifierSettings,
) -> Classification:
    live_keyframe = keyframe if keyframe is not None and keyframe.is_live(now) else None
    evidence = perception + (live_keyframe.evidence if live_keyframe else ())
    fusion = SegmentFusionPolicy().apply(
        FusionInput(
            evidence=evidence,
            looking=looking,
            now=now,
            previously_biddable=previously_biddable,
            enter_at=settings.enter_at,
            exit_at=settings.exit_at,
        )
    )

    # Markers (reliable) and the VLM's label reading: strongest claim per product.
    held: dict[str, HeldProduct] = {}
    if looking > 0:
        candidates = marker_products + (live_keyframe.held_products if live_keyframe else ())
        for product in candidates:
            if product.confidence.value < settings.held_product_min.value:
                continue
            current = held.get(product.key)
            if current is None or product.confidence.value > current.confidence.value:
                held[product.key] = product

    # Fail safe: either source flagging a minor blocks. It never targets.
    if (
        live_keyframe is not None
        and live_keyframe.minor_likely
        and not minor_likely
        and live_keyframe.minor_likely_confidence.value >= settings.minor_likely_min.value
    ):
        minor_likely, minor_likely_confidence = True, live_keyframe.minor_likely_confidence

    return Classification(
        segments=fusion.biddable,
        below_threshold=fusion.below_threshold,
        held_products=tuple(held[key] for key in sorted(held)),
        minor_likely=minor_likely,
        minor_likely_confidence=minor_likely_confidence,
    )
