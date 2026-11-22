"""Screen runtimes are bounded: a stranger can't mint unlimited screens (live.py, ws.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import Container, build_container
from app.config.settings import AppSettings
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
SCREEN = "screen-0123456789abcdef012345678"


def app(tmp_path: Path, **settings: object) -> tuple[TestClient, Container]:
    values = {
        "_env_file": None,
        "app_env": "local",
        "media_dir": tmp_path,
        "voiceovers_enabled": False,
        "auction_swap_holds": False,
        "creative_ai_copy": False,
        "audience_ai": False,
        "max_screen_runtimes": 2,
        **settings,
    }
    container = build_container(AppSettings(**values), clock=FixedClock(T0))  # type: ignore[arg-type]
    return TestClient(create_app(container=container)), container


def refused_with(client: TestClient, path: str, **kwargs: object) -> int:
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(path, **kwargs) as ws:  # type: ignore[arg-type]
            while True:
                ws.receive_json()
    return refused.value.code


def watches(client: TestClient, path: str, **kwargs: object) -> bool:
    with client.websocket_connect(path, **kwargs) as ws:  # type: ignore[arg-type]
        return ws.receive_json()["type"] in {"creative.ready", "live.status"}


def test_past_the_cap_a_new_screen_is_refused_and_running_screens_keep_working(
    tmp_path: Path,
) -> None:
    client, container = app(tmp_path)
    with client:
        assert watches(client, "/ws/screen/one")  # demo-01 + one: at the cap
        assert refused_with(client, "/ws/screen/two") == 1013  # try again later
        assert watches(client, "/ws/screen/demo-01")  # never evicted
        assert watches(client, "/ws/screen/one")
    assert container.admit_screen("two", False) == "unauthorized"  # was never created


def test_the_live_switch_never_creates_a_screen_past_the_cap(tmp_path: Path) -> None:
    client, container = app(tmp_path, max_screen_runtimes=1)
    with client:
        response = client.post("/api/live", json={"screen_id": "elsewhere", "live": False})
        assert response.status_code == 200
    assert container.admit_screen("elsewhere", False) == "unauthorized"


def test_an_invalid_screen_id_is_never_a_runtime(tmp_path: Path) -> None:
    _, container = app(tmp_path)
    assert container.admit_screen("a b", True) == "invalid"
    assert container.admit_screen("x" * 65, True) == "invalid"


def test_with_a_screen_token_only_its_holder_starts_a_new_screen(tmp_path: Path) -> None:
    client, _ = app(tmp_path, screen_token=SCREEN)
    token = {"subprotocols": ["team18", f"team18.token.{SCREEN}"]}
    with client:
        assert refused_with(client, "/ws/screen/lobby") == 1008  # a viewer can't mint one
        assert watches(client, "/ws/screen/demo-01")  # but watches a running screen
        assert watches(client, "/ws/screen/lobby", **token)  # the token holder starts it
        assert watches(client, "/ws/screen/lobby")  # then viewers may attach


def test_without_a_screen_token_any_player_starts_a_screen_under_the_cap(
    tmp_path: Path,
) -> None:
    client, _ = app(tmp_path)  # the demo station: no tokens at all
    with client:
        assert watches(client, "/ws/screen/rehearsal")


def test_viewers_are_registered_without_the_redemption_code_only_when_a_token_is_set(
    tmp_path: Path,
) -> None:
    client, container = app(tmp_path, screen_token=SCREEN)
    token = {"subprotocols": ["team18", f"team18.token.{SCREEN}"]}
    with client:
        with (
            client.websocket_connect("/ws/screen/demo-01") as viewer,
            client.websocket_connect("/ws/screen/demo-01", **token) as billing,  # type: ignore[arg-type]
        ):
            viewer.receive_json()
            billing.receive_json()
            assert len(container.hub._redacted) == 1
    open_client, open_container = app(tmp_path / "open")
    with open_client, open_client.websocket_connect("/ws/screen/demo-01") as ws:
        ws.receive_json()
        assert not open_container.hub._redacted
