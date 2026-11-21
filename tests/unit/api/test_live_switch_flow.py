"""The console's "En vivo" switch over the real ASGI app.

agent (hello) <-> backend <-> player (?live=1) and console (?live=1), with a fixed
clock. Standby means nothing sensed reaches the loop, the screen airs the house ad,
and agents are told to pause; going live resumes the full loop at once.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import Container, build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AGENT_OUTBOUND,
    CONSOLE_OUTBOUND,
    PLAYER_OUTBOUND,
    AgentHello,
    AgentRole,
    AudienceUtterance,
    ConsoleAuction,
    ConsoleNext,
    CreativeReady,
    LiveStatus,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@dataclass
class Station:
    client: TestClient
    clock: FixedClock
    container: Container
    player: Any
    console: Any
    agent: Any
    seq: int = 0

    def hello(self, role: AgentRole = AgentRole.VISION) -> LiveStatus:
        self.agent.send_text(AgentHello(screen_id="demo-01", role=role).model_dump_json())
        return self.agent_status()

    def agent_status(self) -> LiveStatus:
        message = AGENT_OUTBOUND.validate_python(self.agent.receive_json())
        assert isinstance(message, LiveStatus)
        return message

    def set_live(self, **values: bool) -> LiveStatus:
        response = self.client.post("/api/live", json={"screen_id": "demo-01", **values})
        assert response.status_code == 200, response.text
        return LiveStatus.model_validate(response.json())

    def send(self, scene: str, *, after_s: float = 0.1) -> datetime:
        now = self.clock.advance(timedelta(seconds=after_s))
        self.seq += 1
        self.agent.send_text(snapshot_for(scene, self.seq, now).model_dump_json())
        return now

    def player_next(self, kind: type) -> Any:
        while True:
            message = PLAYER_OUTBOUND.validate_python(self.player.receive_json())
            if isinstance(message, kind):
                return message

    def console_next(self, kind: type, until: Callable[[Any], bool] = lambda _m: True) -> Any:
        """The next console message of `kind` that satisfies `until` (older ones skipped)."""
        while True:
            message = CONSOLE_OUTBOUND.validate_python(self.console.receive_json())
            if isinstance(message, kind) and until(message):
                return message


def _station(tmp_path: Path, **settings: Any) -> Iterator[Station]:
    clock = FixedClock(T0)
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path, **settings), clock=clock
    )
    console_ready = threading.Event()
    container.router.on_console_connected(lambda _sink: console_ready.set())
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01?live=1") as player,
        client.websocket_connect("/ws/console?live=1") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        station = Station(client, clock, container, player, console, agent)
        assert console_ready.wait(2)
        assert isinstance(station.player_next(LiveStatus), LiveStatus)  # on connect
        assert paid_brands(station.player_next(CreativeReady)) == ()  # the house ad
        yield station


@pytest.fixture
def standby(tmp_path: Path) -> Iterator[Station]:
    yield from _station(tmp_path, live_starts_on=False, speech_mode_screens=["demo-01"])


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Station]:
    yield from _station(tmp_path, live_starts_on=True, speech_mode_screens=["demo-01"])


def test_an_agent_learns_the_switch_position_when_it_says_hello(standby: Station) -> None:
    status = standby.hello()
    assert not status.sensing and not status.listening
    assert status.agents == [AgentRole.VISION]
    standby.console_next(LiveStatus, lambda m: m.agents == [AgentRole.VISION])


def test_standby_drops_sensing_and_going_live_resumes_the_loop(standby: Station) -> None:
    standby.hello()
    standby.send("fitness")  # dropped at the socket: never auctioned, never observed
    health = standby.client.get("/health").json()
    assert health["live"]["dropped_in_standby"] == 1
    assert health["inputs"]["demo-01"]["camera_recent"] is False  # never observed

    status = standby.set_live(live=True)
    assert status.sensing and status.listening and status.ai_generation and status.revision == 1
    assert standby.agent_status().sensing  # pushed to the agent
    assert standby.player_next(LiveStatus).listening  # the screen's mic indicator
    assert standby.console_next(LiveStatus, lambda m: m.revision == 1).sensing

    sent = standby.send("fitness")
    auction = standby.console_next(ConsoleAuction)
    assert auction.trace.measured_at == sent  # the first auction is the live snapshot
    assert paid_brands(standby.player_next(CreativeReady)) == ("Voltio",)


def test_going_off_airs_the_house_ad_and_tells_the_agent(live: Station) -> None:
    assert live.hello().sensing
    live.send("fitness")
    assert paid_brands(live.player_next(CreativeReady)) == ("Voltio",)

    status = live.set_live(live=False)
    assert not status.sensing and not status.listening and not status.ai_generation
    assert not live.agent_status().sensing
    assert not live.player_next(LiveStatus).listening
    assert paid_brands(live.player_next(CreativeReady)) == ()  # never blank: the house ad

    live.send("fitness")  # an in-flight snapshot after the click is dropped
    assert live.client.get("/health").json()["live"]["dropped_in_standby"] == 1


def test_a_repeated_click_changes_nothing(live: Station) -> None:
    first = live.set_live(live=True)
    assert first.revision == 0  # already live: a no-op, nobody is notified
    assert live.set_live(live=False).revision == 1
    assert live.set_live(live=False).revision == 1


def test_mic_off_drops_utterances_but_keeps_the_camera(live: Station) -> None:
    status = live.set_live(mic=False)
    assert status.sensing and not status.listening
    live.agent.send_text(
        AudienceUtterance(
            screen_id="demo-01",
            seq=1,
            measured_at=live.clock.now_utc(),
            text="quiero un café",
            language="es",
            keyterms_hit=["café"],
        ).model_dump_json()
    )
    live.send("fitness")
    assert paid_brands(live.player_next(CreativeReady)) == ("Voltio",)  # camera still live
    body = live.client.get("/health").json()
    assert body["live"]["dropped_in_standby"] == 1
    assert body["inputs"]["demo-01"]["speech_recent"] is False
    assert "café" not in str(body)


def test_agents_leaving_are_reflected_on_the_console(standby: Station) -> None:
    with standby.client.websocket_connect("/ws/agent") as speech:
        speech.send_text(AgentHello(screen_id="demo-01", role=AgentRole.SPEECH).model_dump_json())
        assert LiveStatus.model_validate(speech.receive_json()).agents == [AgentRole.SPEECH]
        standby.console_next(LiveStatus, lambda m: m.agents == [AgentRole.SPEECH])
    standby.console_next(LiveStatus, lambda m: m.agents == [])
    assert standby.client.get("/api/live").json()["agents"] == []


def test_clients_without_the_live_subscription_see_an_unchanged_stream(tmp_path: Path) -> None:
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path, live_starts_on=False)
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
    ):
        assert PLAYER_OUTBOUND.validate_python(player.receive_json()).type == "creative.ready"
        client.post("/api/live", json={"screen_id": "demo-01", "live": True})
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_text(snapshot_for("fitness", 1, datetime.now(UTC)).model_dump_json())
            # The first message after the toggle is the creative, not a live.status.
            first = PLAYER_OUTBOUND.validate_python(player.receive_json())
            assert isinstance(first, CreativeReady) and paid_brands(first) == ("Voltio",)


def test_the_switch_rejects_unknown_fields_and_bad_screens(live: Station) -> None:
    assert (
        live.client.post("/api/live", json={"screen_id": "demo-01", "on": True}).status_code == 422
    )
    assert live.client.post("/api/live", json={"screen_id": "a b", "live": True}).status_code == 422
    assert live.client.get("/api/live?screen_id=a%20b").status_code == 422


def test_production_has_no_live_switch(tmp_path: Path) -> None:
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path, live_ads_enabled=False)
    )
    container.settings = container.settings.model_copy(update={"app_env": "prod"})
    with TestClient(create_app(container=container)) as client:
        assert client.get("/api/live").status_code == 404
        assert (
            client.post("/api/live", json={"screen_id": "demo-01", "live": True}).status_code == 404
        )


def test_a_connected_player_in_standby_is_reported_connected(standby: Station) -> None:
    inputs = standby.client.get("/health").json()["inputs"]
    assert inputs["demo-01"]["player_connected"] is True
    assert inputs["demo-01"]["camera_recent"] is False


def test_a_station_console_sees_what_airs_and_the_book_even_in_standby(tmp_path: Path) -> None:
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path, live_starts_on=False)
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console?live=1") as station,
    ):
        seen: dict[str, object] = {}
        while not {"creative.ready", "console.book"} <= seen.keys():
            message = CONSOLE_OUTBOUND.validate_python(station.receive_json())
            seen[message.type] = message
        house = seen["creative.ready"]
        assert isinstance(house, CreativeReady) and paid_brands(house) == ()
        assert {line.brand_name for line in seen["console.book"].lines}  # type: ignore[attr-defined]

        client.post("/api/live", json={"screen_id": "demo-01", "live": True})
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_text(snapshot_for("fitness", 1, datetime.now(UTC)).model_dump_json())
            while True:
                message = CONSOLE_OUTBOUND.validate_python(station.receive_json())
                if isinstance(message, CreativeReady) and paid_brands(message):
                    break
        assert paid_brands(message) == ("Voltio",)


def test_a_plain_console_stream_is_unchanged(tmp_path: Path) -> None:
    container = build_container(AppSettings(_env_file=None, app_env="test", media_dir=tmp_path))
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        agent.send_text(snapshot_for("fitness", 1, datetime.now(UTC)).model_dump_json())
        kinds = [console.receive_json()["type"] for _ in range(3)]
        assert kinds == ["console.moment", "console.auction", "console.latency"]


def test_a_held_swap_previews_the_next_ad_and_the_auction_lists_its_baskets(
    tmp_path: Path,
) -> None:
    clock = FixedClock(T0)
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path, auction_swap_holds=True),
        clock=clock,
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console?live=1") as station,
        client.websocket_connect("/ws/agent") as agent,
    ):
        agent.send_text(
            snapshot_for("fitness", 1, clock.advance(timedelta(seconds=0.1))).model_dump_json()
        )
        agent.send_text(
            snapshot_for("co-brand", 2, clock.advance(timedelta(seconds=0.5))).model_dump_json()
        )
        auction = None
        while True:
            message = CONSOLE_OUTBOUND.validate_python(station.receive_json())
            if isinstance(message, ConsoleAuction) and message.swap == "held":
                auction = message
            if isinstance(message, ConsoleNext):
                break
        assert auction is not None
        assert {s.brand_name for s in message.spec.slots} == {"Voltio", "Café Patio"}
        assert message.auction_id == auction.auction_id
        assert message.spec.redemption_code is None  # made only when it airs
        assert message.not_before == auction.trace.swap_not_before
        assert set(auction.alternatives[0].brands) == {w.brand_name for w in auction.winners}
        assert all(set(a.brands) != {"Voltio", "Rayo"} for a in auction.alternatives)
