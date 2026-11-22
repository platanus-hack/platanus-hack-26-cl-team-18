"""Advertiser budget top-ups (self-serve setup): grow-only, idempotent by key, recorded."""

from datetime import UTC, datetime

import pytest

from app.modules.campaigns.application.live_book import (
    ConflictingTopUp,
    LiveCampaignBook,
    TopUpOutcome,
    UncappedCampaign,
    UnknownCampaign,
)
from app.modules.campaigns.domain.book import CampaignId
from app.modules.campaigns.domain.budget import BudgetAccount, BudgetCurrencyMismatch
from app.modules.common.domain.value_objects.money import Money
from tests.unit.auction.builders import FITNESS, bidder, campaign

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def clp(amount: int) -> Money:
    return Money.create(currency="CLP", amount=amount)


def test_a_top_up_grows_the_budget_and_keeps_spend_and_holds() -> None:
    account = BudgetAccount.open(campaign(0), clp(1000)).reserve("c1", clp(300), at=T0)
    account = account.settle("c1", clp(200)).reserve("c2", clp(100), at=T0)
    topped = account.top_up(clp(500))
    assert topped.budget == clp(1500)
    assert topped.spent == clp(200) and topped.reserved == clp(100)
    assert topped.remaining == clp(1200)


@pytest.mark.parametrize("amount", [0, -1])
def test_a_top_up_must_be_positive(amount: int) -> None:
    account = BudgetAccount.open(campaign(0), clp(1000))
    with pytest.raises(ValueError):
        account.top_up(Money.create(currency="CLP", amount=amount))


def test_a_top_up_in_another_currency_fails() -> None:
    account = BudgetAccount.open(campaign(0), clp(1000))
    with pytest.raises(BudgetCurrencyMismatch):
        account.top_up(Money.create(currency="USD", amount=10))


def test_the_book_applies_a_top_up_once_per_key_and_records_the_new_budget() -> None:
    recorded: list[tuple[CampaignId, Money]] = []
    one = bidder(1, "fitness", {FITNESS: 10}, budget=1000)
    book = LiveCampaignBook([one], on_budget=lambda c, b: recorded.append((c, b)))
    version = book.version

    assert book.top_up(one.campaign_id, clp(500), key="k-1") is TopUpOutcome.APPLIED
    assert book.top_up(one.campaign_id, clp(500), key="k-1") is TopUpOutcome.DUPLICATE
    assert book.snapshot()[0].budget_remaining == clp(1500)
    assert recorded == [(one.campaign_id, clp(1500))]
    assert book.version > version

    assert book.top_up(one.campaign_id, clp(250), key="k-2") is TopUpOutcome.APPLIED
    assert book.snapshot()[0].budget_remaining == clp(1750)


def test_a_reused_key_with_another_body_fails_without_moving_money() -> None:
    one = bidder(1, "fitness", {FITNESS: 10}, budget=1000)
    two = bidder(2, "fitness", {FITNESS: 9}, budget=1000)
    book = LiveCampaignBook([one, two])
    book.top_up(one.campaign_id, clp(500), key="k-1")
    with pytest.raises(ConflictingTopUp):
        book.top_up(one.campaign_id, clp(900), key="k-1")
    with pytest.raises(ConflictingTopUp):
        book.top_up(two.campaign_id, clp(500), key="k-1")
    remaining = {b.campaign_id: b.budget_remaining for b in book.snapshot()}
    assert remaining == {one.campaign_id: clp(1500), two.campaign_id: clp(1000)}


def test_unknown_and_uncapped_campaigns_cannot_be_topped_up() -> None:
    uncapped = bidder(1, "fitness", {FITNESS: 10})
    book = LiveCampaignBook([uncapped])
    with pytest.raises(UncappedCampaign):
        book.top_up(uncapped.campaign_id, clp(500), key="k-1")
    with pytest.raises(UnknownCampaign):
        book.top_up(campaign(99), clp(500), key="k-2")
