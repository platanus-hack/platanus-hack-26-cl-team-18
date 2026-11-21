"""Failures at the record boundary must not lose sales or charge a retry twice."""

from datetime import timedelta

import pytest

from app.modules.campaigns.application.live_book import LiveCampaignBook, UnknownCampaign
from app.modules.common.application.id_generator import IdGenerator
from app.modules.delivery.application.billing import DeliveryDesk, PlayRecord
from app.modules.delivery.application.redemptions import RedemptionDesk
from app.modules.delivery.domain.charge import Charge
from app.modules.delivery.domain.redemption import Redemption
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger
from app.modules.delivery.infrastructure.redemptions import InMemoryRedemptionLedger
from tests.unit.auction.builders import FITNESS, bidder
from tests.unit.delivery.test_attention_and_billing import SLOTS, at, desk as billing_desk, report
from tests.unit.delivery.test_redemption import CAMPAIGNS, T0


class InterruptedRedemptionLedger(InMemoryRedemptionLedger):
    def __init__(self, *, after_append: bool) -> None:
        super().__init__()
        self.after_append = after_append
        self.interrupt = True

    def append(self, redemption: Redemption) -> None:
        if self.interrupt:
            self.interrupt = False
            if self.after_append:
                super().append(redemption)
            raise ConnectionError("injected record failure")
        super().append(redemption)


@pytest.mark.parametrize("after_append", [False, True])
@pytest.mark.parametrize("retry_after_s", [1, 601])
def test_redemption_retry_recovers_from_record_failure_without_losing_or_duplicating_the_sale(
    after_append: bool,
    retry_after_s: int,
) -> None:
    ledger = InterruptedRedemptionLedger(after_append=after_append)
    desk = RedemptionDesk(ids=IdGenerator(), ledger=ledger)
    offer = desk.issue(
        auction_id="auction",
        creative_id="creative",
        screen_id="north",
        campaign_ids=CAMPAIGNS,
        brand_names=("Voltio", "Café Patio"),
        offer_text=None,
        at=T0,
    )
    with pytest.raises(ConnectionError):
        desk.redeem(offer.code, "anonymous-nonce", at=T0)
    redemption, created = desk.redeem(
        offer.code.lower(), "anonymous-nonce", at=T0 + timedelta(seconds=retry_after_s)
    )
    assert created
    assert ledger.redemptions() == (redemption,)
    assert redemption.campaign_ids == CAMPAIGNS
    again, created = desk.redeem(offer.code, "anonymous-nonce", at=T0 + timedelta(minutes=11))
    assert not created and again == redemption
    assert ledger.redemptions() == (redemption,)


class InterruptedChargeLedger(InMemoryChargeLedger):
    def __init__(self, *, after_append: bool) -> None:
        super().__init__()
        self.after_append = after_append
        self.interrupt = True

    def append(self, charges: tuple[Charge, ...]) -> None:
        if self.interrupt:
            self.interrupt = False
            if self.after_append:
                super().append(charges)
            raise ConnectionError("injected record failure")
        super().append(charges)


@pytest.mark.parametrize("after_append", [False, True])
def test_billing_retry_recovers_the_same_receipt_without_spending_twice(after_append: bool) -> None:
    ledger = InterruptedChargeLedger(after_append=after_append)
    book = LiveCampaignBook(
        [bidder(i, f"category-{i}", {FITNESS: 8}, budget=1000) for i in range(2)]
    )
    billing = DeliveryDesk(ledger=ledger, ids=IdGenerator(), budgets=book)
    billing.aired(
        PlayRecord(
            creative_id="c1",
            auction_id="auction",
            screen_id="demo-01",
            people=1,
            looking=1,
            slots=SLOTS,
            aired_at=at(0),
        )
    )
    billing.attention.record("demo-01", at(0), ["viewer"])
    with pytest.raises(ConnectionError):
        report(billing)
    spent_after_failure = [entry.account.spent for entry in book.entries() if entry.account]
    receipt = report(billing)
    assert receipt is not None
    assert ledger.charges() == receipt.charges
    assert [entry.account.spent for entry in book.entries() if entry.account] == spent_after_failure
    assert report(billing) is None
    assert len(ledger.charges()) == 2


def test_invalid_second_charge_does_not_spend_the_first_brands_budget() -> None:
    book = LiveCampaignBook([bidder(0, "coffee", {FITNESS: 8}, budget=1000)])
    billing = DeliveryDesk(ledger=InMemoryChargeLedger(), ids=IdGenerator(), budgets=book)
    billing.aired(
        PlayRecord(
            creative_id="c1",
            auction_id="auction",
            screen_id="demo-01",
            people=1,
            looking=1,
            slots=SLOTS,
            aired_at=at(0),
        )
    )
    billing.attention.record("demo-01", at(0), ["viewer"])
    before, version = book.entries(), book.version
    with pytest.raises(UnknownCampaign):
        report(billing)
    assert book.entries() == before
    assert book.version == version


def test_future_playback_cannot_charge_unobserved_time_or_block_the_next_real_report() -> None:
    billing, ledger = billing_desk()
    billing.attention.record("demo-01", at(0), ["viewer"])
    receipt = billing.record_playback(
        screen_id="demo-01",
        creative_id="c1",
        seq=1,
        started_at=at(0),
        ended_at=at(8),
        recorded_at=at(2),
    )
    assert receipt is not None
    assert receipt.played_s == receipt.attention.attention_seconds == 2
    later = billing.record_playback(
        screen_id="demo-01",
        creative_id="c1",
        seq=2,
        started_at=at(0),
        ended_at=at(4),
        recorded_at=at(4),
    )
    assert later is not None and later.attention.attention_seconds == 2
    assert all(charge.played_to <= charge.recorded_at for charge in ledger.charges())
