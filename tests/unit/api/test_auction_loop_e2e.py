"""End to end over the real ASGI app with minimum ad seconds ON.

agent socket → audience → AuctionDesk → engine → creative → player socket, with
console.auction alongside. A fixed clock and a manual scheduler make held
swaps deterministic: nothing sleeps.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    CONSOLE_OUTBOUND,
    ConsoleAuction,
    ConsoleLatency,
    CreativeReady,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

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
    seq: int = 0

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
    app = create_app(container=build_container(settings, clock=clock, scheduler=scheduler))
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        harness = Loop(client, clock, scheduler, player, console, agent)
        assert paid_brands(harness.creative()) == ()  # the house ad: never blank
        yield harness


def test_first_paid_ad_replaces_the_house_ad_at_once(loop: Loop) -> None:
    sent = loop.send("fitness")
    auction = loop.auction()
    creative = loop.creative()
    assert auction.swap == "swap" and auction.trace.swap_not_before is None
    assert paid_brands(creative) == ("Voltio",)
    assert creative.trace.measured_at == sent and creative.trace.ready_at == sent
    assert loop.scheduler.pending == []


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
    assert "$3.490" in creative.spec.slots[0].copy_text
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
    assert loop.scheduler.pending == []


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

    loop.send("empty", after_s=1.0)
    held = loop.auction()
    assert held.swap == "held" and held.winners == []
    assert held.trace.swap_not_before == shown + timedelta(seconds=3)
    loop.clock.set(shown + timedelta(seconds=3))
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
