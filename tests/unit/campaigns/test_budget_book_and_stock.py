"""Budget reservations, the live bidding-war book, and per-screen stock flags."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.campaigns.application.live_book import (
    RESERVATION_TTL,
    ConflictingMultiplierSeq,
    LiveCampaignBook,
    MultiplierOutcome,
    UnknownCampaign,
)
from app.modules.campaigns.application.stock import StockBoard
from app.modules.campaigns.domain.book import BidMultiplier
from app.modules.campaigns.domain.budget import BudgetAccount, BudgetCurrencyMismatch
from app.modules.common.domain.value_objects.money import Money
from tests.unit.auction.builders import FITNESS, bidder, brand, campaign

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def clp(amount: int) -> Money:
    return Money.create(currency="CLP", amount=amount)


def test_budget_reservations_hold_money_until_the_charge_settles_them() -> None:
    account = BudgetAccount.open(campaign(0), clp(1000))
    account = account.reserve("c1", clp(300), at=T0).reserve("c2", clp(200), at=T0)
    assert account.reserved == clp(500) and account.remaining == clp(500)
    account = account.reserve("c1", clp(250), at=T0)  # same key replaces: idempotent
    assert account.reserved == clp(450)
    account = account.settle("c1", clp(40))
    assert account.spent == clp(40) and account.reserved == clp(200)
    assert account.remaining == clp(760)
    account = account.release("c2")
    assert account.remaining == clp(960)


def test_budget_never_shows_negative_and_rejects_other_currencies() -> None:
    account = BudgetAccount.open(campaign(0), clp(10)).settle("c1", clp(25))
    assert account.remaining == clp(0)
    with pytest.raises(BudgetCurrencyMismatch):
        account.reserve("c2", Money.create(currency="USD", amount=1), at=T0)


def test_unreported_plays_free_their_reservation_after_the_ttl() -> None:
    account = BudgetAccount.open(campaign(0), clp(100)).reserve("c1", clp(60), at=T0)
    assert account.expire(before=T0) is account  # not yet
    assert account.expire(before=T0 + timedelta(seconds=1)).reserved == clp(0)


def book() -> LiveCampaignBook:
    return LiveCampaignBook(
        [
            bidder(0, "sports_goods", {FITNESS: "8"}, budget=1000),
            bidder(1, "energy_drink", {FITNESS: "6.5"}),
        ]
    )


def test_live_multiplier_is_idempotent_by_seq() -> None:
    live = book()
    up = BidMultiplier.create(Decimal("1.5"))
    assert live.set_live_multiplier(campaign(1), up, 1) is MultiplierOutcome.APPLIED
    assert live.set_live_multiplier(campaign(1), up, 1) is MultiplierOutcome.DUPLICATE
    with pytest.raises(ConflictingMultiplierSeq):
        live.set_live_multiplier(campaign(1), BidMultiplier.create(2), 1)
    assert (
        live.set_live_multiplier(campaign(1), BidMultiplier.create(3), 3)
        is MultiplierOutcome.APPLIED
    )
    late = live.set_live_multiplier(campaign(1), BidMultiplier.create(Decimal("0.5")), 2)
    assert late is MultiplierOutcome.STALE  # an older slider position arrived late
    multipliers = {b.campaign_id: b.live_multiplier.value for b in live.snapshot()}
    assert multipliers[campaign(1)] == 3
    with pytest.raises(UnknownCampaign):
        live.set_live_multiplier(campaign(9), up, 1)


def test_multiplier_bounds_are_enforced() -> None:
    for bad in ("0.1", "5.1"):
        with pytest.raises(ValueError):
            BidMultiplier.create(Decimal(bad))


def test_snapshot_bidders_see_budget_net_of_reservations_and_spend() -> None:
    live = book()
    live.reserve("c1", {campaign(0): clp(300)}, at=T0)
    live.settle("c0", {campaign(0): clp(100)})  # a charge for an earlier play
    remaining = {b.campaign_id: b.budget_remaining for b in live.snapshot()}
    assert remaining[campaign(0)] == clp(600)
    assert remaining[campaign(1)] is None  # uncapped house campaign
    live.reserve("c2", {campaign(0): clp(1)}, at=T0 + RESERVATION_TTL + timedelta(seconds=1))
    remaining = {b.campaign_id: b.budget_remaining for b in live.snapshot()}
    assert remaining[campaign(0)] == clp(899)  # c1 expired, c2 held


def test_stock_toggles_report_only_real_changes() -> None:
    board = StockBoard()
    assert board.set("demo-01", brand(0), in_stock=False)
    assert not board.set("demo-01", brand(0), in_stock=False)
    assert board.out_of_stock("demo-01") == {brand(0)}
    assert board.out_of_stock("other") == frozenset()
    assert board.set("demo-01", brand(0), in_stock=True)
    assert board.out_of_stock("demo-01") == frozenset()


def test_a_re_auction_does_not_price_the_incumbent_out_with_its_own_hold() -> None:
    from app.modules.auction.domain.clearing import clear
    from app.modules.auction.domain.result import ExclusionReason
    from tests.unit.auction.builders import moment, opportunity, segment

    live = LiveCampaignBook([bidder(0, "sports_goods", {FITNESS: "8"}, budget=150)])
    live.reserve("on-air", {campaign(0): clp(98)}, at=T0)
    audience = moment(segment(FITNESS))
    naive = clear(opportunity(audience, *live.snapshot()))
    assert naive.exclusion_for(campaign(0)) is not None  # 52 left < 8 × 15 exposure
    assert naive.exclusion_for(campaign(0)).reason is ExclusionReason.BUDGET  # type: ignore[union-attr]
    fair = clear(opportunity(audience, *live.snapshot(excluding_hold="on-air")))
    assert fair.winner_ids == {campaign(0)}


def test_the_exposure_lease_grows_until_the_budget_runs_out() -> None:
    live = LiveCampaignBook([bidder(0, "sports_goods", {FITNESS: "8"}, budget=200)])
    assert live.extend_hold("play", {campaign(0): clp(98)}, at=T0) == frozenset()
    assert live.extend_hold("play", {campaign(0): clp(196)}, at=T0) == frozenset()
    assert live.extend_hold("play", {campaign(0): clp(294)}, at=T0) == {campaign(0)}
    held = next(e for e in live.entries() if e.bidder.campaign_id == campaign(0))
    assert held.account is not None and held.account.reserved == clp(196)  # last good lease
