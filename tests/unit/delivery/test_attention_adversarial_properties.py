"""Independent discrete-time oracles for glances, crowd churn and billing retries."""

from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings, strategies as st

from app.modules.campaigns.application.live_book import LiveCampaignBook
from app.modules.common.application.id_generator import IdGenerator
from app.modules.delivery.application.billing import DeliveryDesk, PlayRecord
from app.modules.delivery.domain.attention import LookSample, measure_attention
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger
from tests.unit.auction.builders import FITNESS, T0, bidder
from tests.unit.delivery.test_attention_and_billing import SLOTS


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.sampled_from((frozenset(), frozenset({"a"}), frozenset({"b"}), frozenset({"a", "b"}))),
        min_size=1,
        max_size=40,
    )
)
def test_crowd_churn_matches_an_independent_half_second_look_oracle(
    crowd: list[frozenset[str]],
) -> None:
    samples = tuple(
        LookSample(at=T0 + timedelta(milliseconds=500 * tick), looking=looking)
        for tick, looking in enumerate(crowd)
    )
    # Every element describes half a second. Runs of at least two elements
    # are billable in full; isolated elements are glances, never rounded up.
    expected_ticks = expected_looks = expected_glances = 0
    for person in ("a", "b"):
        run = 0
        for looking in [*crowd, frozenset()]:
            if person in looking:
                run += 1
            elif run:
                if run >= 2:
                    expected_ticks += run
                    expected_looks += 1
                else:
                    expected_glances += 1
                run = 0
    result = measure_attention(samples, start=T0, end=T0 + timedelta(milliseconds=500 * len(crowd)))
    assert result.attention_seconds == Decimal(expected_ticks) / 2
    assert (result.billable_looks, result.glances) == (expected_looks, expected_glances)
    assert (
        measure_attention(
            tuple(reversed(samples)), start=T0, end=T0 + timedelta(milliseconds=500 * len(crowd))
        )
        == result
    )


@settings(max_examples=150, deadline=None)
@given(
    increments=st.lists(st.integers(1, 5), min_size=1, max_size=12),
    budget=st.integers(0, 100),
    viewers=st.integers(0, 4),
)
def test_overlapping_reports_and_retries_never_duplicate_attention_or_create_money(
    increments: list[int],
    budget: int,
    viewers: int,
) -> None:
    book = LiveCampaignBook(
        [bidder(i, f"category-{i}", {FITNESS: 8}, budget=budget) for i in range(2)]
    )
    ledger = InMemoryChargeLedger()
    billing = DeliveryDesk(ledger=ledger, ids=IdGenerator(), budgets=book)
    billing.aired(
        PlayRecord(
            creative_id="play",
            auction_id="auction",
            screen_id="north",
            people=viewers,
            looking=viewers,
            slots=SLOTS,
            aired_at=T0,
        )
    )
    length = sum(increments)
    for tick in range(length + 1):
        billing.attention.record(
            "north", T0 + timedelta(seconds=tick), (f"t{i}" for i in range(viewers))
        )
    end = 0
    receipts = []
    for index, increment in enumerate(increments):
        end += increment
        report = dict(
            screen_id="north",
            creative_id="play",
            seq=index * 2,
            started_at=T0,
            ended_at=T0 + timedelta(seconds=end),
            recorded_at=T0 + timedelta(seconds=end),
        )
        receipt = billing.record_playback(**report)
        assert receipt is not None
        receipts.append(receipt)
        assert billing.record_playback(**report) is None
        assert billing.record_playback(**{**report, "seq": index * 2 + 1}) is None
    assert sum(receipt.attention.attention_seconds for receipt in receipts) == viewers * length
    for entry in book.entries():
        assert entry.account is not None
        spent = sum(
            charge.amount.amount
            for charge in ledger.charges()
            if charge.campaign_id == entry.bidder.campaign_id
        )
        assert spent == entry.account.spent.amount
        assert 0 <= spent <= budget
        assert entry.account.remaining.amount + spent == budget
    assert len({(charge.dedupe_key, charge.campaign_id) for charge in ledger.charges()}) == len(
        ledger.charges()
    )
