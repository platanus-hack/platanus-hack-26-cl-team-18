"""End to end over the real ASGI app with minimum ad seconds ON.

agent socket → audience → AuctionDesk → engine → creative → player socket, with
console.auction alongside. A fixed clock and a manual scheduler make held
swaps deterministic: nothing sleeps.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.rehearse_demo import demo_snapshot
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    CONSOLE_OUTBOUND,
    AudienceSnapshot,
    ConsoleAuction,
    ConsoleBook,
    ConsoleLatency,
    ConsoleReceipt,
    CreativeReady,
    PlaybackReport,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.delivery.domain.charge import ChargeReason

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@dataclass
class ManualScheduler:
    """Collects held releases; `fire_due` runs those whose instant has come."""

    clock: FixedClock
    pending: list[tuple[datetime, Callable[[], None]]] = field(default_factory=list)

    def __call__(self, delay_s: float, callback: Callable[[], None]) -> None:
        self.pending.append((self.clock.now_utc() + timedelta(seconds=delay_s), callback))

    def fire_due(self) -> int:
        now = self.clock.now_utc()
        due = [item for item in self.pending if item[0] <= now]
        self.pending = [item for item in self.pending if item[0] > now]
        for _, callback in due:
            callback()
        return len(due)


@dataclass
class Loop:
    client: TestClient
    clock: FixedClock
    scheduler: ManualScheduler
    player: Any
    console: Any
    agent: Any
    container: Any = None
    seq: int = 0
    play_seq: int = 0

    def next_of(self, kind: type) -> Any:
        while True:
            message = CONSOLE_OUTBOUND.validate_python(self.console.receive_json())
            if isinstance(message, kind):
                return message

    def send(self, scene: str, *, after_s: float = 0.1) -> datetime:
        now = self.clock.advance(timedelta(seconds=after_s))
        self.seq += 1
        self.agent.send_text(snapshot_for(scene, self.seq, now).model_dump_json())
        return now

    def auction(self) -> ConsoleAuction:
        while True:
            message = CONSOLE_OUTBOUND.validate_python(self.console.receive_json())
            if isinstance(message, ConsoleAuction):
                return message

    def latency(self) -> ConsoleLatency:
        while True:
            message = CONSOLE_OUTBOUND.validate_python(self.console.receive_json())
            if isinstance(message, ConsoleLatency):
                return message

    def creative(self) -> CreativeReady:
        return CreativeReady.model_validate(self.player.receive_json())


@pytest.fixture
def loop(tmp_path: Path) -> Iterator[Loop]:
    clock = FixedClock(T0)
    scheduler = ManualScheduler(clock)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, auction_swap_holds=True
    )
    container = build_container(settings, clock=clock, scheduler=scheduler)
    console_ready = threading.Event()
    container.router.on_console_connected(lambda _sink: console_ready.set())
    app = create_app(container=container)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        harness = Loop(client, clock, scheduler, player, console, agent, container)
        assert console_ready.wait(2), "console replay must finish before live decisions start"
        assert paid_brands(harness.creative()) == ()  # the house ad: never blank
        yield harness


def test_first_paid_ad_replaces_the_house_ad_at_once(loop: Loop) -> None:
    sent = loop.send("fitness")
    auction = loop.auction()
    creative = loop.creative()
    assert auction.swap == "swap" and auction.trace.swap_not_before is None
    assert paid_brands(creative) == ("Voltio",)
    assert creative.trace.measured_at == sent and creative.trace.ready_at == sent
    assert loop.scheduler.fire_due() == 0  # no swap is waiting (only the 15 s lease timer)


def test_same_brands_refresh_the_offer_and_held_product_copy(loop: Loop) -> None:
    def send_fixture(scene: str, seq: int, delay: float) -> ConsoleAuction:
        at = loop.clock.advance(timedelta(seconds=delay))
        snapshot = demo_snapshot(scene, "demo-01", seq, 1.0)
        loop.agent.send_text(snapshot.model_copy(update={"measured_at": at}).model_dump_json())
        return loop.auction()

    assert send_fixture("duo", 1, 0.1).swap == "swap"
    duo = loop.creative()
    assert duo.spec.offer is not None and duo.spec.offer.price.amount == "5990"
    assert send_fixture("trio", 2, 0.5).swap == "held"
    loop.clock.advance(timedelta(seconds=1))
    assert loop.scheduler.fire_due() == 1
    assert loop.auction().swap == "released"
    trio = loop.creative()
    assert set(paid_brands(trio)) == set(paid_brands(duo))
    assert trio.spec.offer is not None and trio.spec.offer.price.amount == "8490"
    held_auction = send_fixture("held", 3, 3.1)
    assert held_auction.swap == "swap"
    held = loop.creative()
    assert set(paid_brands(held)) == set(paid_brands(duo)), held_auction.model_dump()
    assert held.spec.angle == "complete_basket"
    assert "A tu Café Patio le falta Voltio" in held.spec.stages[0].copy_text
    assert held.spec.offer == trio.spec.offer


def test_new_group_is_held_to_the_floor_then_released(loop: Loop) -> None:
    shown = loop.send("fitness")
    loop.auction(), loop.creative()

    loop.send("co-brand", after_s=0.5)
    held = loop.auction()
    assert held.swap == "held"
    assert held.trace.swap_not_before == shown + timedelta(seconds=1.5)
    assert {w.brand_name for w in held.winners} == {"Voltio", "Café Patio"}

    loop.clock.advance(timedelta(seconds=0.9))  # 1.4 s after display: still held
    assert loop.scheduler.fire_due() == 0
    loop.clock.advance(timedelta(seconds=0.1))
    assert loop.scheduler.fire_due() == 1

    released = loop.auction()
    creative = loop.creative()
    assert released.swap == "released" and released.auction_id == held.auction_id
    assert set(paid_brands(creative)) == {"Voltio", "Café Patio"}
    assert creative.auction_id == held.auction_id
    # Two people looking: the basket offer is sized to the group (duo tier).
    assert "para los 2" in creative.spec.slots[0].copy_text
    assert "$5.990" in creative.spec.slots[0].copy_text
    # The trace keeps the hold so latency metrics can exclude it.
    assert creative.trace.swap_not_before == held.trace.swap_not_before
    assert creative.trace.ready_at == shown + timedelta(seconds=1.5)
    assert loop.latency().creative_id == creative.creative_id


def test_minor_in_front_of_beer_swaps_immediately(loop: Loop) -> None:
    loop.send("adult")
    loop.auction()
    assert paid_brands(loop.creative()) == ("Malta Sur",)

    loop.send("minor-safe", after_s=0.1)  # well inside any hold
    blocked = loop.auction()
    assert blocked.swap == "swap" and blocked.winners == []
    assert [e.reason for e in blocked.exclusions] == ["safety"]
    assert paid_brands(loop.creative()) == ()
    assert loop.scheduler.fire_due() == 0  # no swap is waiting (only the 15 s lease timer)


def test_a_superseded_hold_never_reaches_the_screen(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()

    loop.send("co-brand", after_s=0.2)
    assert loop.auction().swap == "held"
    loop.send("fitness", after_s=0.2)  # the group went back: same ad already on air
    assert loop.auction().swap == "unchanged"

    loop.clock.advance(timedelta(seconds=5))
    loop.scheduler.fire_due()  # the stale release is dropped by the desk
    loop.send("adult", after_s=0.1)  # a later real change goes through normally
    assert loop.auction().swap == "swap"
    # The next creative is the adult one: no stale basket was queued before it.
    assert paid_brands(loop.creative()) == ("Malta Sur",)


def test_walking_away_keeps_the_ad_for_the_grace_period_then_house_ad(loop: Loop) -> None:
    shown = loop.send("co-brand")
    loop.auction()
    assert set(paid_brands(loop.creative())) == {"Voltio", "Café Patio"}

    left = loop.send("empty", after_s=1.0)
    held = loop.auction()
    assert held.swap == "held" and held.winners == []
    # D5.5: the grace counts from when attention left, not from when the ad went up.
    assert held.trace.swap_not_before == left + timedelta(seconds=3)
    assert left == shown + timedelta(seconds=1)
    loop.clock.set(left + timedelta(seconds=3))
    assert loop.scheduler.fire_due() == 1
    loop.auction()
    assert paid_brands(loop.creative()) == ()


def test_console_money_is_consistent_with_billing_units(loop: Loop) -> None:
    loop.send("co-brand")
    auction = loop.auction()
    loop.creative()
    payments = {
        w.brand_name: Decimal(w.payment_per_s.amount) for w in auction.winners if w.payment_per_s
    }
    prices = {w.brand_name: Decimal(w.price_per_as.amount) for w in auction.winners}
    # price per attention-second × 2 looking = payment per screen-second.
    assert {name: prices[name] * 2 for name in prices} == payments
    assert auction.bundle_revenue is not None and auction.single_brand_revenue is not None
    assert sum(payments.values()) == Decimal(auction.bundle_revenue.amount)
    assert Decimal(auction.bundle_revenue.amount) > Decimal(auction.single_brand_revenue.amount)
    assert Decimal(auction.bundle_value.amount) > Decimal(auction.single_brand_value.amount)
    assert auction.pricing == "core_adjusted" and auction.trigger == "moment_changed"
    for bid in auction.bids:  # no winner pays more per AS than it bid
        if bid.brand_name in prices:
            assert prices[bid.brand_name] <= Decimal(bid.bid_per_as.amount)


def test_full_demo_script_in_order_with_holds(loop: Loop) -> None:
    """docs/demo.md beat order: solo → basket → beer → minor → basket → walk away."""
    expected = [
        ("fitness", {"Voltio"}),
        ("co-brand", {"Voltio", "Café Patio"}),
        ("adult", {"Malta Sur"}),
        ("minor-safe", set()),
        ("co-brand", {"Voltio", "Café Patio"}),
        ("empty", set()),
    ]
    for scene, brands in expected:
        loop.send(scene, after_s=0.2)
        auction = loop.auction()
        if auction.swap == "held":
            assert auction.trace.swap_not_before is not None
            loop.clock.set(auction.trace.swap_not_before)
            assert loop.scheduler.fire_due() == 1
            assert loop.auction().swap == "released"
        else:
            assert auction.swap == "swap", scene
        assert set(paid_brands(loop.creative())) == brands, scene


def test_an_engine_failure_keeps_the_ad_and_the_same_audience_is_retried(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.auction.infrastructure.engines.in_process import InProcessBundleEngine

    real = InProcessBundleEngine.decide
    calls = {"n": 0}

    def flaky(self, opportunity):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("engine down")
        return real(self, opportunity)

    monkeypatch.setattr(InProcessBundleEngine, "decide", flaky)
    loop.send("fitness")  # engine fails: nothing new is pushed, the house ad stays
    loop.send("fitness", after_s=0.1)  # identical audience is re-auctioned, not deduped
    assert loop.auction().swap == "swap"
    assert paid_brands(loop.creative()) == ("Voltio",)
    assert calls["n"] == 2


# ------------------------------------------------------------ bidding war, stock, billing


def campaign_of(loop: Loop, brand: str) -> str:
    book = ConsoleBook.model_validate(loop.client.get("/api/book").json())
    return next(line.campaign_id for line in book.lines if line.brand_name == brand)


def brand_of(loop: Loop, brand: str) -> str:
    book = ConsoleBook.model_validate(loop.client.get("/api/book").json())
    return next(line.brand_id for line in book.lines if line.brand_name == brand)


def slide(loop: Loop, brand: str, multiplier: str, seq: int) -> Any:
    return loop.client.post(
        "/api/advertiser/bid-multiplier",
        json={
            "type": "advertiser.bid_multiplier",
            "v": 1,
            "campaign_id": campaign_of(loop, brand),
            "multiplier": multiplier,
            "seq": seq,
        },
    )


def stock(loop: Loop, brand: str, *, in_stock: bool) -> Any:
    return loop.client.post(
        "/api/clerk/stock",
        json={
            "type": "stock.set",
            "v": 1,
            "screen_id": "demo-01",
            "brand_id": brand_of(loop, brand),
            "in_stock": in_stock,
        },
    )


def played(loop: Loop, creative: CreativeReady, *, seconds: float) -> None:
    """The player reports a finished play window that started at ready_at."""
    assert creative.trace.ready_at is not None
    start = creative.trace.ready_at
    end = start + timedelta(seconds=seconds)
    if loop.clock.now_utc() < end:
        loop.clock.set(end)
    loop.play_seq += 1
    loop.player.send_text(
        PlaybackReport(
            screen_id="demo-01",
            creative_id=creative.creative_id,
            seq=loop.play_seq,
            started_at=start,
            ended_at=end,
        ).model_dump_json()
    )


def test_bidding_war_flips_the_screen_after_the_teaser_hold(loop: Loop) -> None:
    shown = loop.send("fitness")
    loop.auction()
    assert paid_brands(loop.creative()) == ("Voltio",)

    # Rayo ×1.24 values 8.06 vs Voltio 8.00: within the 2 % margin, Voltio stays.
    assert slide(loop, "Rayo", "1.24", 1).json() == {"outcome": "applied"}
    kept = loop.auction()
    assert kept.trigger == "bid_changed" and kept.swap == "keep_incumbent"

    # ×1.5 wins clearly: held one teaser stage (3 s from display), then flips.
    loop.clock.advance(timedelta(seconds=1))
    assert slide(loop, "Rayo", "1.5", 2).json() == {"outcome": "applied"}
    held = loop.auction()
    assert held.swap == "held" and [w.brand_name for w in held.winners] == ["Rayo"]
    assert held.trace.swap_not_before == shown + timedelta(seconds=3)
    loop.clock.set(shown + timedelta(seconds=3))
    assert loop.scheduler.fire_due() == 1
    assert paid_brands(loop.creative()) == ("Rayo",)
    book = loop.next_of(ConsoleBook)
    rayo = next(line for line in book.lines if line.brand_name == "Rayo")
    assert rayo.multiplier == "1.5" and rayo.multiplier_seq == 2


def test_bidding_war_inputs_are_idempotent_bounded_and_validated(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()
    assert slide(loop, "Rayo", "2", 5).json() == {"outcome": "applied"}
    assert slide(loop, "Rayo", "2", 5).json() == {"outcome": "duplicate"}  # retry
    assert slide(loop, "Rayo", "3", 5).status_code == 409  # same seq, other value
    assert slide(loop, "Rayo", "0.5", 4).json() == {"outcome": "stale"}  # late, older
    assert slide(loop, "Rayo", "9", 6).status_code == 422  # above ×5.0
    assert slide(loop, "Rayo", "0.1", 7).status_code == 422  # below ×0.2
    bad = loop.client.post(
        "/api/advertiser/bid-multiplier",
        json={
            "type": "advertiser.bid_multiplier",
            "v": 1,
            "campaign_id": "018f0000-0000-7000-8000-000000000999",
            "multiplier": "1",
            "seq": 1,
        },
    )
    assert bad.status_code == 404
    assert (
        loop.client.post("/api/advertiser/bid-multiplier", json={"campaign_id": "x"}).status_code
        == 422
    )


def test_clerk_stock_out_reforms_the_basket_immediately(loop: Loop) -> None:
    shown = loop.send("co-brand")
    loop.auction()
    assert set(paid_brands(loop.creative())) == {"Voltio", "Café Patio"}

    loop.clock.advance(timedelta(seconds=0.5))
    assert stock(loop, "Café Patio", in_stock=False).json() == {"changed": True}
    out = loop.auction()
    assert out.trigger == "stock_changed" and out.swap == "swap"  # never held
    assert [(e.reason, e.campaign_id) for e in out.exclusions if e.reason == "out_of_stock"]
    assert paid_brands(loop.creative()) == ("Voltio",)
    assert stock(loop, "Café Patio", in_stock=False).json() == {"changed": False}  # no-op

    # Back in stock: a same-moment re-auction, held one teaser stage.
    restocked_at = loop.clock.advance(timedelta(seconds=0.5))
    assert stock(loop, "Café Patio", in_stock=True).json() == {"changed": True}
    back = loop.auction()
    assert back.swap == "held" and back.trace.swap_not_before == restocked_at - timedelta(
        seconds=0.5
    ) + timedelta(seconds=3)
    loop.clock.set(back.trace.swap_not_before)
    loop.scheduler.fire_due()
    assert set(paid_brands(loop.creative())) == {"Voltio", "Café Patio"}
    assert shown < restocked_at


def test_unknown_stock_toggles_are_rejected(loop: Loop) -> None:
    body = {
        "type": "stock.set",
        "v": 1,
        "screen_id": "demo-01",
        "brand_id": "018f0000-0000-7000-8000-000000000999",
        "in_stock": False,
    }
    assert loop.client.post("/api/clerk/stock", json=body).status_code == 404
    assert (
        loop.client.post("/api/clerk/stock", json={**body, "screen_id": "other"}).status_code == 404
    )


def test_a_watched_play_is_billed_and_the_budget_reservation_settles(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction()
    creative = loop.creative()
    assert paid_brands(creative) == ("Voltio",)
    voltio = next(
        l
        for l in ConsoleBook.model_validate(loop.client.get("/api/book").json()).lines
        if l.brand_name == "Voltio"
    )
    assert voltio.on_air and voltio.reserved.amount == "98"  # 6.5 × 1 looking × 15 s, ceiling

    loop.clock.advance(timedelta(seconds=4))
    played(loop, creative, seconds=4)
    receipt = loop.next_of(ConsoleReceipt)
    assert receipt.creative_id == creative.creative_id and receipt.auction_id == creative.auction_id
    assert (receipt.people, receipt.looking) == (1, 1)
    assert receipt.attention_seconds == "4.000" and receipt.played_s == "4.000"
    [charge] = receipt.charges
    # Solo Voltio clears at Rayo's 6.5 CLP/AS: 6.5 × 4 AS = 26 CLP.
    assert (charge.brand_name, charge.price_per_as.amount, charge.amount.amount) == (
        "Voltio",
        "6.5",
        "26",
    )
    assert charge.reason == "attention" and receipt.total.amount == "26"
    book = loop.next_of(ConsoleBook)
    voltio = next(line for line in book.lines if line.brand_name == "Voltio")
    assert (voltio.spent.amount, voltio.reserved.amount, voltio.remaining.amount) == (
        "26",
        "0",
        "49974",
    )
    charges = loop.container.ledger.charges()
    assert [c.amount.amount for c in charges] == [26]


def test_a_glance_under_one_second_is_charged_zero_explicitly(loop: Loop) -> None:
    sent = loop.send("fitness")
    loop.auction()
    creative = loop.creative()
    # Same audience facts (no re-auction), but the tracklet looks away after 0.6 s.
    away = snapshot_for("fitness", 99, sent + timedelta(seconds=0.6))
    away = away.model_copy(
        update={"tracklets": [away.tracklets[0].model_copy(update={"looking": False})]}
    )
    loop.clock.advance(timedelta(seconds=0.6))
    observed = threading.Event()

    def processed(snapshot: AudienceSnapshot, _received: object) -> None:
        if snapshot.seq == away.seq:
            observed.set()

    # Agent and player have independent sockets. Advance the fake world only
    # after the look-away reading has reached delivery, rather than racing it.
    loop.container.router.on(AudienceSnapshot, processed)
    loop.agent.send_text(away.model_dump_json())
    assert observed.wait(2), "look-away observation was not processed"
    loop.clock.advance(timedelta(seconds=4))
    played(loop, creative, seconds=4)
    receipt = loop.next_of(ConsoleReceipt)
    assert receipt.attention_seconds == "0.000" and receipt.total.amount == "0"
    assert [c.reason for c in receipt.charges] == ["no_attention"]
    assert {c.reason for c in loop.container.ledger.charges()} == {ChargeReason.NO_ATTENTION}


def test_the_house_ad_is_never_billed(loop: Loop) -> None:
    loop.send("empty")
    loop.send("fitness", after_s=0.2)
    loop.auction()
    creative = loop.creative()
    house = CreativeReady.model_validate(
        {**creative.model_dump(mode="json"), "creative_id": "house-1", "auction_id": None}
    )
    played(loop, house, seconds=3)
    played(loop, creative, seconds=1)
    receipt = loop.next_of(ConsoleReceipt)  # only the paid creative's report
    assert receipt.creative_id == creative.creative_id
    assert len(loop.container.ledger.charges()) == 1


def test_every_auction_is_recorded(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()
    slide(loop, "Rayo", "2", 1)
    loop.auction()
    records = loop.container.auction_log.records()
    assert [r.trigger.value for r in records] == ["moment_changed", "bid_changed"]
    assert records[0].result.winner_ids != records[1].result.winner_ids


# ---------------------------------------------------------------- audit regressions


def _engine_down(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    from app.modules.auction.infrastructure.engines.in_process import InProcessBundleEngine

    called = threading.Event()

    def down(self, opportunity):  # type: ignore[no-untyped-def]
        called.set()
        raise RuntimeError("engine down")

    monkeypatch.setattr(InProcessBundleEngine, "decide", down)
    return called


def test_engine_down_while_beer_is_held_never_shows_it_to_a_minor(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()
    loop.send("adult", after_s=0.5)
    assert loop.auction().swap == "held"  # Malta Sur waits for the 1.5 s floor
    called = _engine_down(monkeypatch)
    loop.send("minor-safe", after_s=0.2)
    assert called.wait(5)  # the minor's snapshot reached the (failing) engine
    loop.clock.advance(timedelta(seconds=2))
    loop.scheduler.fire_due()  # the stale beer release fires and must be dropped
    monkeypatch.undo()
    loop.send("co-brand", after_s=0.1)
    loop.auction()
    assert set(paid_brands(loop.creative())) == {"Voltio", "Café Patio"}  # not Malta first


def test_engine_down_when_a_minor_meets_a_beer_ad_on_air_falls_back_to_house(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop.send("adult")
    loop.auction()
    assert paid_brands(loop.creative()) == ("Malta Sur",)
    _engine_down(monkeypatch)
    loop.send("minor-safe", after_s=0.2)
    fallback = loop.auction()
    assert fallback.swap == "swap" and fallback.winners == []
    assert paid_brands(loop.creative()) == ()  # safety never depends on the engine


def test_stock_out_of_a_held_winner_never_airs_it(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()
    loop.send("co-brand", after_s=0.5)
    held = loop.auction()
    assert held.swap == "held"
    assert stock(loop, "Café Patio", in_stock=False).json() == {"changed": True}
    loop.auction()  # Voltio alone wins; the basket hold is dropped (refreshed to its Dúo)
    loop.clock.advance(timedelta(seconds=3))
    loop.scheduler.fire_due()
    loop.send("adult", after_s=0.1)
    beer = loop.auction()
    while beer.swap == "released":  # the Voltio refresh ("Dúo Voltio" for the two) aired
        beer = loop.auction()
    assert [w.brand_name for w in beer.winners] == ["Malta Sur"]
    if beer.swap == "held":  # the refresh just aired: the new-group floor applies
        loop.clock.advance(timedelta(seconds=1.5))
        loop.scheduler.fire_due()
    aired = [paid_brands(loop.creative())]
    while aired[-1] != ("Malta Sur",):
        aired.append(paid_brands(loop.creative()))
    assert set(aired[:-1]) <= {("Voltio",)}, aired  # never a stale Café basket


def test_concurrent_slider_calls_settle_on_the_highest_seq(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()
    rayo = campaign_of(loop, "Rayo")
    codes: list[int] = []

    def move(seq: int) -> None:
        response = loop.client.post(
            "/api/advertiser/bid-multiplier",
            json={
                "type": "advertiser.bid_multiplier",
                "v": 1,
                "campaign_id": rayo,
                "multiplier": f"{1 + seq / 10:.1f}",
                "seq": seq,
            },
        )
        codes.append(response.status_code)

    threads = [threading.Thread(target=move, args=(seq,)) for seq in range(1, 21)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(codes) == {200}
    book = ConsoleBook.model_validate(loop.client.get("/api/book").json())
    line = next(l for l in book.lines if l.brand_name == "Rayo")
    assert (line.multiplier, line.multiplier_seq) == ("3", 20)


@pytest.fixture
def poor_loop(tmp_path: Path) -> Iterator[Loop]:
    clock = FixedClock(T0)
    scheduler = ManualScheduler(clock)
    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        media_dir=tmp_path,
        auction_swap_holds=True,
        demo_budget_clp=200,
    )
    container = build_container(settings, clock=clock, scheduler=scheduler)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        harness = Loop(client, clock, scheduler, player, console, agent, container)
        harness.creative()
        yield harness


def test_a_brand_that_cannot_cover_its_next_lease_comes_down_and_is_capped(
    poor_loop: Loop,
) -> None:
    loop = poor_loop
    # Five seconds of simulated time per sample is not a network burst. Wait
    # for receipt of each reading before advancing the fake clock, otherwise
    # the gateway correctly conflates samples and attention has real gaps.
    with loop.client.websocket_connect("/ws/screen/demo-01?attention=1") as observer:
        observer.receive_json()  # last good house creative

        def observed(at: datetime) -> None:
            while True:
                message = observer.receive_json()
                if (
                    message["type"] == "screen.attention"
                    and datetime.fromisoformat(message["measured_at"].replace("Z", "+00:00")) == at
                ):
                    return

        shown = loop.send("fitness", after_s=0)
        loop.auction()
        observed(shown)
        voltio = loop.creative()
        assert paid_brands(voltio) == ("Voltio",)  # 6.5 CLP/AS: each 15 s lease holds 98
        for tick in range(5, 35, 5):  # the same viewer keeps watching
            again = snapshot_for("fitness", 100 + tick, shown + timedelta(seconds=tick))
            loop.clock.set(shown + timedelta(seconds=tick))
            loop.agent.send_text(again.model_dump_json())
            observed(again.measured_at)
            if tick in (15, 30):
                loop.scheduler.fire_due()  # lease renewals
    # Lease 2 (196) fits the 200 budget, lease 3 (294) doesn't: Voltio comes down at once.
    gone = loop.next_of(ConsoleAuction)
    assert gone.trigger == "budget_changed" and gone.swap == "swap"
    voltio_campaign = next(bid.campaign_id for bid in gone.bids if bid.brand_name == "Voltio")
    assert any(e.reason == "budget" and e.campaign_id == voltio_campaign for e in gone.exclusions)
    rayo = loop.creative()
    assert paid_brands(rayo) == ("Rayo",)
    played(loop, voltio, seconds=30)
    receipt = loop.next_of(ConsoleReceipt)
    [charge] = receipt.charges
    assert charge.amount.amount == "195"  # 6.5 × 30 AS, within the 200 budget
    # Neither brand ever pays past its 200 CLP budget, however long the plays.
    totals: dict[str, int] = {}
    for charge in loop.container.ledger.charges():
        name = "Voltio" if str(charge.campaign_id) == voltio_campaign else "other"
        totals[name] = totals.get(name, 0) + int(charge.amount.amount)
    assert totals["Voltio"] <= 200


def test_sensing_boundaries_survive_the_agent_to_player_path(loop: Loop) -> None:
    now = loop.clock.advance(timedelta(seconds=1))
    snapshot = snapshot_for("fitness", 1, now).model_copy(
        update={
            "candidate_at": now - timedelta(milliseconds=600),
            "detected_at": now,
            "published_at": now,
        }
    )
    loop.agent.send_text(snapshot.model_dump_json())
    trace = loop.creative().trace
    assert trace.candidate_at == snapshot.candidate_at
    assert trace.measured_at == now
    assert trace.detected_at == trace.published_at == now


# ---------------------------------------------------------------- auto-bidding + invalid attention


def autobid(loop: Loop, brand: str, *, enabled: bool = True) -> Any:
    return loop.client.post(
        "/api/advertiser/autobid",
        json={
            "type": "advertiser.autobid",
            "v": 1,
            "campaign_id": campaign_of(loop, brand),
            "enabled": enabled,
        },
    )


def book_line(loop: Loop, brand: str) -> Any:
    book = ConsoleBook.model_validate(loop.client.get("/api/book").json())
    return next(line for line in book.lines if line.brand_name == brand)


def test_auto_bidding_chases_an_outbid_brand_until_it_wins_then_yields_to_a_human(
    loop: Loop,
) -> None:
    shown = loop.send("fitness")
    loop.auction()
    assert paid_brands(loop.creative()) == ("Voltio",)
    assert autobid(loop, "Rayo").json() == {"enabled": True}
    flipped_at = None
    for tick in range(1, 9):  # Rayo keeps losing to Voltio while auto-bidding learns
        loop.clock.set(shown + timedelta(seconds=5 * tick))
        loop.send("fitness", after_s=0)  # a heartbeat: same moment, re-observed
        loop.container.demo.reauction_bids()
        loop.scheduler.fire_due()  # the 5 s auto-bid tick (and any due releases)
        line = book_line(loop, "Rayo")
        if Decimal(line.multiplier) * Decimal("6.5") > Decimal("8") * Decimal("1.02"):
            flipped_at = tick
            break
    rayo = book_line(loop, "Rayo")
    assert rayo.autobid and rayo.autobid_rule == "chase_outbid"
    assert "Outbid" in (rayo.autobid_reason or "")
    assert flipped_at is not None and Decimal(rayo.multiplier) <= Decimal("1.5")  # small steps
    # A human takes the slider: auto-bidding turns off and the human value stands.
    assert slide(loop, "Rayo", "0.5", 1_763_740_000_000).json() == {"outcome": "applied"}
    rayo = book_line(loop, "Rayo")
    assert not rayo.autobid and rayo.multiplier == "0.5"


def test_a_far_away_viewer_is_filtered_off_the_bill(loop: Loop) -> None:
    from app.contract.messages import DistanceBand

    sent = loop.send("fitness")
    loop.auction()
    creative = loop.creative()
    far = snapshot_for("fitness", 50, sent + timedelta(seconds=0.2))
    far = far.model_copy(
        update={
            "tracklets": [far.tracklets[0].model_copy(update={"distance_band": DistanceBand.FAR})]
        }
    )
    loop.clock.advance(timedelta(seconds=0.2))
    loop.agent.send_text(far.model_dump_json())
    loop.clock.advance(timedelta(seconds=4))
    played(loop, creative, seconds=4)
    receipt = loop.next_of(ConsoleReceipt)
    # 0.2 s near (a glance) then 3.8 s far: nothing billable, every second explained.
    assert receipt.attention_seconds == "0.000" and receipt.total.amount == "0"
    assert {item.reason for item in receipt.filtered} == {"far", "glance"}


def test_a_failing_auto_bid_tick_keeps_the_loop_alive(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: one bad tick must never stop auto-bidding until a restart."""
    from app.modules.campaigns.application.autobidder import AutoBidder

    loop.send("fitness")
    loop.auction(), loop.creative()
    calls = {"n": 0}
    real = AutoBidder.tick

    def flaky(self, *, at):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad tick")
        return real(self, at=at)

    monkeypatch.setattr(AutoBidder, "tick", flaky)
    autobid(loop, "Rayo")
    autobid(loop, "Rayo")  # enabling twice still runs one loop
    for n in range(1, 4):
        loop.clock.advance(timedelta(seconds=5))
        loop.scheduler.fire_due()
        assert calls["n"] == n  # exactly one tick per period, even after the failure


def test_the_aired_hook_is_credited_with_the_attention_it_earned(tmp_path: Path) -> None:
    """Stock copy (no cached AI draft): approved hooks rotate and each earns its attention."""
    from tests.unit.api.test_ws_gateway import Recorder

    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, creative_ai_copy=False
    )
    container = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        assert paid_brands(CreativeReady.model_validate(player.receive_json())) == ()
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        first = CreativeReady.model_validate(player.receive_json())
        assert first.spec.copy_source is not None and first.spec.copy_source.value == "stock"
        routed = Recorder()
        container.router.on(PlaybackReport, routed)
        player.send_text(
            PlaybackReport(
                screen_id="demo-01",
                creative_id=first.creative_id,
                seq=1,
                started_at=first.trace.ready_at,
                ended_at=clock.advance(timedelta(seconds=4)),
            ).model_dump_json()
        )
        routed.wait_for(1)
        rows = client.get("/api/hooks").json()
    assert len(rows) >= 2  # the targeted hook plus approved alternates are under test
    played = [row for row in rows if row["plays"]]
    assert [(row["variant"], row["hook"]) for row in played] == [
        (0, first.spec.stages[0].copy_text)  # untried hooks air in order, targeted first
    ]
