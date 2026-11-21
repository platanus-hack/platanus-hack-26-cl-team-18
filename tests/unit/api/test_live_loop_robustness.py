"""Adversarial inputs over the live loop: a bad message must never blank or wedge the screen.

Real ASGI app, demo loop wired, fixed clock, manual scheduler. Each test sends
something hostile, then a normal snapshot, and checks the loop still decides.
"""

from __future__ import annotations

import json
import logging
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
from app.contract.messages import (
    CONSOLE_OUTBOUND,
    ConsoleAuction,
    CreativeReady,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

from .test_auction_loop_e2e import ManualScheduler

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@dataclass
class Rig:
    client: TestClient
    clock: FixedClock
    player: Any
    console: Any
    agent: Any
    container: Any
    seq: int = 0

    def snapshot(self, scene: str, *, after_s: float = 0.1, **overrides: object) -> str:
        now = self.clock.advance(timedelta(seconds=after_s))
        self.seq += 1
        data = json.loads(snapshot_for(scene, self.seq, now).model_dump_json())
        data.update(overrides)
        return json.dumps(data)

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
def rig(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Iterator[Rig]:
    clock = FixedClock(T0)
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock, scheduler=ManualScheduler(clock))
    console_ready = threading.Event()
    container.router.on_console_connected(lambda _sink: console_ready.set())
    caplog.set_level(logging.WARNING)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        rig = Rig(client, clock, player, console, agent, container)
        assert console_ready.wait(2)
        assert paid_brands(rig.creative()) == ()  # house ad first
        yield rig
    failures = [r for r in caplog.records if r.getMessage() == "inbound handler failed"]
    assert not failures, [r.exc_info for r in failures]


def test_an_absurd_group_size_neither_crashes_nor_wedges_the_loop(rig: Rig) -> None:
    # 10**22 viewers: the worst-case budget hold excludes everyone (no fill, the
    # house ad keeps playing); the next sane reading must still win the screen.
    rig.agent.send_text(rig.snapshot("fitness", people=10**22, looking=10**22))
    assert rig.auction().winners == []
    rig.agent.send_text(rig.snapshot("fitness"))
    rig.agent.send_text(rig.snapshot("fitness"))
    while not (auction := rig.auction()).winners:
        pass
    assert paid_brands(rig.creative()) == ("Voltio",)


@pytest.mark.parametrize("group", [7, 13, 20, 64])
def test_a_large_real_group_gets_a_paid_ad_with_a_valid_offer(rig: Rig, group: int) -> None:
    tracklets = [
        {"id": f"t{i}", "dwell_s": 2.0, "looking": True, "distance_band": "near"}
        for i in range(min(group, 64))
    ]
    for _ in range(2):  # a confirmed count, not a glance
        rig.agent.send_text(
            rig.snapshot(
                "fitness",
                people=group,
                looking=group,
                tracklets=tracklets,
                objects=[{"label": "sports ball", "score": 1.0}] * min(group, 64),
            )
        )
    auction = rig.auction()
    assert auction.winners, (auction.exclusions, auction.bids)
    creative = rig.creative()
    assert creative.auction_id is not None
    assert creative.spec.offer is None or 1 <= creative.spec.offer.party_size <= 12


def test_empty_room_after_an_ad_returns_to_a_creative_never_blank(rig: Rig) -> None:
    rig.agent.send_text(rig.snapshot("fitness"))
    assert paid_brands(rig.creative()) == ("Voltio",)
    rig.agent.send_text(rig.snapshot("empty", after_s=10))
    rig.agent.send_text(rig.snapshot("empty", after_s=1))
    creative = rig.creative()
    assert creative.spec.slots  # house ad or the last good creative: never empty


def test_infinite_dwell_is_refused_by_the_contract() -> None:
    from app.contract.messages import AGENT_INBOUND

    raw = (
        snapshot_for("fitness", 1, T0).model_dump_json().replace('"dwell_s":1.0', '"dwell_s":1e999')
    )
    with pytest.raises(ValueError):
        AGENT_INBOUND.validate_json(raw)


def test_a_second_player_and_a_reconnect_get_the_ad_on_air_not_the_house_ad(rig: Rig) -> None:
    rig.agent.send_text(rig.snapshot("fitness"))
    on_air = rig.creative()
    assert paid_brands(on_air) == ("Voltio",)
    with rig.client.websocket_connect("/ws/screen/demo-01") as second:
        joined = CreativeReady.model_validate(second.receive_json())
        assert joined.creative_id == on_air.creative_id
    # The second player left: the first still gets the next decision.
    rig.agent.send_text(rig.snapshot("coffee", after_s=5))
    rig.agent.send_text(rig.snapshot("coffee", after_s=0.2))
    nxt = rig.creative()
    assert nxt.creative_id != on_air.creative_id
    with rig.client.websocket_connect("/ws/screen/demo-01") as again:
        assert CreativeReady.model_validate(again.receive_json()).creative_id == nxt.creative_id


def playback(creative_id: str, seq: int, start: datetime, end: datetime) -> str:
    return json.dumps(
        {
            "type": "playback.report",
            "screen_id": "demo-01",
            "creative_id": creative_id,
            "seq": seq,
            "started_at": start.isoformat(),
            "ended_at": end.isoformat(),
        }
    )


def test_odd_playback_reports_never_break_the_player_socket_or_the_loop(rig: Rig) -> None:
    rig.agent.send_text(rig.snapshot("fitness"))
    on_air = rig.creative()
    now = rig.clock.now_utc()
    for seq, (creative_id, start, end) in enumerate(
        [
            ("never-aired", now, now + timedelta(seconds=5)),  # unknown creative
            (on_air.creative_id, now, now),  # zero length
            (on_air.creative_id, now + timedelta(days=365), now + timedelta(days=366)),  # future
            (on_air.creative_id, now - timedelta(days=1), now + timedelta(seconds=3)),  # far past
        ],
        start=1,
    ):
        rig.player.send_text(playback(creative_id, seq, start, end))
    rig.player.send_text(playback(on_air.creative_id, 4, now, now + timedelta(seconds=3)))  # dup
    rig.agent.send_text(rig.snapshot("coffee", after_s=5))
    rig.agent.send_text(rig.snapshot("coffee", after_s=0.2))
    assert rig.creative().creative_id != on_air.creative_id  # the loop still decides


# ------------------------------------------------------------ a vision agent that goes away

HELLO = json.dumps({"type": "agent.hello", "screen_id": "demo-01", "role": "vision"})


def say_hello(rig: Rig) -> None:
    rig.agent.send_text(HELLO)
    assert rig.agent.receive_json()["type"] == "live.status"


def receipt_for(rig: Rig, creative_id: str) -> Any:
    while True:
        message = CONSOLE_OUTBOUND.validate_python(rig.console.receive_json())
        if getattr(message, "type", None) == "console.receipt":
            assert message.creative_id == creative_id  # type: ignore[union-attr]
            return message


def test_a_silent_vision_agent_empties_the_room_and_bills_nothing_for_the_silence(
    rig: Rig,
) -> None:
    say_hello(rig)
    rig.agent.send_text(rig.snapshot("fitness"))
    on_air = rig.creative()
    assert paid_brands(on_air) == ("Voltio",)
    aired_at = rig.clock.now_utc()
    rig.clock.advance(timedelta(seconds=6))  # five heartbeats missed: hung or half-open
    while rig.auction().winners:
        pass
    assert paid_brands(rig.creative()) == ()  # the house ad, never blank
    rig.player.send_text(playback(on_air.creative_id, 1, aired_at, rig.clock.now_utc()))
    receipt = receipt_for(rig, on_air.creative_id)
    assert receipt.total.amount == "0"  # zero attention after the last reading costs zero


def test_a_vision_agent_whose_socket_closes_empties_the_room(rig: Rig) -> None:
    say_hello(rig)
    rig.agent.send_text(rig.snapshot("fitness"))
    assert paid_brands(rig.creative()) == ("Voltio",)
    rig.clock.advance(timedelta(seconds=1))
    rig.agent.close()
    assert paid_brands(rig.creative()) == ()


def test_a_returning_agent_brings_the_ad_back(rig: Rig) -> None:
    say_hello(rig)
    rig.agent.send_text(rig.snapshot("fitness"))
    assert paid_brands(rig.creative()) == ("Voltio",)
    rig.clock.advance(timedelta(seconds=1))
    rig.agent.close()
    assert paid_brands(rig.creative()) == ()
    with rig.client.websocket_connect("/ws/agent") as agent:
        agent.send_text(HELLO)
        assert agent.receive_json()["type"] == "live.status"
        agent.send_text(rig.snapshot("fitness", after_s=0.5))
        assert paid_brands(rig.creative()) == ("Voltio",)


def test_a_script_that_never_says_hello_keeps_its_sparse_scene_on_air(rig: Rig) -> None:
    # Replay / demo insurance sends one snapshot per scene, then goes quiet or exits.
    rig.agent.send_text(rig.snapshot("fitness"))
    on_air = rig.creative()
    rig.clock.advance(timedelta(seconds=30))
    threading.Event().wait(1.2)  # two watchdog polls
    rig.agent.close()
    with rig.client.websocket_connect("/ws/screen/demo-01") as late:
        assert CreativeReady.model_validate(late.receive_json()).creative_id == on_air.creative_id


def test_a_publish_that_fails_is_retried_by_the_next_identical_snapshot(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Composing the creative raises once (a bad kit line, a QR bug...). The audience
    # must not be recorded as handled: the same reading again re-auctions and airs.
    import app.bootstrap.demo as demo

    real = demo.render_spec
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("compose failed once")
        return real(*args, **kwargs)

    monkeypatch.setattr(demo, "render_spec", flaky)
    rig.agent.send_text(rig.snapshot("fitness"))
    rig.agent.send_text(rig.snapshot("fitness"))
    assert paid_brands(rig.creative()) == ("Voltio",)
    assert calls["n"] >= 2
    caplog.clear()  # the one injected failure is expected
