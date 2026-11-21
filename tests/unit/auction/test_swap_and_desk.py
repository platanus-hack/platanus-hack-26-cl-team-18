"""Minimum ad seconds, hysteresis and the per-screen desk (docs/auction-plan.md §9, D5/D8)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.modules.auction.application.desk import AuctionDesk
from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import AuctionResult
from app.modules.auction.domain.swap import SwapAction, Trigger
from app.modules.auction.infrastructure.engines.in_process import InProcessBundleEngine
from tests.unit.auction.builders import (
    BEER,
    CAFE,
    COFFEE,
    DEMO_BASKETS,
    FITNESS,
    MALTA,
    T0,
    VOLTIO,
    brand,
    campaign,
    demo_bidders,
    moment,
    opportunity,
    segment,
)

FITNESS_ONLY = moment(segment(FITNESS))
CO_BRAND = moment(segment(FITNESS), segment(COFFEE), looking=2)


def at(seconds: float):  # type: ignore[no-untyped-def]
    return T0 + timedelta(seconds=seconds)


def desk_on_air(first: AdOpportunity, *, holds: bool = True) -> AuctionDesk:
    desk = AuctionDesk(InProcessBundleEngine(), holds_enabled=holds)
    outcome = desk.run(first, Trigger.MOMENT_CHANGED, now=at(0))
    assert outcome.swap_now  # nothing sold on air: a paid result goes up at once
    desk.aired(outcome.result, displayed_at=at(0))
    return desk


def ids(result: AuctionResult | None) -> set[int]:
    assert result is not None
    return {int(str(w.campaign_id.value)[-8:]) for w in result.winners}


def test_new_group_waits_for_the_one_and_a_half_second_floor() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    outcome = desk.run(
        opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS),
        Trigger.MOMENT_CHANGED,
        now=at(0.5),
    )
    assert not outcome.swap_now and outcome.not_before == at(1.5)
    assert outcome.hold_token is not None
    assert ids(desk.release(outcome.hold_token)) == {VOLTIO, CAFE}


def test_same_moment_triggers_hold_one_full_teaser_stage() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    outcome = desk.run(
        opportunity(FITNESS_ONLY, *demo_bidders(rayo_multiplier="2")),
        Trigger.BID_CHANGED,
        now=at(1),
    )
    assert outcome.action is SwapAction.SWAP and outcome.not_before == at(3)


def test_a_minor_replaces_an_age_restricted_ad_immediately() -> None:
    adult = moment(segment(BEER))
    desk = desk_on_air(opportunity(adult, *demo_bidders()))
    assert desk.on_air is not None and desk.on_air.campaign_ids == {campaign(MALTA)}
    outcome = desk.run(
        opportunity(replace(adult, minor_likely=True), *demo_bidders()),
        Trigger.MOMENT_CHANGED,
        now=at(0.1),
    )
    assert outcome.swap_now and outcome.result is not None and not outcome.result.filled


def test_a_stock_out_of_a_winner_swaps_immediately() -> None:
    desk = desk_on_air(opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS))
    outcome = desk.run(
        replace(
            opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS),
            out_of_stock=frozenset({brand(CAFE)}),
        ),
        Trigger.STOCK_CHANGED,
        now=at(0.2),
    )
    assert outcome.swap_now and ids(outcome.result) == {VOLTIO}


def test_audience_leaving_keeps_the_ad_for_a_grace_period() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    outcome = desk.run(
        opportunity(moment(looking=0), *demo_bidders()), Trigger.MOMENT_CHANGED, now=at(1)
    )
    assert outcome.not_before == at(3)


def test_a_held_decision_superseded_during_the_hold_is_never_released() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    first = desk.run(
        opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS),
        Trigger.MOMENT_CHANGED,
        now=at(0.2),
    )
    second = desk.run(
        opportunity(moment(segment(COFFEE)), *demo_bidders()), Trigger.MOMENT_CHANGED, now=at(0.4)
    )
    assert first.hold_token is not None and second.hold_token is not None
    assert desk.release(first.hold_token) is None
    assert ids(desk.release(second.hold_token)) == {CAFE}


def test_the_same_basket_again_is_unchanged_and_drops_a_pending_swap() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    held = desk.run(
        opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS),
        Trigger.MOMENT_CHANGED,
        now=at(0.2),
    )
    back = desk.run(opportunity(FITNESS_ONLY, *demo_bidders()), Trigger.MOMENT_CHANGED, now=at(0.3))
    assert back.action is SwapAction.UNCHANGED
    assert held.hold_token is not None and desk.release(held.hold_token) is None


@pytest.mark.parametrize(
    ("multiplier", "action"),
    [("1.24", SwapAction.KEEP_INCUMBENT), ("1.3", SwapAction.SWAP)],
)
def test_bidding_war_needs_a_two_percent_margin_to_flip(
    multiplier: str, action: SwapAction
) -> None:
    # Rayo ×1.24 values 8.06 vs Voltio 8.00 (+0.75 %): within hysteresis, Voltio stays.
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    outcome = desk.run(
        opportunity(FITNESS_ONLY, *demo_bidders(rayo_multiplier=multiplier)),
        Trigger.BID_CHANGED,
        now=at(5),
    )
    assert outcome.action is action


def test_new_group_has_no_hysteresis() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    outcome = desk.run(
        opportunity(FITNESS_ONLY, *demo_bidders(rayo_multiplier="1.24")),
        Trigger.MOMENT_CHANGED,
        now=at(5),
    )
    assert outcome.swap_now


def test_holds_disabled_swaps_at_once() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()), holds=False)
    outcome = desk.run(
        opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS),
        Trigger.MOMENT_CHANGED,
        now=at(0.1),
    )
    assert outcome.swap_now


def test_an_engine_failure_keeps_the_current_creative() -> None:
    class Broken:
        def decide(self, opportunity: AdOpportunity) -> AuctionResult:
            raise RuntimeError("boom")

    desk = AuctionDesk(Broken())
    outcome = desk.run(
        opportunity(FITNESS_ONLY, *demo_bidders()), Trigger.MOMENT_CHANGED, now=at(0)
    )
    assert outcome.result is None and outcome.action is SwapAction.UNCHANGED


# ---------------------------------------------------------------- audit regressions


@pytest.mark.parametrize(
    "audience",
    [
        moment(segment(FITNESS), minor_likely=True),  # the beer segment is gone
        moment(looking=0, minor_likely=True),  # nobody looking, minor present
    ],
    ids=["beer-segment-gone", "nobody-looking"],
)
def test_a_minor_ends_a_beer_ad_at_once_even_without_a_safety_exclusion(audience) -> None:  # type: ignore[no-untyped-def]
    desk = desk_on_air(opportunity(moment(segment(BEER)), *demo_bidders()))
    outcome = desk.run(opportunity(audience, *demo_bidders()), Trigger.MOMENT_CHANGED, now=at(0.2))
    assert outcome.swap_now


def test_a_stock_out_ends_the_on_air_ad_even_when_its_segment_is_gone() -> None:
    desk = desk_on_air(opportunity(moment(segment(BEER)), *demo_bidders()))
    outcome = desk.run(
        replace(opportunity(FITNESS_ONLY, *demo_bidders()), out_of_stock=frozenset({brand(MALTA)})),
        Trigger.STOCK_CHANGED,
        now=at(0.2),
    )
    assert outcome.swap_now and ids(outcome.result) == {VOLTIO}


def test_an_on_air_campaign_dropped_from_the_book_is_replaced_at_once() -> None:
    desk = desk_on_air(opportunity(FITNESS_ONLY, *demo_bidders()))
    without_voltio = tuple(b for b in demo_bidders() if b.campaign_id != campaign(VOLTIO))
    outcome = desk.run(opportunity(FITNESS_ONLY, *without_voltio), Trigger.BID_CHANGED, now=at(0.2))
    assert outcome.swap_now
