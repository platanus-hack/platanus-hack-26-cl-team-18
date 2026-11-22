"""A quiet link still carries a heartbeat, so a page can tell quiet from dead.

Browsers send no WebSocket pings, and a Wi-Fi link that dies without a reset
keeps the socket "open" for minutes. Measured with evals/network_stress.py: the
console showed "En vivo" over frozen numbers for a whole 30 s partition, and the
TV aired a frozen ad through a 20 s half-open link. The backend now repeats the
current `live.status` to station consoles and TV players; the web socket closes
and reconnects when nothing arrives for a few heartbeats (typed-socket.ts).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import container as container_module
from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.main import create_app


def _types_within(socket: object, seconds: float) -> list[str]:
    seen: list[str] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        seen.append(socket.receive_json()["type"])  # type: ignore[attr-defined]
        if seen.count("live.status") >= 3:
            break
    return seen


def test_station_console_and_tv_get_a_heartbeat_while_nothing_happens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(container_module, "LIVE_HEARTBEAT_S", 0.05)
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    with (
        TestClient(create_app(container=build_container(settings))) as client,
        client.websocket_connect("/ws/console?live=1") as console,
        client.websocket_connect("/ws/screen/demo-01?live=1") as player,
    ):
        # No agent, no decisions: only the catch-up and then heartbeats.
        assert _types_within(console, 5).count("live.status") >= 3
        assert _types_within(player, 5).count("live.status") >= 3


def test_raw_console_readers_never_see_the_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Consumers that read the console stream strictly in order (evals, scripts) did
    # not ask for live.status (?live=1) and must not start receiving it.
    monkeypatch.setattr(container_module, "LIVE_HEARTBEAT_S", 0.02)
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings)
    with TestClient(create_app(container=container)) as client:
        with client.websocket_connect("/ws/screen/demo-01") as player:
            assert player.receive_json()["type"] == "creative.ready"
            time.sleep(0.2)
            sent_before = container.hub.stats.sent
            time.sleep(0.2)
            # Nothing addressed to this plain player: the hub sent it nothing new.
            assert container.hub.stats.sent == sent_before
