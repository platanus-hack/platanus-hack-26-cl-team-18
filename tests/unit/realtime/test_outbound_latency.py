"""Real hub send boundaries under slow clients and concurrent decisions."""

from __future__ import annotations

import asyncio
import json

import anyio
import pytest

from app.contract.messages import ErrorMessage
from app.realtime.hub import ConnectionHub


class GatedSink:
    def __init__(self, *, blocked: bool = False) -> None:
        self.sent: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.active = 0
        self.peak = 0
        if not blocked:
            self.release.set()

    async def send_text(self, data: str) -> None:
        self.active += 1
        self.peak = max(self.active, self.peak)
        self.entered.set()
        try:
            await self.release.wait()
            self.sent.append(json.loads(data)["code"])
        finally:
            self.active -= 1

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        assert code == 1013
        self.closed.set()


def message(code: str) -> ErrorMessage:
    return ErrorMessage(code=code, message="test")


def test_slow_console_cannot_hold_back_healthy_consoles_or_later_decisions() -> None:
    async def run() -> None:
        hub = ConnectionHub()
        slow, fast = GatedSink(blocked=True), GatedSink()
        hub.add_console(slow)
        hub.add_console(fast)
        first = asyncio.create_task(
            hub.broadcast_console(message("moment"), message("auction"), message("ready"))
        )
        await slow.entered.wait()
        second = asyncio.create_task(hub.broadcast_console(message("next")))
        try:
            async with asyncio.timeout(0.1):
                while len(fast.sent) < 4:
                    await asyncio.sleep(0)
            assert fast.sent == ["moment", "auction", "ready", "next"]
            assert not slow.sent
        finally:
            slow.release.set()
            await asyncio.gather(first, second)

    anyio.run(run)


def test_player_writes_are_ordered_without_overlapping_asgi_sends() -> None:
    async def run() -> None:
        hub, sink = ConnectionHub(), GatedSink(blocked=True)
        hub.add_player("one", sink)
        first = asyncio.create_task(hub.send_to_screen(message("ready"), screen_id="one"))
        await sink.entered.wait()
        second = asyncio.create_task(hub.send_to_screen(message("upgrade"), screen_id="one"))
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            assert sink.peak == 1
        finally:
            sink.release.set()
            await asyncio.gather(first, second)
        assert sink.sent == ["ready", "upgrade"]

    anyio.run(run)


def test_slow_sink_overflow_is_bounded_and_closes_for_reconnect() -> None:
    async def run() -> None:
        hub = ConnectionHub(max_pending_batches=2)
        slow, fast = GatedSink(blocked=True), GatedSink()
        hub.add_player("one", slow)
        hub.add_player("two", fast)
        tasks = [asyncio.create_task(hub.send_to_screen(message("first"), screen_id="one"))]
        await slow.entered.wait()
        tasks += [
            asyncio.create_task(hub.send_to_screen(message(str(i)), screen_id="one"))
            for i in range(20)
        ]
        await hub.send_to_screen(message("healthy"), screen_id="two")
        async with asyncio.timeout(0.2):
            await slow.closed.wait()
            await asyncio.gather(*tasks)
        assert slow.sent == []
        assert fast.sent == ["healthy"]
        assert hub.connected_screens() == ["two"]
        assert hub.stats.dropped_sinks == 1

    anyio.run(run)


def test_timeout_drops_queued_writes_and_closes_the_connection(monkeypatch) -> None:
    monkeypatch.setattr("app.realtime.hub.SEND_TIMEOUT_S", 0.02)

    async def run() -> None:
        hub, sink = ConnectionHub(), GatedSink(blocked=True)
        hub.add_console(sink)
        await hub.broadcast_console(message("one"), message("two"))
        assert sink.closed.is_set()
        assert hub.console_count() == 0
        assert sink.sent == []
        assert hub.stats.dropped_sinks == 1

    anyio.run(run)


def test_disconnect_does_not_resurrect_pending_writes() -> None:
    async def run() -> None:
        hub, sink = ConnectionHub(), GatedSink(blocked=True)
        hub.add_player("one", sink)
        task = asyncio.create_task(hub.send_to_screen(message("old"), screen_id="one"))
        await sink.entered.wait()
        hub.remove_player("one", sink)
        sink.release.set()
        await task
        assert sink.sent == []

    anyio.run(run)


def test_outbound_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ConnectionHub(max_pending_batches=0)


def test_real_agent_player_and_console_continue_while_another_console_is_blocked(tmp_path) -> None:
    import time
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient
    from scripts.replay_audience import snapshot_for

    from app.bootstrap.container import build_container
    from app.config.settings import AppSettings
    from app.main import create_app

    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        creative_ai_copy=False,
        audience_ai=False,
        media_dir=tmp_path,
    )
    container = build_container(settings)
    slow = GatedSink(blocked=True)
    with TestClient(create_app(container=container)) as client:
        client.portal.call(container.hub.add_console, slow)
        with (
            client.websocket_connect("/ws/console") as console,
            client.websocket_connect("/ws/screen/demo-01") as player,
            client.websocket_connect("/ws/agent") as agent,
        ):
            assert player.receive_json()["auction_id"] is None
            started = time.perf_counter()
            agent.send_text(snapshot_for("co-brand", 1, datetime.now(UTC)).model_dump_json())
            paid = player.receive_json()
            assert paid["auction_id"] is not None
            while (message := console.receive_json())["type"] != "console.auction":
                pass
            assert len(message["winners"]) == 2
            assert time.perf_counter() - started < 0.5
            assert not slow.sent
        client.portal.call(container.hub.remove_console, slow)


def test_real_slow_player_is_closed_with_retry_code_and_can_reconnect(monkeypatch) -> None:
    from datetime import UTC, datetime

    from evals.live import live_backend
    from scripts.replay_audience import snapshot_for
    from starlette.websockets import WebSocket
    from websockets.exceptions import ConnectionClosedError
    from websockets.sync.client import connect

    original = WebSocket.send_text

    async def slow_send(self, data):
        if self.query_params.get("slow") == "1":
            await asyncio.sleep(0.2)
        await original(self, data)

    monkeypatch.setattr(WebSocket, "send_text", slow_send)
    monkeypatch.setattr("app.realtime.hub.SEND_TIMEOUT_S", 0.03)
    with live_backend(creative_ai_copy=False, audience_ai=False) as backend:
        with connect(f"{backend.ws_url}/ws/screen/recovery?slow=1") as slow:
            with pytest.raises(ConnectionClosedError) as closed:
                slow.recv(timeout=2)
            assert closed.value.rcvd.code == 1013
        with backend.player("recovery") as recovered, backend.agent() as agent:
            assert json.loads(recovered.recv(timeout=2))["auction_id"] is None
            snapshot = snapshot_for("co-brand", 1, datetime.now(UTC)).model_copy(
                update={"screen_id": "recovery"}
            )
            agent.send(snapshot.model_dump_json())
            paid = json.loads(recovered.recv(timeout=2))
            assert paid["auction_id"] is not None
            assert len(paid["spec"]["slots"]) == 2
