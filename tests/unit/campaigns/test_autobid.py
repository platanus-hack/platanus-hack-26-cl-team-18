"""Rule-based auto-bidding: ordered, explainable, bounded."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.campaigns.application.autobidder import PACING_PERIOD, AutoBidder
from app.modules.campaigns.application.live_book import LiveCampaignBook
from app.modules.campaigns.domain.autobid import (
    AutoBidGoal,
    AutoBidInput,
    AutoBidPolicy,
    AutoBidRule,
    PerformanceWindow,
)
from app.modules.campaigns.domain.book import BidMultiplier
from app.modules.common.domain.value_objects.money import Money
from tests.unit.auction.builders import FITNESS, bidder, campaign

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def clp(n: int) -> Money:
    return Money.create(currency="CLP", amount=n)


def window(**overrides) -> PerformanceWindow:  # type: ignore[no-untyped-def]
    base = dict(
        auctions=10,
        wins=3,
        outbid=0,
        zero_attention_wins=0,
        spend=clp(100),
        redemptions=0,
        budget=clp(1000),
        remaining=clp(900),
        elapsed_fraction=Decimal("0.5"),
    )
    return PerformanceWindow(**(base | overrides))


def decide(current: str = "1", goal: AutoBidGoal | None = None, **w):  # type: ignore[no-untyped-def]
    return AutoBidPolicy().apply(
        AutoBidInput(
            current=BidMultiplier.create(current), goal=goal or AutoBidGoal(), window=window(**w)
        )
    )


@pytest.mark.parametrize(
    ("case", "kwargs", "rule", "multiplier"),
    [
        ("budget nearly gone → floor", dict(remaining=clp(40)), AutoBidRule.BUDGET_FLOOR, "0.2"),
        (
            "spending ahead of plan → down 20 %",
            dict(spend=clp(700), elapsed_fraction=Decimal("0.3")),
            AutoBidRule.PACE_DOWN,
            "0.8",
        ),
        ("too little data → hold", dict(auctions=2), AutoBidRule.COLD_START, "1"),
        (
            "half the wins unwatched → ease off",
            dict(wins=4, zero_attention_wins=2),
            AutoBidRule.WASTED_WINS,
            "0.9",
        ),
        (
            "outbid while behind → chase what it takes to win",
            dict(outbid=3, spend=clp(100), win_multiplier_needed=Decimal("1.1")),
            AutoBidRule.CHASE_OUTBID,
            "1.16",
        ),
        (
            "chasing never jumps more than 20 %",
            dict(outbid=3, win_multiplier_needed=Decimal("3")),
            AutoBidRule.CHASE_OUTBID,
            "1.2",
        ),
        ("on plan → hold", dict(), AutoBidRule.HOLD, "1"),
    ],
)
def test_rules_in_order(case, kwargs, rule, multiplier) -> None:  # type: ignore[no-untyped-def]
    decision = decide(**kwargs)
    assert (decision.rule, decision.multiplier.value) == (rule, Decimal(multiplier)), case
    assert decision.reason


def test_cost_per_redemption_targets_steer_the_bid() -> None:
    goal = AutoBidGoal(target_cost_per_redemption=clp(50))
    over = decide(goal=goal, spend=clp(300), redemptions=2)  # $150 per redemption
    assert over.rule is AutoBidRule.OVER_TARGET and over.multiplier.value == Decimal("0.8")
    under = decide(goal=goal, spend=clp(60), redemptions=3)  # $20, and pacing behind
    assert under.rule is AutoBidRule.UNDER_TARGET and under.multiplier.value == Decimal("1.2")


def test_bounds_are_never_crossed() -> None:
    assert decide(current="5", outbid=5).multiplier.value == Decimal(5)
    assert decide(
        current="0.2", spend=clp(900), remaining=clp(100), elapsed_fraction=Decimal("0.1")
    ).multiplier.value == Decimal("0.2")


def test_the_service_learns_applies_and_yields_to_a_human() -> None:
    book = LiveCampaignBook([bidder(0, "sports_goods", {FITNESS: "6.5"}, budget=5000)])
    auto = AutoBidder(book)
    auto.enable(campaign(0), AutoBidGoal(), at=T0)
    assert auto.tick(at=T0 + timedelta(seconds=5)) == {}  # cold start: holds
    for _ in range(4):
        auto.observe_auction(campaign(0), won=False, outbid=True, multiplier_needed=Decimal("1.3"))
    changed = auto.tick(at=T0 + PACING_PERIOD / 2)
    assert changed[campaign(0)].rule is AutoBidRule.CHASE_OUTBID
    live = next(e for e in book.entries() if e.bidder.campaign_id == campaign(0))
    assert live.bidder.live_multiplier.value == Decimal("1.2") and live.multiplier_seq == 1
    # A human slider move (ms seq) always beats the auto move and turns auto off upstream.
    book.set_live_multiplier(campaign(0), BidMultiplier.create(2), 1_763_740_000_000)
    auto.disable(campaign(0))
    assert not auto.enabled(campaign(0)) and auto.tick(at=T0 + PACING_PERIOD) == {}
