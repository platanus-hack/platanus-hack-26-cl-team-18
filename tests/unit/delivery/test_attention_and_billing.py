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
            aired_at=at(0),
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


# ---------------------------------------------------------------- audit regressions


def test_a_window_is_billed_only_for_air_time_and_only_once() -> None:
    billing, ledger = desk()  # c1 aired at T+0
    billing.attention.record("demo-01", at(-5), ["a"])
    billing.attention.record("demo-01", at(3), ["a"])  # still looking: held to T+13
    early = billing.record_playback(
        screen_id="demo-01",
        creative_id="c1",
        seq=1,
        started_at=at(-20),
        ended_at=at(4),
        recorded_at=at(4),
    )
    assert early is not None and early.attention.attention_seconds == 4  # not the 20 s before
    again = billing.record_playback(
        screen_id="demo-01",
        creative_id="c1",
        seq=2,
        started_at=at(0),
        ended_at=at(4),
        recorded_at=at(4),
    )
    assert again is None  # the same seconds under a new seq bill nothing
    overlap = billing.record_playback(
        screen_id="demo-01",
        creative_id="c1",
        seq=3,
        started_at=at(2),
        ended_at=at(6),
        recorded_at=at(6),
    )
    assert overlap is not None and overlap.attention.attention_seconds == 2  # only 4..6
    assert len(ledger.charges()) == 4  # two brands × two billed windows


def test_a_charge_that_rounds_to_zero_still_says_attention() -> None:
    priced = price_play(SLOTS[1:], Decimal("0.4"))  # 1.2 × 0.4 = 0.48 → 0 CLP
    assert priced[0].amount.amount == 0 and priced[0].reason is ChargeReason.ATTENTION


def test_a_brand_never_pays_past_its_budget() -> None:
    from app.modules.audience.domain.value_objects.segment import SegmentId
    from app.modules.campaigns.application.live_book import LiveCampaignBook
    from app.modules.campaigns.domain.book import Bidder, SegmentBid

    book = LiveCampaignBook(
        [
            Bidder(
                campaign_id=slot.campaign_id,
                brand_id=slot.brand_id,
                category=f"c{index}",
                age_restricted=False,
                segment_bids=(
                    SegmentBid(segment_id=SegmentId.create("S"), rate=slot.price_per_as),
                ),
                budget_remaining=Money.create(currency="CLP", amount=300),
            )
            for index, slot in enumerate(SLOTS)
        ]
    )
    ledger = InMemoryChargeLedger()
    billing = DeliveryDesk(ledger=ledger, ids=IdGenerator(), budgets=book)
    billing.aired(
        PlayRecord(
            creative_id="long",
            auction_id="a",
            screen_id="demo-01",
            people=1,
            looking=1,
            slots=SLOTS,
            aired_at=at(0),
        )
    )
    billing.attention.record("demo-01", at(0), ["a"])
    for tick in range(10, 130, 10):  # a fresh sample every 10 s: one viewer watching 120 s
        billing.attention.record("demo-01", at(tick), ["a"])
    receipt = billing.record_playback(
        screen_id="demo-01",
        creative_id="long",
        seq=1,
        started_at=at(0),
        ended_at=at(120),
        recorded_at=at(120),
    )
    assert receipt is not None and receipt.attention.attention_seconds == 120
    amounts = {c.campaign_id: (c.amount.amount, c.reason) for c in receipt.charges}
    # Voltio 2.275 × 120 = 273 (fits); Café 1.2 × 120 = 144 (fits). A second 20 s
    # play would cost Voltio 46, but only 27 is left: it is capped there.
    assert amounts[SLOTS[0].campaign_id] == (273, ChargeReason.ATTENTION)
    billing.aired(
        PlayRecord(
            creative_id="long2",
            auction_id="a2",
            screen_id="demo-01",
            people=1,
            looking=1,
            slots=SLOTS,
            aired_at=at(120),
        )
    )
    for tick in (130, 140):
        billing.attention.record("demo-01", at(tick), ["a"])
    capped = billing.record_playback(  # 20 s would cost Voltio 46; only 27 is left
        screen_id="demo-01",
        creative_id="long2",
        seq=1,
        started_at=at(120),
        ended_at=at(140),
        recorded_at=at(140),
    )
    assert capped is not None
    first = next(c for c in capped.charges if c.campaign_id == SLOTS[0].campaign_id)
    assert (first.amount.amount, first.reason) == (27, ChargeReason.BUDGET_CAP)  # 300 − 273
    spent = {b.campaign_id: b.budget_remaining for b in book.snapshot()}
    assert spent[SLOTS[0].campaign_id] == Money.create(currency="CLP", amount=0)


def test_a_replaced_creative_never_bills_time_after_it_left_the_screen() -> None:
    # Money audit: the player's clock ran ahead (or its report was late), so the
    # window of a creative replaced at T+5 ended at T+60. Those 55 s belong to the
    # next creative's attention: billing them here charged the same seconds twice.
    billing, ledger = desk()
    billing.attention.record("demo-01", at(-1), ["a"])
    billing.attention.record("demo-01", at(4), ["a"])
    billing.attention.record("demo-01", at(9), ["a"])
    billing.off_air("demo-01", at=at(5))
    receipt = billing.record_playback(
        screen_id="demo-01",
        creative_id="c1",
        seq=1,
        started_at=at(0),
        ended_at=at(60),
        recorded_at=at(60),
    )
    assert receipt is not None and receipt.attention.attention_seconds == 5
    assert {c.played_to for c in ledger.charges()} == {at(5)}


def test_a_report_for_a_creative_replaced_before_it_aired_bills_nothing() -> None:
    billing, ledger = desk()
    billing.off_air("demo-01", at=at(0))
    billing.attention.record("demo-01", at(0), ["a"])
    assert report(billing, end=8) is None
    assert ledger.charges() == ()


def test_a_report_that_bills_nothing_for_a_play_off_air_still_frees_its_hold() -> None:
    # Money audit: a play replaced the instant it aired (or a report clamped to an
    # empty window) returned None before settling, so its worst-case hold kept
    # shrinking the brand's budget until some unrelated reservation swept it.
    from app.modules.audience.domain.value_objects.segment import SegmentId
    from app.modules.campaigns.application.live_book import LiveCampaignBook
    from app.modules.campaigns.domain.book import Bidder, SegmentBid

    book = LiveCampaignBook(
        [
            Bidder(
                campaign_id=slot.campaign_id,
                brand_id=slot.brand_id,
                category=f"c{index}",
                age_restricted=False,
                segment_bids=(
                    SegmentBid(segment_id=SegmentId.create("S"), rate=slot.price_per_as),
                ),
                budget_remaining=Money.create(currency="CLP", amount=300),
            )
            for index, slot in enumerate(SLOTS)
        ]
    )
    billing = DeliveryDesk(ledger=InMemoryChargeLedger(), ids=IdGenerator(), budgets=book)
    billing.aired(
        PlayRecord(
            creative_id="c1",
            auction_id="a",
            screen_id="demo-01",
            people=1,
            looking=1,
            slots=SLOTS,
            aired_at=at(0),
        )
    )
    hold = Money.create(currency="CLP", amount=50)
    book.reserve("c1", {slot.campaign_id: hold for slot in SLOTS}, at=at(0))
    billing.off_air("demo-01", at=at(0))  # replaced in the same instant
    assert report(billing, end=0) is None
    for entry in book.entries():
        assert entry.account is not None
        assert entry.account.reserved.amount == 0
        assert entry.account.spent.amount == 0
