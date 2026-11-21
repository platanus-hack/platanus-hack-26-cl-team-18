"""Cross-product scenarios: safety, inventory, exact budgets, reserves and pricing."""

from dataclasses import replace
from decimal import Decimal
from itertools import permutations

import pytest
from hypothesis import given, settings, strategies as st

from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.opportunity import CategoryMultiplier, HeldProduct, PricingRule
from app.modules.auction.domain.result import ExclusionReason, NoFillReason
from app.modules.common.domain.value_objects.percentage import Percentage
from tests.unit.auction.builders import (
    COFFEE,
    FITNESS,
    basket,
    bidder,
    brand,
    campaign,
    moment,
    opportunity,
    segment,
)


@pytest.mark.parametrize("pricing", list(PricingRule))
@pytest.mark.parametrize("minor", [False, True])
@pytest.mark.parametrize("stocked", [False, True])
@pytest.mark.parametrize("budget", [149, 150, 151])  # 5 CLP/AS × 2 looking × 15 seconds
@pytest.mark.parametrize("reserve", ["0", "1.5", "8"])
def test_mixed_market_keeps_safety_budget_and_price_guarantees(
    pricing: PricingRule, minor: bool, stocked: bool, budget: int, reserve: str
) -> None:
    audience = moment(segment(FITNESS), segment(COFFEE), looking=2, minor_likely=minor)
    market = opportunity(
        audience,
        bidder(0, "sports_goods", {FITNESS: 8}, partners=(1,)),
        bidder(1, "coffee", {COFFEE: 5}, partners=(0,), budget=budget),
        bidder(2, "alcohol", {FITNESS: 30}, age_restricted=True),
        bidder(3, "coffee", {COFFEE: 1000}, active=False),
        bidder(4, "coffee", {COFFEE: 1000}, currency="USD"),
        baskets=(basket(0, 1),),
        out_of_stock=() if stocked else (1,),
        reserve=reserve,
    )
    result = clear(market, pricing=pricing)
    expected = (
        {campaign(2)}
        if not minor
        else set()
        if reserve == "8"
        else {campaign(0), campaign(1)}
        if stocked and budget >= 150
        else {campaign(0)}
    )
    assert result.winner_ids == expected
    assert result.filled == bool(expected)
    if not expected:
        assert result.no_fill is NoFillReason.BELOW_RESERVE
    reasons = {item.campaign_id: item.reason for item in result.exclusions}
    assert reasons[campaign(3)] is ExclusionReason.INACTIVE
    assert reasons[campaign(4)] is ExclusionReason.INVALID_BID
    if minor:
        assert reasons[campaign(2)] is ExclusionReason.SAFETY
    if not stocked:
        assert reasons[campaign(1)] is ExclusionReason.OUT_OF_STOCK
    elif budget < 150:
        assert reasons[campaign(1)] is ExclusionReason.BUDGET
    assert len(reasons) == len(result.exclusions)
    assert not result.winner_ids.intersection(reasons)
    assert Decimal(0) <= result.coverage <= Decimal(1)
    for winner in result.winners:
        assert 0 <= winner.payment_per_s.amount <= winner.value_per_s.amount
        assert winner.price_per_as.amount <= max(b.rate.amount for b in winner.bidder.segment_bids)
        assert winner.price_per_as.amount >= Decimal(reserve) * winner.share
    assert result.bundle_revenue.amount == sum(w.payment_per_s.amount for w in result.winners)
    assert (
        clear(replace(market, bidders=tuple(reversed(market.bidders))), pricing=pricing) == result
    )


@settings(max_examples=150, deadline=None)
@given(
    pricing=st.sampled_from(list(PricingRule)),
    looking=st.integers(1, 8),
    confidence=st.integers(1, 100),
    intent=st.integers(0, 100),
    context=st.decimals(min_value="0.2", max_value="5", places=2),
    multiplier=st.decimals(min_value="0.2", max_value="5", places=2),
    bonus=st.integers(0, 10),
)
def test_combined_relevance_signals_never_raise_the_advertisers_contractual_ceiling(
    pricing: PricingRule,
    looking: int,
    confidence: int,
    intent: int,
    context: Decimal,
    multiplier: Decimal,
    bonus: int,
) -> None:
    audience = moment(
        segment(FITNESS, looking, confidence, speech_intent=intent),
        segment(COFFEE, looking, confidence, speech_intent=intent),
        looking=looking,
    )
    market = replace(
        opportunity(
            audience,
            bidder(
                0,
                "sports_goods",
                {FITNESS: 8},
                partners=(1,),
                multiplier=str(multiplier),
                bonuses={1: str(bonus)},
            ),
            bidder(1, "coffee", {COFFEE: 5}, partners=(0,), multiplier=str(multiplier)),
            bidder(2, "sports_goods", {FITNESS: 7}, multiplier="5"),
            baskets=(basket(0, 1),),
        ),
        category_multipliers=(CategoryMultiplier(category="sports_goods", multiplier=context),),
        held_products=(HeldProduct(brand_id=brand(1), confidence=Percentage.create(confidence)),),
    )
    result = clear(market, pricing=pricing)
    on_screen = {winner.bidder.brand_id for winner in result.winners}
    for winner in result.winners:
        ceiling = (
            max(b.rate.amount for b in winner.bidder.segment_bids)
            * winner.bidder.live_multiplier.value
        )
        ceiling += sum(
            b.rate.amount for b in winner.bidder.basket_bonuses if b.partner in on_screen
        )
        # Rates are published at four decimals; allow only that rounding quantum.
        assert winner.price_per_as.amount <= ceiling + Decimal("0.0001")
        assert winner.payment_per_s.amount <= winner.value_per_s.amount + Decimal("0.0001")
        assert winner.payment_per_s.amount >= 0
    for ordered in permutations(market.bidders):
        assert clear(replace(market, bidders=ordered), pricing=pricing) == result
