"""`/ws/dev` end to end: the QA console sees the loop, the demo console stays clean."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import SOCKET_UNIONS, DevCreative, DevPerception, PlayerDisplayed
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

T0 = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
DEV_OUTBOUND_TYPES = {m.model_fields["type"].default for m in SOCKET_UNIONS["DevOutbound"]}


def _until(ws: Any, kind: str, limit: int = 30) -> dict[str, Any]:
    for _ in range(limit):
        message: dict[str, Any] = ws.receive_json()
        assert message["type"] in DEV_OUTBOUND_TYPES
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} within {limit} messages")


def test_dev_socket_is_closed_when_the_dev_console_is_off(tmp_path) -> None:
    settings = AppSettings(app_env="test", dev_console=False, media_dir=tmp_path)
    container = build_container(settings, clock=FixedClock(T0))
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/dev") as dev,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        dev.receive_text()
    assert closed.value.code == 4404


def test_dev_sees_perception_decision_build_and_display(tmp_path) -> None:
    clock = FixedClock(T0)
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/dev") as dev,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()  # the house ad
        agent.send_text(
            DevPerception(
                screen_id="demo-01",
                seq=1,
                measured_at=clock.now_utc(),
                frame_w=1280,
                frame_h=720,
                detector="local",
                inference_ms=12.5,
                raw_people=1,
            ).model_dump_json()
        )
        perception = _until(dev, "dev.perception")
        assert (perception["frame_w"], perception["detector"]) == (1280, "local")

        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        moment = _until(dev, "console.moment")
        _until(dev, "console.auction")
        built = DevCreative.model_validate(_until(dev, "dev.creative"))
        assert built.step == "composed"
        assert built.moment_id == moment["moment_id"]
        assert built.brief is not None and built.brief.brands
        assert built.copy_draft is not None and built.copy_draft.passed

        ready = player.receive_json()
        assert ready["creative_id"] == built.creative_id
        player.send_text(
            PlayerDisplayed(
                screen_id="demo-01",
                creative_id=built.creative_id or "",
                seq=1,
                displayed_at=clock.now_utc(),
            ).model_dump_json()
        )
        shown = _until(dev, "dev.player")
        assert (shown["event"], shown["creative_id"]) == ("displayed", built.creative_id)


def test_station_console_gets_camera_boxes_without_dev_or_key(tmp_path) -> None:
    """The console's camera panel works where `/dev` is off (prod): boxes only, no key."""
    clock = FixedClock(T0)
    settings = AppSettings(app_env="test", dev_console=False, media_dir=tmp_path)
    container = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console?live=1") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        agent.send_text(
            DevPerception(
                screen_id="demo-01",
                seq=1,
                measured_at=clock.now_utc(),
                frame_w=1280,
                frame_h=720,
                detector="local",
                inference_ms=9.0,
                raw_people=1,
            ).model_dump_json()
        )
        perception = _until(console, "dev.perception")
        assert perception["detector"] == "local"
