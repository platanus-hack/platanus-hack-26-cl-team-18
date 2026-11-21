"""Attention-seconds with the 1-second minimum look, charges, and idempotent billing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.campaigns.domain.book import BrandId, CampaignId
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.delivery.application.billing import (
    ConflictingPlaybackReport,
    DeliveryDesk,
    PlayRecord,
)
from app.modules.delivery.domain.attention import LookSample, measure_attention
from app.modules.delivery.domain.charge import BilledSlot, ChargeReason, price_play
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def look(seconds: float, *ids: str) -> LookSample:
    return LookSample(at=at(seconds), looking=frozenset(ids))


@pytest.mark.parametrize(
    ("case", "samples", "window", "seconds", "billable", "glances"),
    [
        ("one viewer, whole play", [look(0, "a")], (0, 8), "8", 1, 0),
        ("two viewers, both whole play", [look(0, "a", "b")], (0, 8), "16", 2, 0),
        ("a 0.9 s glance bills nothing", [look(0, "a"), look(0.9)], (0, 8), "0", 0, 1),
        ("exactly 1 s counts in full", [look(0, "a"), look(1)], (0, 8), "1", 1, 0),
        (
            "a look in progress counts from play start only",
            [look(-5, "a"), look(0.5)],
            (0, 8),
            "0",
            0,
            1,
        ),
        ("look started before play", [look(-5, "a")], (0, 3), "3", 1, 0),
        (
            "looking away breaks the look",
            [look(0, "a"), look(0.6), look(1.2, "a")],
            (0, 2),
            "0",
            0,
            2,
        ),
        (
            "same look across snapshots continues",
            [look(0, "a"), look(0.5, "a"), look(1, "a")],
            (0, 1.5),
            "1.5",
            1,
            0,
        ),
        ("a silent agent stops counting after 10 s", [look(0, "a")], (0, 30), "10", 1, 0),
        ("nobody looking", [look(0)], (0, 8), "0", 0, 0),
        ("no samples at all", [], (0, 8), "0", 0, 0),
        ("empty window", [look(0, "a")], (5, 5), "0", 0, 0),
        ("samples after the play are ignored", [look(9, "a")], (0, 8), "0", 0, 0),
    ],
)
def test_attention_seconds(
    case: str,
    samples: list[LookSample],
    window: tuple[float, float],
    seconds: str,
    billable: int,
    glances: int,
) -> None:
    attention = measure_attention(samples, start=at(window[0]), end=at(window[1]))
    assert attention.attention_seconds == Decimal(seconds), case
    assert (attention.billable_looks, attention.glances) == (billable, glances), case


def test_attention_rounds_down_to_the_millisecond() -> None:
    attention = measure_attention([look(0, "a")], start=at(0), end=at(1.0004999))
    assert attention.attention_seconds == Decimal("1.000")


SLOTS = (
    BilledSlot(
        campaign_id=CampaignId.create(UUID("018f0000-0000-7000-8000-000200000000")),
        brand_id=BrandId.create(UUID("018f0000-0000-7000-8000-000100000000")),
        price_per_as=MoneyRate.create(currency="CLP", amount="2.275"),
    ),
    BilledSlot(
        campaign_id=CampaignId.create(UUID("018f0000-0000-7000-8000-000200000001")),
        brand_id=BrandId.create(UUID("018f0000-0000-7000-8000-000100000001")),
        price_per_as=MoneyRate.create(currency="CLP", amount="1.2"),
    ),
)


def test_every_brand_in_the_basket_pays_its_price_times_the_play_attention() -> None:
    priced = price_play(SLOTS, Decimal("7.4"))
    assert [p.amount for p in priced] == [
        Money.create(currency="CLP", amount=17),  # 16.835 → 17
        Money.create(currency="CLP", amount=9),  # 8.88 → 9
    ]
    assert {p.reason for p in priced} == {ChargeReason.ATTENTION}


def test_zero_attention_is_an_explicit_zero_charge() -> None:
    priced = price_play(SLOTS, Decimal(0))
    assert [p.amount.amount for p in priced] == [0, 0]
    assert {p.reason for p in priced} == {ChargeReason.NO_ATTENTION}


def desk() -> tuple[DeliveryDesk, InMemoryChargeLedger]:
    ledger = InMemoryChargeLedger()
    billing = DeliveryDesk(ledger=ledger, ids=IdGenerator())
    billing.aired(
        PlayRecord(
            creative_id="c1",
            auction_id="018f0000-0000-7000-8000-000500000000",
            screen_id="demo-01",
            people=2,
            looking=2,
            slots=SLOTS,
        )
    )
    return billing, ledger


def report(billing: DeliveryDesk, *, seq: int = 1, end: float = 4, creative: str = "c1"):  # type: ignore[no-untyped-def]
    return billing.record_playback(
        screen_id="demo-01",
        creative_id=creative,
        seq=seq,
        started_at=at(0),
        ended_at=at(end),
        recorded_at=at(end),
    )


def test_a_play_bills_measured_attention_and_writes_the_ledger() -> None:
    billing, ledger = desk()
    billing.attention.record("demo-01", at(-1), ["a", "b"])
    receipt = report(billing)
    assert receipt is not None
    assert receipt.attention.attention_seconds == 8  # 2 viewers × 4 s
    assert receipt.played_s == 4
    assert [c.amount.amount for c in receipt.charges] == [18, 10]  # 18.2 → 18, 9.6 → 10
    assert receipt.total == Money.create(currency="CLP", amount=28)
    assert ledger.charges() == receipt.charges
    assert {c.dedupe_key for c in receipt.charges} == {"demo-01:c1:1"}


def test_a_retried_report_bills_once_and_a_conflicting_one_fails() -> None:
    billing, ledger = desk()
    billing.attention.record("demo-01", at(-1), ["a"])
    assert report(billing) is not None
    assert report(billing) is None  # same key, same window: idempotent
    assert len(ledger.charges()) == 2
    with pytest.raises(ConflictingPlaybackReport):
        report(billing, end=5)  # same key, different window


def test_house_ads_unknown_creatives_and_implausible_windows_bill_nothing() -> None:
    billing, ledger = desk()
    assert report(billing, creative="house") is None
    assert report(billing, seq=2, end=-1) is None  # ends before it starts
    assert report(billing, seq=3, end=601) is None  # longer than 10 minutes
    assert ledger.charges() == ()


def test_nobody_looked_charges_zero_explicitly() -> None:
    billing, ledger = desk()
    billing.attention.record("demo-01", at(-1), [])
    receipt = report(billing)
    assert receipt is not None and receipt.total.amount == 0
    assert {c.reason for c in ledger.charges()} == {ChargeReason.NO_ATTENTION}
