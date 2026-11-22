"""Hostile frames on every socket: each gets `system.error`, and the socket keeps working."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import Container, build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceSnapshot, PlaybackReport
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

from .test_ws_gateway import T0, Recorder, snapshot_json


@pytest.fixture
def container() -> Container:
    return build_container(
        AppSettings(app_env="test", live_ads_enabled=False, ws_max_frame_bytes=4096),
        clock=FixedClock(T0 + timedelta(milliseconds=3)),
    )


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container=container)) as c:
        yield c


HOSTILE_AGENT_FRAMES = [
    "not json",
    "",
    "null",
    "[]",
    '{"type": "audience.nope", "screen_id": "mall-01", "seq": 1}',
    '{"type": "playback.report"}',  # a player message on the agent socket
    snapshot_json(-1),
    snapshot_json(1.5),  # type: ignore[arg-type]
    snapshot_json(1, people=-3),
    snapshot_json(1, people="many"),
    snapshot_json(1, measured_at="yesterday"),
    snapshot_json(1, measured_at="2026-11-21T15:00:00"),  # naive: refused
    snapshot_json(1, screen_id="../../etc"),
    snapshot_json(1, minor_likely={"value": False, "confidence": float("nan")}),
    snapshot_json(1, extra_field=True),
    "[" * 50_000 + "]" * 50_000,  # deeply nested, over the frame limit
    "[" * 1_500 + "]" * 1_500,  # deeply nested, under it
    json.dumps({"type": "audience.snapshot", "pad": "x" * 10_000}),  # oversized
]


@pytest.mark.parametrize("frame", HOSTILE_AGENT_FRAMES, ids=range(len(HOSTILE_AGENT_FRAMES)))
def test_a_hostile_agent_frame_is_answered_and_the_next_snapshot_still_routes(
    client: TestClient, container: Container, frame: str
) -> None:
    rec = Recorder()
    container.router.on(AudienceSnapshot, rec)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(frame)
        error = ws.receive_json()
        assert error["type"] == "system.error"
        ws.send_text(snapshot_json(2))
        ((msg, _),) = rec.wait_for(1)
    assert isinstance(msg, AudienceSnapshot) and msg.seq == 2


def test_a_keyframe_header_left_without_bytes_cannot_pair_with_later_bytes(
    client: TestClient,
) -> None:
    header = {
        "type": "audience.keyframe",
        "screen_id": "mall-01",
        "seq": 1,
        "measured_at": T0.isoformat(),
        "content_type": "image/jpeg",
        "byte_length": 4,
    }
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps(header))
        ws.send_bytes(b"\xff\xd8\xff")  # wrong length: refused, header dropped
        assert ws.receive_json()["code"] == "keyframe.unexpected_bytes"
        ws.send_bytes(b"\xff\xd8\xff\xd9")  # right length, but its header is gone
        assert ws.receive_json()["code"] == "keyframe.unexpected_bytes"


def report_json(seq: int, **kw: object) -> str:
    data: dict[str, object] = {
        "type": "playback.report",
        "screen_id": "mall-01",
        "creative_id": "cr-1",
        "seq": seq,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(seconds=8)).isoformat(),
    }
    data.update(kw)
    return json.dumps(data)


HOSTILE_PLAYER_FRAMES = [
    "garbage",
    "{}",
    snapshot_json(1),  # an agent message on the player socket
    report_json(1, ended_at=(T0 - timedelta(seconds=1)).isoformat()),  # ends before it starts
    report_json(1, creative_id=""),
    report_json(-1),
    report_json(1, screen_id="other"),
]


@pytest.mark.parametrize("frame", HOSTILE_PLAYER_FRAMES, ids=range(len(HOSTILE_PLAYER_FRAMES)))
def test_a_hostile_player_frame_is_answered_and_the_next_report_still_routes(
    client: TestClient, container: Container, frame: str
) -> None:
    rec = Recorder()
    container.router.on(PlaybackReport, rec)
    with client.websocket_connect("/ws/screen/mall-01") as ws:
        ws.send_text(frame)
        assert ws.receive_json()["type"] == "system.error"
        ws.send_text(report_json(2))
        ((msg, _),) = rec.wait_for(1)
    assert isinstance(msg, PlaybackReport) and msg.seq == 2


def test_a_binary_player_frame_closes_the_socket(client: TestClient) -> None:
    # Players send JSON text only; a binary frame is not a player, so nothing buffers more.
    with client.websocket_connect("/ws/screen/mall-01") as ws:
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["code"] == "frame.binary_unsupported"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1003


@pytest.mark.parametrize("path", ["/ws/console"])
def test_a_binary_frame_closes_a_push_only_socket(client: TestClient, path: str) -> None:
    with client.websocket_connect(path) as ws:
        ws.send_bytes(b"\x00\x01")
        while (message := ws.receive_json())["type"] != "system.error":
            pass  # catch-up messages first
        assert message["code"] == "frame.binary_unsupported"
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_json()
    assert closed.value.code == 1003


def test_a_handler_that_raises_does_not_close_the_agent_socket(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()

    def explode(_msg: object, _received: object) -> None:
        raise RuntimeError("handler bug")

    container.router.on(AudienceSnapshot, explode)
    container.router.on(AudienceSnapshot, rec)  # a sibling still runs
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(snapshot_json(1))
        rec.wait_for(1)
        ws.send_text(snapshot_json(2))
        items = rec.wait_for(2)
    assert [m.seq for m, _ in items] == [1, 2]  # type: ignore[attr-defined]


def test_the_frame_cap_counts_utf8_bytes_not_characters(client: TestClient) -> None:
    frame = json.dumps({"type": "playback.report", "pad": "é" * 2_100}, ensure_ascii=False)
    assert len(frame) < 4096 < len(frame.encode())
    with client.websocket_connect("/ws/screen/mall-01") as ws:
        ws.send_text(frame)
        assert ws.receive_json()["code"] == "frame.too_large"
