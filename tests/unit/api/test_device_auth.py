"""Only token holders feed the live loop; anyone may watch a screen (device_auth.py)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import Container, build_container
from app.config.settings import AppSettings
from app.contract.messages import CreativeReady, PlaybackReport
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

AGENT = "agent-0123456789abcdef0123456789"
SCREEN = "screen-0123456789abcdef012345678"
PLACEHOLDER = "set-in-render-dashboard"  # what Render holds until someone sets the secret
T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def app(tmp_path: Path, **settings: object) -> tuple[TestClient, Container]:
    values = {
        "_env_file": None,
        "app_env": "prod",
        "media_dir": tmp_path,
        "voiceovers_enabled": False,
        "auction_swap_holds": False,
        **settings,
    }
    container = build_container(AppSettings(**values), clock=FixedClock(T0))  # type: ignore[arg-type]
    return TestClient(create_app(container=container)), container


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("configured", "presented"),
    [
        (None, None),  # prod with no token set: closed, not open
        (None, AGENT),
        (PLACEHOLDER, PLACEHOLDER),  # the dashboard placeholder never unlocks prod
        (AGENT, None),
        (AGENT, "wrong-0123456789abcdef0123456789"),
    ],
)
def test_prod_refuses_an_agent_without_the_right_strong_token(
    tmp_path: Path, configured: str | None, presented: str | None
) -> None:
    client, _ = app(tmp_path, agent_token=configured)
    headers = bearer(presented) if presented else {}
    with client, pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/agent", headers=headers):
            pass
    assert refused.value.code == 1008


def test_a_configured_token_is_enforced_locally_too(tmp_path: Path) -> None:
    client, _ = app(tmp_path, app_env="local", agent_token=AGENT)
    with client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/agent"):
                pass
        with client.websocket_connect("/ws/agent", headers=bearer(AGENT)):
            pass


def watch_one_play(client: TestClient, container: Container, **player: object) -> str | None:
    """Agent sees a group, the player gets the ad and reports 4 s of it."""
    with (
        client.websocket_connect("/ws/screen/demo-01", **player) as screen,  # type: ignore[arg-type]
        client.websocket_connect("/ws/agent", headers=bearer(AGENT)) as agent,
    ):
        screen.receive_json()  # house ad
        agent.send_text(snapshot_for("fitness", 1, T0).model_dump_json())
        creative = CreativeReady.model_validate(screen.receive_json())
        assert creative.auction_id is not None  # watching always gets the ad
        container.clock.advance(timedelta(seconds=4))  # type: ignore[attr-defined]
        screen.send_text(
            PlaybackReport(
                screen_id="demo-01",
                creative_id=creative.creative_id,
                seq=1,
                started_at=T0,
                ended_at=T0 + timedelta(seconds=4),
            ).model_dump_json()
        )
        deadline = time.monotonic() + 3
        while not container.ledger.charges() and time.monotonic() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.02)
        return screen.accepted_subprotocol  # type: ignore[no-any-return]


def test_a_player_without_the_screen_token_watches_but_never_bills(tmp_path: Path) -> None:
    client, container = app(tmp_path, agent_token=AGENT, screen_token=SCREEN)
    with client:
        with (
            client.websocket_connect("/ws/screen/demo-01") as screen,
            client.websocket_connect("/ws/agent", headers=bearer(AGENT)) as agent,
        ):
            screen.receive_json()
            agent.send_text(snapshot_for("fitness", 1, T0).model_dump_json())
            creative = CreativeReady.model_validate(screen.receive_json())
            assert creative.auction_id is not None
            report = PlaybackReport(
                screen_id="demo-01",
                creative_id=creative.creative_id,
                seq=1,
                started_at=T0,
                ended_at=T0 + timedelta(seconds=4),
            )
            screen.send_text(report.model_dump_json())
            error = screen.receive_json()
            assert error["code"] == "screen.unauthorized"
            assert error["rejected_type"] == "playback.report"
    assert container.ledger.charges() == ()  # type: ignore[attr-defined]


def test_a_player_with_the_screen_token_bills_and_the_token_is_never_echoed(
    tmp_path: Path,
) -> None:
    client, container = app(tmp_path, agent_token=AGENT, screen_token=SCREEN)
    with client:
        accepted = watch_one_play(
            client, container, subprotocols=["team18", f"team18.token.{SCREEN}"]
        )
    assert accepted == "team18"
    assert container.ledger.charges()  # type: ignore[attr-defined]


def test_prod_with_no_screen_token_lets_nobody_bill(tmp_path: Path) -> None:
    client, container = app(tmp_path, agent_token=AGENT)
    with client:
        watch_one_play(client, container, subprotocols=["team18", "team18.token.guess"])
    assert container.ledger.charges() == ()  # type: ignore[attr-defined]
