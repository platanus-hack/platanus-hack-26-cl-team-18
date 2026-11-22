"""`/ws/console` auth is opt-in (CONSOLE_TOKEN), and every socket caps its frame size."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import PlaybackReport
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

from .test_ws_gateway import T0, Recorder

TOKEN = "console-0123456789abcdef01234567"


def client_for(tmp_path: Path, **settings: object) -> TestClient:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "live_ads_enabled": False,
        "media_dir": tmp_path,
        **settings,
    }
    container = build_container(AppSettings(**values), clock=FixedClock(T0))  # type: ignore[arg-type]
    return TestClient(create_app(container=container))


def test_without_a_console_token_the_console_stays_open(tmp_path: Path) -> None:
    with client_for(tmp_path) as client, client.websocket_connect("/ws/console") as ws:
        ws.send_text("{}")
        assert ws.receive_json()["code"] == "console.read_only"


@pytest.mark.parametrize(
    "offered",
    [None, ["team18"], ["team18", "team18.token.wrong-token"], ["team18", "team18.token."]],
)
def test_with_a_console_token_a_console_without_it_is_refused(
    tmp_path: Path, offered: list[str] | None
) -> None:
    with client_for(tmp_path, console_token=TOKEN) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws/console", subprotocols=offered):
                pass
    assert refused.value.code == 1008


def test_with_a_console_token_the_right_subprotocol_gets_the_stream(tmp_path: Path) -> None:
    offered = ["team18", f"team18.token.{TOKEN}"]
    with client_for(tmp_path, console_token=TOKEN) as client:
        with client.websocket_connect("/ws/console?live=1", subprotocols=offered) as ws:
            assert ws.accepted_subprotocol == "team18"  # never the token
            ws.send_text("{}")
            assert ws.receive_json()["code"] == "console.read_only"


def test_an_oversized_player_frame_is_refused_before_parsing(tmp_path: Path) -> None:
    client = client_for(tmp_path, ws_max_frame_bytes=4096)
    container = client.app.state.container  # type: ignore[attr-defined]
    rec = Recorder()
    container.router.on(PlaybackReport, rec)
    report = {
        "type": "playback.report",
        "screen_id": "mall-01",
        "creative_id": "cr-1",
        "seq": 2,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(seconds=8)).isoformat(),
    }
    with client, client.websocket_connect("/ws/screen/mall-01") as ws:
        ws.send_text(json.dumps({**report, "pad": "x" * 10_000}))
        assert ws.receive_json()["code"] == "frame.too_large"
        ws.send_text(json.dumps(report))
        ((msg, _),) = rec.wait_for(1)
    assert isinstance(msg, PlaybackReport) and msg.seq == 2
