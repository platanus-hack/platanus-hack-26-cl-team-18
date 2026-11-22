"""NTP steps the station laptop's wall clock mid-demo (venue stress, third pass).

Agent and backend share laptop A's clock (ADR 0008 D7), so a step moves the
snapshot's `measured_at` and the server's `received_at` together. The loop must
keep following the room through a step either way: a backward step must not
freeze the screen on the old ad until wall time catches up with the readings
that came before the step.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import CONSOLE_OUTBOUND, ConsoleAuction, CreativeReady
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

from .test_auction_loop_e2e import ManualScheduler

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@dataclass
class Rig:
    clock: FixedClock
    player: Any
    console: Any
    agent: Any
    seq: int = 0

    def send(self, scene: str, *, after_s: float = 0.2) -> None:
        now = self.clock.advance(timedelta(seconds=after_s))
        self.seq += 1
        self.agent.send_text(snapshot_for(scene, self.seq, now).model_dump_json())

    def auction(self) -> ConsoleAuction:
        while True:
            message = CONSOLE_OUTBOUND.validate_python(self.console.receive_json())
            if isinstance(message, ConsoleAuction):
                return message

    def creative(self) -> CreativeReady:
        while True:
            raw = self.player.receive_json()
            if raw.get("type") == "creative.ready":
                return CreativeReady.model_validate(raw)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, auction_swap_holds=False
    )
    container = build_container(settings, clock=clock, scheduler=ManualScheduler(clock))
    console_ready = threading.Event()
    container.router.on_console_connected(lambda _sink: console_ready.set())
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        rig = Rig(clock, player, console, agent)
        assert console_ready.wait(2)
        assert paid_brands(rig.creative()) == ()  # house ad first
        yield rig


def _air(rig: Rig, scene: str, brands: tuple[str, ...]) -> None:
    for _ in range(2):  # a confirmed count, not a glance
        rig.send(scene)
    while not rig.auction().winners:
        pass
    assert paid_brands(rig.creative()) == brands


def test_a_backward_ntp_step_does_not_freeze_the_screen_on_the_old_ad(rig: Rig) -> None:
    _air(rig, "fitness", ("Voltio",))
    # NTP steps laptop A's clock 30 s back: agent and backend stamp the same new time.
    rig.clock.set(rig.clock.now_utc() - timedelta(seconds=30))
    # The room changes right after the step; the screen must follow it now,
    # not after 30 s of readings dropped as "older than the last one".
    for _ in range(2):
        rig.send("adult")
    auction = rig.auction()
    assert auction.winners, "the first readings after the step were dropped"
    assert paid_brands(rig.creative()) != ("Voltio",)


def test_a_forward_ntp_step_keeps_the_loop_deciding(rig: Rig) -> None:
    _air(rig, "fitness", ("Voltio",))
    rig.clock.set(rig.clock.now_utc() + timedelta(seconds=30))
    for _ in range(2):
        rig.send("adult")
    assert rig.auction().winners
    assert paid_brands(rig.creative()) != ("Voltio",)


def test_a_late_reading_from_before_a_reconnect_is_still_dropped(rig: Rig) -> None:
    # The step fix must not reopen the door to out-of-order readings: a stale
    # reading (older than the last, same clock) still never re-decides the screen.
    _air(rig, "fitness", ("Voltio",))
    stale = rig.clock.now_utc() - timedelta(seconds=2)
    rig.seq += 1
    data = json.loads(snapshot_for("adult", rig.seq, stale).model_dump_json())
    rig.agent.send_text(json.dumps(data))
    rig.agent.send_text(json.dumps({**data, "seq": rig.seq + 1}))
    rig.seq += 1
    rig.send("fitness")  # a fresh, unchanged reading: no new decision
    rig.send("fitness")
    # Nothing re-decided: the player still holds Voltio and nothing new arrived.
    rig.player.send_text("{}")  # elicits a system.error, proving the socket is idle
    raw = rig.player.receive_json()
    assert raw["type"] == "system.error", raw
