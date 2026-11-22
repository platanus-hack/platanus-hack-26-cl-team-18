"""A long-running process keeps bounded billing state, without forgetting anything live."""

from app.modules.common.application.id_generator import IdGenerator
from app.modules.delivery.application.billing import (
    MAX_PLAYS_REMEMBERED,
    DeliveryDesk,
    PlayRecord,
)
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger
from tests.unit.delivery.test_attention_and_billing import SLOTS, at, desk, report


def _play(creative_id: str, screen_id: str, seconds: float) -> PlayRecord:
    return PlayRecord(
        creative_id=creative_id,
        auction_id="018f0000-0000-7000-8000-000500000000",
        screen_id=screen_id,
        people=1,
        looking=1,
        slots=SLOTS,
        aired_at=at(seconds),
    )


def test_forgotten_plays_take_their_billing_marks_with_them() -> None:
    billing, _ = desk()
    billing.attention.record("demo-01", at(-1), ["a"])
    assert report(billing) is not None
    assert "c1" in billing._billed_until
    for n in range(MAX_PLAYS_REMEMBERED):
        billing.aired(_play(f"later-{n}", "demo-01", 10 + n))
    assert "c1" not in billing._billed_until and "c1" not in billing._off_air
    assert len(billing._billed_until) <= MAX_PLAYS_REMEMBERED
    assert report(billing, seq=2, end=6) is None  # a forgotten play never bills again


def test_the_live_play_keeps_its_billing_mark() -> None:
    billing, _ = desk()
    billing.attention.record("demo-01", at(-1), ["a"])
    assert report(billing) is not None
    for n in range(MAX_PLAYS_REMEMBERED - 1):
        billing.aired(_play(f"later-{n}", f"other-{n}", 0))
    assert "c1" in billing._billed_until  # still remembered: a resent seq can't rebill
    assert report(billing, seq=2, end=4) is None


def test_a_postgres_mirror_keeps_only_recent_charges_but_the_record_keeps_all() -> None:
    mirror = InMemoryChargeLedger(max_charges=2)
    record = InMemoryChargeLedger()
    billing = DeliveryDesk(ledger=mirror, ids=IdGenerator())
    for n in range(3):
        billing.aired(_play(f"c{n}", f"s{n}", 0))
        receipt = billing.record_playback(
            screen_id=f"s{n}",
            creative_id=f"c{n}",
            seq=1,
            started_at=at(0),
            ended_at=at(4),
            recorded_at=at(4),
        )
        assert receipt is not None
        record.append(receipt.charges)
    assert len(mirror.charges()) == 2  # two brands of the last play only
    assert {c.creative_id for c in mirror.charges()} == {"c2"}
    assert len(record.charges()) == 6


def test_a_play_re_aired_under_the_same_creative_is_never_evicted_while_on_air() -> None:
    billing, _ = desk()
    billing.aired(_play("c-loop", "demo-01", 0))
    for n in range(MAX_PLAYS_REMEMBERED - 1):
        billing.aired(_play(f"later-{n}", f"other-{n}", 1))
    billing.aired(_play("c-loop", "demo-01", 2))  # the same creative airs again
    billing.aired(_play("newest", "elsewhere", 3))  # over the cap: the oldest goes
    assert "c-loop" in billing._plays
    assert "later-0" not in billing._plays
