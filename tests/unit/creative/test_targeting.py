"""Angle policy and group-sized offers: the targeting that never depends on a model."""

from __future__ import annotations

import pytest

from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.creative.domain.angle import Angle, AngleReason, choose_angle
from tests.unit.creative.factory import OFFERS, PATIO, VOLTIO

FOOD = SegmentId.create("FOOD")
FIT = SegmentId.create("FIT")
COFFEE = SegmentId.create("COFFEE")
BEER = SegmentId.create("BEER")
ANGLES = {
    FOOD: Angle.HUNGER,
    FIT: Angle.POST_WORKOUT,
    COFFEE: Angle.COFFEE_BREAK,
    BEER: Angle.SOCIAL,
}


def seg(
    segment: SegmentId,
    viewers: int,
    confidence: int = 90,
    *,
    speech: int | None = None,
) -> SegmentEstimate:
    sources = (
        frozenset({SegmentSource.SPEECH})
        if speech is not None
        else frozenset({SegmentSource.VISION})
    )
    return SegmentEstimate.create(
        segment_id=segment,
        viewer_count=viewers,
        confidence=Percentage.create(confidence),
        sources=sources,
        speech_intent=Percentage.create(speech) if speech is not None else None,
    )


@pytest.mark.parametrize(
    ("segments", "categories", "kwargs", "angle", "reason"),
    [
        # "uf, qué hambre" beats a stronger vision segment.
        (
            (seg(FIT, 3), seg(FOOD, 1, speech=90)),
            ("energy_drink", "coffee"),
            {},
            Angle.HUNGER,
            AngleReason.SPEECH,
        ),
        # Weak speech intent does not count as explicit.
        (
            (seg(FIT, 3), seg(FOOD, 1, speech=20)),
            ("energy_drink", "coffee"),
            {},
            Angle.POST_WORKOUT,
            AngleReason.SEGMENT,
        ),
        # Hunger cannot front a basket that sells no food.
        ((seg(FOOD, 1, speech=90),), ("energy_drink",), {}, Angle.ENERGY, AngleReason.CATEGORY),
        (
            (seg(FIT, 2),),
            ("coffee", "pastry"),
            {"held_in_basket": True},
            Angle.COMPLETE_BASKET,
            AngleReason.HELD_PRODUCT,
        ),
        ((seg(FIT, 2),), ("coffee",), {"queue_length": 3}, Angle.QUEUE, AngleReason.QUEUE),
        (
            (seg(COFFEE, 1), seg(FIT, 2)),
            ("energy_drink", "coffee"),
            {},
            Angle.POST_WORKOUT,
            AngleReason.SEGMENT,
        ),
        # A minor blocks the social angle even when it is the top signal.
        ((seg(BEER, 2),), ("alcohol",), {"minor_likely": True}, Angle.GENERIC, AngleReason.DEFAULT),
        ((), ("coffee",), {}, Angle.COFFEE_BREAK, AngleReason.CATEGORY),
        ((), ("unknown",), {}, Angle.GENERIC, AngleReason.DEFAULT),
    ],
)
def test_angle_policy(segments, categories, kwargs, angle, reason) -> None:
    choice = choose_angle(segments=segments, segment_angles=ANGLES, categories=categories, **kwargs)
    assert (choice.angle, choice.reason) == (angle, reason)


@pytest.mark.parametrize(
    ("party", "label", "price"),
    [
        (0, "Combo post-entreno", 3490),
        (1, "Combo post-entreno", 3490),
        (2, "Dúo post-entreno", 5990),
        (3, "Trío post-entreno", 8480),
        (4, "Pack x4 post-entreno", 10970),
        (9, "Pack x4 post-entreno", 10970),
    ],
)
def test_offer_is_sized_to_the_group(party: int, label: str, price: int) -> None:
    offer = OFFERS.for_party(frozenset({VOLTIO, PATIO}), party)
    assert offer is not None
    assert (offer.label, int(offer.price.amount)) == (label, price)


def test_no_offer_for_an_unapproved_basket() -> None:
    assert OFFERS.for_party(frozenset({VOLTIO}), 2) is None


def test_price_text_is_chilean() -> None:
    offer = OFFERS.for_party(frozenset({VOLTIO, PATIO}), 4)
    assert offer is not None and offer.price_text == "$10.970"
