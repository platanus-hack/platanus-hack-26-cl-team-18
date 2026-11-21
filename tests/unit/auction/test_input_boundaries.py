"""Reject ambiguous auction facts before they can affect selection or exposure."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.auction.domain.opportunity import CategoryMultiplier
from app.modules.campaigns.domain.book import BasketBonus
from tests.unit.auction.builders import FITNESS, bidder, brand, moment, opportunity, rate, segment


@pytest.mark.parametrize("duration", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_play_exposure_requires_a_positive_finite_duration(duration: str) -> None:
    original = opportunity(moment(segment(FITNESS)), bidder(0, "coffee", {FITNESS: 8}))
    with pytest.raises(ValueError, match="play"):
        replace(original, max_play_seconds=Decimal(duration))


def test_a_campaign_cannot_enter_the_same_auction_twice() -> None:
    campaign = bidder(0, "coffee", {FITNESS: 8})
    with pytest.raises(ValueError, match="campaign"):
        opportunity(moment(segment(FITNESS)), campaign, replace(campaign, active=False))


def test_context_cannot_supply_two_multipliers_for_the_same_category() -> None:
    original = opportunity(moment(segment(FITNESS)))
    with pytest.raises(ValueError, match="categor"):
        replace(
            original,
            category_multipliers=(
                CategoryMultiplier(category="coffee", multiplier=Decimal(1)),
                CategoryMultiplier(category="coffee", multiplier=Decimal(5)),
            ),
        )


def test_a_partner_bonus_cannot_be_counted_twice() -> None:
    campaign = bidder(0, "coffee", {FITNESS: 8}, partners=(1,))
    bonus = BasketBonus(partner=brand(1), rate=rate(2))
    with pytest.raises(ValueError, match="bonus"):
        replace(campaign, basket_bonuses=(bonus, bonus))
