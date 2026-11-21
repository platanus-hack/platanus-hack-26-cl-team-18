"""A rejected basket budget update must leave every campaign unchanged."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.modules.campaigns.application.live_book import LiveCampaignBook, UnknownCampaign
from app.modules.campaigns.domain.budget import BudgetAccount, BudgetCurrencyMismatch
from app.modules.common.domain.value_objects.money import Money
from tests.unit.auction.builders import FITNESS, T0, bidder, campaign


@pytest.mark.parametrize("operation", ["reserve", "settle", "extend_hold"])
@pytest.mark.parametrize("invalid", ["unknown_campaign", "currency_mismatch"])
def test_invalid_second_member_never_partially_updates_a_basket(
    operation: str, invalid: str
) -> None:
    book = LiveCampaignBook(
        [bidder(i, f"category-{i}", {FITNESS: 8}, budget=1000) for i in range(2)]
    )
    clp = Money.create(currency="CLP", amount=100)
    book.reserve("play", {campaign(0): clp, campaign(1): clp}, at=T0)
    before, version = book.entries(), book.version
    amounts = {
        campaign(0): Money.create(currency="CLP", amount=200),
        campaign(9 if invalid == "unknown_campaign" else 1): (
            clp if invalid == "unknown_campaign" else Money.create(currency="USD", amount=100)
        ),
    }
    error = UnknownCampaign if invalid == "unknown_campaign" else BudgetCurrencyMismatch
    with pytest.raises(error):
        if operation == "settle":
            book.settle("play", amounts)
        elif operation == "reserve":
            book.reserve("play", amounts, at=T0 + timedelta(seconds=1))
        else:
            book.extend_hold("play", amounts, at=T0 + timedelta(seconds=1))
    assert book.entries() == before
    assert book.version == version


@pytest.mark.parametrize("operation", ["open", "reserve", "settle", "capped_charge"])
def test_negative_money_cannot_create_budget_credit(operation: str) -> None:
    account = BudgetAccount.open(campaign(0), Money.create(currency="CLP", amount=100))
    negative = Money.create(currency="CLP", amount=-1)
    with pytest.raises(ValueError, match="negative"):
        if operation == "open":
            BudgetAccount.open(campaign(0), negative)
        elif operation == "reserve":
            account.reserve("play", negative, at=T0)
        elif operation == "settle":
            account.settle("play", negative)
        else:
            account.capped_charge("play", negative)


def test_concurrent_screens_cannot_spend_another_screens_reservation() -> None:
    book = LiveCampaignBook([bidder(0, "coffee", {FITNESS: 8}, budget=1000)])
    book.reserve("protected-screen", {campaign(0): Money.create(currency="CLP", amount=300)}, at=T0)

    def charge(index: int) -> Money:
        return book.charge(f"screen-{index}", campaign(0), Money.create(currency="CLP", amount=100))

    with ThreadPoolExecutor(max_workers=8) as workers:
        amounts = list(workers.map(charge, range(32)))
    assert sum(amount.amount for amount in amounts) == 700
    assert all(0 <= amount.amount <= 100 for amount in amounts)
    protected = book.charge(
        "protected-screen", campaign(0), Money.create(currency="CLP", amount=400)
    )
    assert protected.amount == 300
    [entry] = book.entries()
    assert entry.account is not None
    assert entry.account.spent.amount == 1000
    assert entry.account.remaining.amount == entry.account.reserved.amount == 0
