"""The creative angle: which need of the moment the copy answers (pure policy).

Targeting must not depend on a network model, so the angle is decided here,
deterministically, before any copy is written. Priority (docs/creative-generation.md §2):

1. **Explicit speech intent** ("uf, qué hambre"): someone *said* it.
2. **Held product** of a basket brand: complete the basket.
3. **Queue**: people waiting have time and a reason to add something.
4. **Top segment** by looking viewers × confidence.
5. The basket's lead category; then a generic angle.

An angle only wins if some brand in the basket can serve it, so a hunger
angle never fronts an energy-drink-only ad.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)


class Angle(StrEnum):
    HUNGER = "hunger"
    ENERGY = "energy"
    COFFEE_BREAK = "coffee_break"
    POST_WORKOUT = "post_workout"
    SOCIAL = "social"  # age-restricted categories only
    QUEUE = "queue"
    COMPLETE_BASKET = "complete_basket"
    GENERIC = "generic"


class AngleReason(StrEnum):
    SPEECH = "speech"
    HELD_PRODUCT = "held_product"
    QUEUE = "queue"
    SEGMENT = "segment"
    CATEGORY = "category"
    DEFAULT = "default"


# Which brand categories can credibly answer each angle.
ANGLE_CATEGORIES: Mapping[Angle, frozenset[str]] = {
    Angle.HUNGER: frozenset(
        {
            "food",
            "pastry",
            "burger",
            "snacks",
            "candy",
            "coffee",
            "cola",
            "dairy",
            "juice",
            "ice_cream",
        }
    ),
    Angle.ENERGY: frozenset({"energy_drink", "sports_goods", "sports_drink", "coffee", "water"}),
    Angle.COFFEE_BREAK: frozenset({"coffee", "pastry", "dairy"}),
    Angle.POST_WORKOUT: frozenset(
        {"sports_goods", "energy_drink", "sports_drink", "coffee", "water"}
    ),
    Angle.SOCIAL: frozenset({"alcohol"}),
}

# The lead category's natural angle when no stronger signal exists.
CATEGORY_ANGLE: Mapping[str, Angle] = {
    "coffee": Angle.COFFEE_BREAK,
    "pastry": Angle.HUNGER,
    "food": Angle.HUNGER,
    "burger": Angle.HUNGER,
    "snacks": Angle.HUNGER,
    "candy": Angle.HUNGER,
    "dairy": Angle.HUNGER,
    "juice": Angle.HUNGER,
    "ice_cream": Angle.HUNGER,
    "sports_drink": Angle.POST_WORKOUT,
    "cola": Angle.HUNGER,  # a cold soda answers the burger moment
    "water": Angle.POST_WORKOUT,
    "energy_drink": Angle.ENERGY,
    "sports_goods": Angle.ENERGY,
    "alcohol": Angle.SOCIAL,
}

SPEECH_INTENT_MIN = Decimal("0.5")
QUEUE_MIN = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class AngleChoice:
    angle: Angle
    reason: AngleReason
    segment_id: SegmentId | None = None


def serves(angle: Angle, categories: Iterable[str]) -> bool:
    """Whether a basket with these categories can answer the angle."""
    allowed = ANGLE_CATEGORIES.get(angle)
    if allowed is None:  # queue / complete-the-basket / generic fit any basket
        return True
    return any(category in allowed for category in categories)


def _strength(segment: SegmentEstimate) -> Decimal:
    return Decimal(segment.viewer_count) * segment.confidence.to_ratio()


def choose_angle(
    *,
    segments: tuple[SegmentEstimate, ...],
    segment_angles: Mapping[SegmentId, Angle],
    categories: tuple[str, ...],
    held_in_basket: bool = False,
    queue_length: int = 0,
    minor_likely: bool = False,
) -> AngleChoice:
    """Pick the angle for a basket. `categories` is in slot order (lead first)."""

    def usable(angle: Angle) -> bool:
        if angle is Angle.SOCIAL and minor_likely:
            return False  # invariant 2: never pitch age-restricted moods to a group with a minor
        return serves(angle, categories)

    spoken = sorted(
        (
            s
            for s in segments
            if SegmentSource.SPEECH in s.sources
            and s.speech_intent is not None
            and s.speech_intent.to_ratio() >= SPEECH_INTENT_MIN
            and s.segment_id in segment_angles
        ),
        key=lambda s: (-(s.speech_intent.to_ratio() if s.speech_intent else 0), s.segment_id.value),
    )
    for segment in spoken:
        angle = segment_angles[segment.segment_id]
        if usable(angle):
            return AngleChoice(
                angle=angle, reason=AngleReason.SPEECH, segment_id=segment.segment_id
            )

    if held_in_basket:
        return AngleChoice(angle=Angle.COMPLETE_BASKET, reason=AngleReason.HELD_PRODUCT)
    if queue_length >= QUEUE_MIN:
        return AngleChoice(angle=Angle.QUEUE, reason=AngleReason.QUEUE)

    ranked = sorted(
        (s for s in segments if s.segment_id in segment_angles and s.viewer_count > 0),
        key=lambda s: (-_strength(s), s.segment_id.value),
    )
    for segment in ranked:
        angle = segment_angles[segment.segment_id]
        if usable(angle):
            return AngleChoice(
                angle=angle, reason=AngleReason.SEGMENT, segment_id=segment.segment_id
            )

    for category in categories:
        fallback = CATEGORY_ANGLE.get(category)
        if fallback is not None and usable(fallback):
            return AngleChoice(angle=fallback, reason=AngleReason.CATEGORY)
    return AngleChoice(angle=Angle.GENERIC, reason=AngleReason.DEFAULT)
