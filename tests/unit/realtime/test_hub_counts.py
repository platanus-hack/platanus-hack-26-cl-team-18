"""Worker threads read who is watching from counts, never from the loop-owned sets."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime

from app.contract.messages import (
    AgentRole,
    CreativeReady,
    CreativeSlot,
    CreativeSpec,
    LatencyTrace,
)
from app.realtime.hub import ConnectionHub


class Sink:
    async def send_text(self, data: str) -> None:
        return None


def test_station_and_dev_counts_follow_every_registration() -> None:
    hub = ConnectionHub()
    raw, station, dev, player = Sink(), Sink(), Sink(), Sink()
    assert not hub.has_station_consoles() and hub.dev_count() == 0
    hub.add_console(raw)  # a raw reader (no ?live=1) is not a station console
    assert not hub.has_station_consoles()
    hub.add_player("demo-01", player, live=True)  # a live player is not one either
    assert not hub.has_station_consoles()
    hub.add_console(station, live=True)
    assert hub.has_station_consoles()
    hub.remove_console(station)
    assert not hub.has_station_consoles()
    hub.add_dev(dev)
    assert hub.dev_count() == 1 and hub.has_station_consoles()
    hub.remove_dev(dev)
    assert hub.dev_count() == 0 and not hub.has_station_consoles()


def test_reads_from_worker_threads_never_race_set_mutation() -> None:
    hub = ConnectionHub()
    errors: list[BaseException] = []
    stop = threading.Event()

    def read() -> None:
        try:
            while not stop.is_set():
                hub.has_station_consoles()
                hub.dev_count()
        except BaseException as exc:  # "Set changed size during iteration" before the fix
            errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(4)]
    for reader in readers:
        reader.start()
    sinks = [Sink() for _ in range(200)]
    for _ in range(20):
        for sink in sinks:
            hub.add_console(sink, live=True)
            hub.add_dev(sink)
        for sink in sinks:
            hub.remove_console(sink)
            hub.remove_dev(sink)
    stop.set()
    for reader in readers:
        reader.join()
    assert errors == []
    assert not hub.has_station_consoles() and hub.dev_count() == 0


def test_screens_roles_and_station_consoles_are_snapshots_safe_for_worker_threads() -> None:
    hub = ConnectionHub()
    errors: list[BaseException] = []
    stop = threading.Event()
    probe = Sink()

    def read() -> None:
        try:
            while not stop.is_set():
                hub.connected_screens()
                hub.agent_roles("s-1")
                hub.is_station_console(probe)
        except BaseException as exc:  # "dictionary changed size during iteration"
            errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(4)]
    for reader in readers:
        reader.start()
    sinks = [Sink() for _ in range(100)]
    for _ in range(10):
        for index, sink in enumerate(sinks):
            hub.add_player(f"s-{index}", sink)
            hub.add_agent(f"s-{index}", AgentRole.VISION, sink)
            hub.add_console(sink, live=True)
        for index, sink in enumerate(sinks):
            hub.remove_player(f"s-{index}", sink)
            hub.remove_agent(sink)
            hub.remove_console(sink)
    stop.set()
    for reader in readers:
        reader.join()
    assert errors == []
    assert hub.connected_screens() == [] and hub.agent_roles("s-1") == []


def test_snapshots_follow_registration() -> None:
    hub = ConnectionHub()
    player, agent, console = Sink(), Sink(), Sink()
    hub.add_player("demo-01", player)
    hub.add_agent("demo-01", AgentRole.SPEECH, agent)
    hub.add_agent("demo-01", AgentRole.VISION, Sink())
    hub.add_console(console, live=True)
    assert hub.connected_screens() == ["demo-01"]
    assert hub.agent_roles("demo-01") == sorted([AgentRole.SPEECH, AgentRole.VISION])
    assert hub.is_station_console(console) and not hub.is_station_console(player)
    assert hub.remove_agent(agent) == ["demo-01"]
    assert hub.agent_roles("demo-01") == [AgentRole.VISION]
    hub.remove_player("demo-01", player)
    hub.remove_console(console)
    assert hub.connected_screens() == [] and not hub.is_station_console(console)


class Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


def _basket_creative() -> CreativeReady:
    return CreativeReady(
        screen_id="demo-01",
        creative_id="cr-1",
        spec=CreativeSpec(
            layout_id="duo",
            slots=[
                CreativeSlot(
                    brand_id="b1", brand_name="Voltio", logo_url="/kits/v.svg", copy_text="Dale."
                )
            ],
            duration_ms=8000,
            redemption_code="AB12CD",
            redeem_url="https://x.example/r/AB12CD",
        ),
        trace=LatencyTrace(measured_at=datetime(2026, 11, 21, 15, tzinfo=UTC)),
    )


def test_view_only_players_never_receive_the_redemption_code() -> None:
    hub = ConnectionHub()
    billing, viewer = Recorder(), Recorder()
    hub.add_player("demo-01", billing)
    hub.add_player("demo-01", viewer, redact_redemption=True)
    asyncio.run(hub.send_to_screen(_basket_creative()))
    (full,) = billing.sent
    (redacted,) = viewer.sent
    assert full["spec"]["redemption_code"] == "AB12CD"  # type: ignore[index]
    assert redacted["spec"]["redemption_code"] is None  # type: ignore[index]
    assert redacted["spec"]["redeem_url"] is None  # type: ignore[index]
    assert redacted["spec"]["slots"] == full["spec"]["slots"]  # type: ignore[index]


def test_without_redacted_players_every_player_gets_the_same_creative() -> None:
    hub = ConnectionHub()
    one, two = Recorder(), Recorder()
    hub.add_player("demo-01", one)
    hub.add_player("demo-01", two)
    asyncio.run(hub.send_to_screen(_basket_creative()))
    assert one.sent == two.sent and one.sent[0]["spec"]["redemption_code"] == "AB12CD"  # type: ignore[index]


def test_one_socket_can_serve_vision_and_speech() -> None:
    """The /screen page (ADR 0010) says hello twice on one socket; neither role is lost."""
    hub = ConnectionHub()
    page = Sink()
    hub.add_agent("demo-01", AgentRole.VISION, page)
    hub.add_agent("demo-01", AgentRole.SPEECH, page)
    assert hub.agent_roles("demo-01") == sorted([AgentRole.SPEECH, AgentRole.VISION])
    assert hub.remove_agent(page) == ["demo-01"]
    assert hub.agent_roles("demo-01") == []
