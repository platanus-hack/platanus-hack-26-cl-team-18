"""Laptop B's console reconnects (Wi-Fi drop, reload) and gets the run's receipts back.

Measured with evals/network_stress.py: the console only summed the receipts it
happened to be connected for, so a reconnect undercounted "Cobrado" and a reload
reset it to $0 while the backend's book had charged the brands. A station
console now receives this run's receipts and redemptions on connect; the web
reducer dedupes them (web/src/features/console/console-state.ts).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import CreativeReady, PlaybackReport, PlayerDisplayed
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

from .test_auction_loop_e2e import ManualScheduler

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def _creative(player: Any) -> CreativeReady:
    while True:
        raw = player.receive_json()
        if raw.get("type") == "creative.ready":
            return CreativeReady.model_validate(raw)


def _until(console: Any, kind: str) -> dict[str, Any]:
    while True:
        raw: dict[str, Any] = console.receive_json()
        if raw["type"] == kind:
            return raw


@pytest.fixture
def app_and_clock(tmp_path: Path) -> Iterator[tuple[TestClient, FixedClock, threading.Event]]:
    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, auction_swap_holds=False
    )
    container = build_container(settings, clock=clock, scheduler=ManualScheduler(clock))
    connected = threading.Event()
    container.router.on_console_connected(lambda _sink: connected.set())
    with TestClient(create_app(container=container)) as client:
        yield client, clock, connected


def test_a_reconnected_station_console_gets_the_runs_receipts(
    app_and_clock: tuple[TestClient, FixedClock, threading.Event],
) -> None:
    client, clock, connected = app_and_clock
    with (
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        house = _creative(player)
        assert house.auction_id is None
        for seq in (1, 2):
            now = clock.advance(timedelta(seconds=0.2))
            agent.send_text(snapshot_for("fitness", seq, now).model_dump_json())
        paid = _creative(player)
        assert paid.auction_id is not None
        shown = clock.now_utc()
        player.send_text(
            PlayerDisplayed(
                screen_id="demo-01", creative_id=paid.creative_id, seq=1, displayed_at=shown
            ).model_dump_json()
        )
        for seq in range(3, 20):  # people keep looking while it plays
            now = clock.advance(timedelta(seconds=0.5))
            agent.send_text(snapshot_for("fitness", seq, now).model_dump_json())
        # The console was offline for all of it (laptop B's Wi-Fi was down).
        connected.clear()
        with client.websocket_connect("/ws/console") as watcher:
            assert connected.wait(2)
            player.send_text(
                PlaybackReport(
                    screen_id="demo-01",
                    creative_id=paid.creative_id,
                    seq=2,
                    started_at=shown,
                    ended_at=clock.now_utc(),
                ).model_dump_json()
            )
            live_receipt = _until(watcher, "console.receipt")
        # Laptop B comes back: its station console must get that receipt on connect.
        connected.clear()
        with client.websocket_connect("/ws/console?live=1") as console:
            assert connected.wait(2)
            replayed = _until(console, "console.receipt")
    assert replayed == live_receipt
    assert float(replayed["total"]["amount"]) > 0


def test_a_plain_console_reader_gets_no_replay(
    app_and_clock: tuple[TestClient, FixedClock, threading.Event],
) -> None:
    # Readers of the raw console stream (evals, scripts) never asked for station
    # extras; replaying old receipts to them would read as new charges.
    client, _clock, connected = app_and_clock
    with client.websocket_connect("/ws/console") as console:
        assert connected.wait(2)
        console.send_text("{}")  # answered with system.error: proves nothing else queued
        assert console.receive_json()["type"] == "system.error"
