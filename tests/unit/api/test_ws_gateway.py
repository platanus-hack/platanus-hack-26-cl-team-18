"""End-to-end over the real ASGI app: socket -> validate -> stamp -> gate -> router -> hub."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import Container, build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    CreativeReady,
    CreativeSlot,
    CreativeSpec,
    LatencyTrace,
    PlaybackReport,
    RecognitionSnapshot,
)
from app.main import create_app
from app.modules.audience.infrastructure.recognition_vlm import (
    RecognitionVlm,
    VisualDetail,
    VisualDetails,
)
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.realtime.router import Received

T0 = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


class Recorder:
    def __init__(self) -> None:
        self.items: list[tuple[object, Received]] = []
        self._cv = threading.Condition()

    def __call__(self, msg: object, received: Received) -> None:
        with self._cv:
            self.items.append((msg, received))
            self._cv.notify_all()

    def wait_for(self, n: int, timeout: float = 2.0) -> list[tuple[object, Received]]:
        with self._cv:
            assert self._cv.wait_for(lambda: len(self.items) >= n, timeout), self.items
            return list(self.items)


@pytest.fixture
def container() -> Container:
    return build_container(
        AppSettings(app_env="test", live_ads_enabled=False),
        clock=FixedClock(T0 + timedelta(milliseconds=3)),
    )


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container=container)) as c:
        yield c


def snapshot_json(seq: int, **kw: object) -> str:
    data: dict[str, object] = {
        "type": "audience.snapshot",
        "v": 1,
        "screen_id": "mall-01",
        "seq": seq,
        "measured_at": T0.isoformat(),
        "people": 1,
        "looking": 1,
        "minor_likely": {"value": False, "confidence": 0.9},
    }
    data.update(kw)
    return json.dumps(data)


def test_health_reports_contract_version_and_connections(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["contract_version"] == 1
    assert body["screens_connected"] == []


def test_agent_snapshot_is_routed_with_the_server_received_at(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()
    container.router.on(AudienceSnapshot, rec)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(snapshot_json(1))
        ((msg, received),) = rec.wait_for(1)
    assert isinstance(msg, AudienceSnapshot) and msg.seq == 1
    assert msg.measured_at == T0  # device clock
    assert received.received_at == T0 + timedelta(milliseconds=3)  # server clock


def test_recognition_snapshot_reaches_router_and_ephemeral_read_model(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()
    container.router.on(RecognitionSnapshot, rec)
    message = {
        "type": "recognition.snapshot",
        "screen_id": "mall-01",
        "session_id": "session-1",
        "session_started_at": T0.isoformat(),
        "seq": 1,
        "measured_at": T0.isoformat(),
        "video_time_s": 0.5,
        "people": [
            {
                "track_id": "1",
                "first_seen_s": 0.5,
                "last_seen_s": 0.5,
                "box": [1, 2, 3, 4],
                "confidence": 0.8,
                "direction": "unknown",
                "currently_visible": True,
                "attributes": [],
            }
        ],
        "history": [],
        "objects": [],
        "audio_events": [],
        "latency": {
            "decode_ms": 1,
            "detection_tracking_ms": 10,
            "publication_delay_ms": 11,
            "first_observation_ms": 11,
        },
    }
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json(message)
        rec.wait_for(1)
        response = client.get("/recognition/mall-01")
    assert response.status_code == 200
    assert response.json()["snapshot"]["people"][0]["track_id"] == "1"
    assert response.json()["received_at"] == "2026-11-21T15:00:00.003000Z"


def test_redacted_keyframe_enriches_snapshot_via_websocket(
    client: TestClient, container: Container
) -> None:
    class FakeAI:
        async def structured_output(self, *args: object, **kwargs: object) -> VisualDetails:
            return VisualDetails(
                details=[
                    VisualDetail(
                        kind="object",
                        value="black hoodie",
                        evidence="visible fabric",
                        associated_item="hoodie",
                        uncertainty="medium",
                    )
                ]
            )

    rec = Recorder()
    container.router.on(RecognitionSnapshot, rec)
    vlm = RecognitionVlm(FakeAI(), "fake", container.warm, container.recognition, container.router)  # type: ignore[arg-type]
    container.router.on(AudienceKeyframe, vlm.accept)
    frame = Image.new("RGB", (120, 200), "white")
    frame.paste("black", (0, 0, 120, 100))
    buffer = BytesIO()
    frame.save(buffer, format="JPEG")
    jpeg = buffer.getvalue()
    snapshot = {
        "type": "recognition.snapshot",
        "screen_id": "mall-01",
        "session_id": "session-1",
        "session_started_at": T0.isoformat(),
        "seq": 1,
        "measured_at": T0.isoformat(),
        "video_time_s": 0.5,
        "people": [
            {
                "track_id": "1",
                "first_seen_s": 0.5,
                "last_seen_s": 0.5,
                "box": [1, 2, 3, 4],
                "confidence": 0.8,
                "direction": "unknown",
                "currently_visible": True,
            }
        ],
        "latency": {"decode_ms": 1, "detection_tracking_ms": 10, "publication_delay_ms": 11},
    }
    header = {
        "type": "audience.keyframe",
        "screen_id": "mall-01",
        "session_id": "session-1",
        "track_id": "1",
        "video_time_s": 0.5,
        "seq": 1,
        "measured_at": T0.isoformat(),
        "byte_length": len(jpeg),
    }
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json(snapshot)
        rec.wait_for(1)
        ws.send_json(header)
        ws.send_bytes(jpeg)
        items = rec.wait_for(2)
        ws.send_json({**snapshot, "seq": 2})
        items = rec.wait_for(3)
    enriched = items[-1][0]
    assert isinstance(enriched, RecognitionSnapshot)
    assert enriched.revision == 1
    assert enriched.people[0].attributes[0].value == "black hoodie"


def test_agent_duplicate_and_stale_seq_are_dropped(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()
    container.router.on(AudienceSnapshot, rec)
    container.router.on(AudienceUtterance, rec)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(snapshot_json(5))
        rec.wait_for(1)
        ws.send_text(snapshot_json(5))
        ws.send_text(snapshot_json(4))
        # A distinct stream proves the socket processed the frames before it.
        ws.send_text(
            json.dumps(
                {
                    "type": "audience.utterance",
                    "v": 1,
                    "screen_id": "mall-01",
                    "seq": 1,
                    "measured_at": T0.isoformat(),
                    "text": "qué hambre",
                    "language": "es",
                    "keyterms_hit": ["hambre"],
                }
            )
        )
        items = rec.wait_for(2)
    assert [type(m).__name__ for m, _ in items] == ["AudienceSnapshot", "AudienceUtterance"]


def test_invalid_frame_answers_system_error_and_keeps_the_socket(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()
    container.router.on(AudienceSnapshot, rec)
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(snapshot_json(1, looking=9))
        error = ws.receive_json()
        assert error["type"] == "system.error"
        assert error["code"] == "message.invalid"
        assert error["rejected_type"] == "audience.snapshot"
        ws.send_text(snapshot_json(2))
        rec.wait_for(1)


def test_keyframe_header_then_bytes_reaches_handlers_in_memory(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()
    container.router.on(AudienceKeyframe, rec)
    jpeg = b"\xff\xd8blurred\xff\xd9"
    header = {
        "type": "audience.keyframe",
        "v": 1,
        "screen_id": "mall-01",
        "seq": 1,
        "measured_at": T0.isoformat(),
        "content_type": "image/jpeg",
        "byte_length": len(jpeg),
    }
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps(header))
        ws.send_bytes(jpeg)
        ((msg, received),) = rec.wait_for(1)
    assert isinstance(msg, AudienceKeyframe)
    assert received.payload == jpeg


def test_stray_bytes_without_a_header_are_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_bytes(b"\xff\xd8")
        assert ws.receive_json()["code"] == "keyframe.unexpected_bytes"


def creative(screen_id: str) -> CreativeReady:
    return CreativeReady(
        screen_id=screen_id,
        creative_id="cr-1",
        spec=CreativeSpec(
            layout_id="solo",
            slots=[
                CreativeSlot(
                    brand_id="b1",
                    brand_name="Voltio",
                    logo_url="/kits/voltio.svg",
                    copy_text="Dale.",
                )
            ],
            duration_ms=8000,
        ),
        trace=LatencyTrace(measured_at=T0),
    )


def test_player_receives_creatives_pushed_from_a_worker_thread(
    client: TestClient, container: Container
) -> None:
    connected = threading.Event()
    container.router.on_player_connected(lambda screen_id, sink: connected.set())
    with client.websocket_connect("/ws/screen/mall-01") as ws:
        assert connected.wait(2)
        threading.Thread(
            target=container.hub.send_to_screen_threadsafe, args=(creative("mall-01"),)
        ).start()
        msg = ws.receive_json()
    assert msg["type"] == "creative.ready"
    assert msg["spec"]["slots"][0]["brand_name"] == "Voltio"


def test_player_playback_report_is_routed_and_screen_id_is_enforced(
    client: TestClient, container: Container
) -> None:
    rec = Recorder()
    container.router.on(PlaybackReport, rec)
    report = {
        "type": "playback.report",
        "v": 1,
        "creative_id": "cr-1",
        "seq": 1,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(seconds=8)).isoformat(),
    }
    with client.websocket_connect("/ws/screen/mall-01") as ws:
        ws.send_text(json.dumps({**report, "screen_id": "other"}))
        assert ws.receive_json()["code"] == "message.screen_mismatch"
        ws.send_text(json.dumps({**report, "screen_id": "mall-01"}))
        ((msg, _),) = rec.wait_for(1)
    assert isinstance(msg, PlaybackReport)


def test_invalid_screen_id_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/screen/bad id!") as ws:
            ws.receive_text()


def test_console_gets_broadcasts(client: TestClient, container: Container) -> None:
    with client.websocket_connect("/ws/console") as ws:
        deadline = datetime.now() + timedelta(seconds=2)
        while container.hub.console_count() == 0 and datetime.now() < deadline:
            pass
        container.hub.broadcast_console_threadsafe(creative("mall-01"))
        assert ws.receive_json()["type"] == "creative.ready"


def test_dev_push_reaches_the_player_and_console(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws/screen/mall-01") as player,
        client.websocket_connect("/ws/console") as console,
    ):
        resp = client.post(
            "/dev/push",
            content=creative("mall-01").model_dump_json(),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 202
        assert player.receive_json()["creative_id"] == "cr-1"
        assert console.receive_json()["type"] == "creative.ready"


def test_dev_push_does_not_exist_in_prod() -> None:
    prod = create_app(container=build_container(AppSettings(app_env="prod")))
    with TestClient(prod) as c:
        assert c.post("/dev/push", json={}).status_code == 404
