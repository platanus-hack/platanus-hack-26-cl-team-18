"""A healthy server is not proof that the visible player receives the live inputs."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceUtterance
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock


def test_health_distinguishes_live_inputs_from_an_unconnected_player(tmp_path):
    clock = FixedClock(datetime(2026, 11, 21, tzinfo=UTC))
    container = build_container(
        AppSettings(
            _env_file=None,
            app_env="test",
            media_dir=tmp_path,
            live_ads_enabled=False,
            speech_mode_screens=["demo-01"],
        ),
        clock=clock,
    )
    with TestClient(create_app(container=container)) as client:
        assert client.get("/health").json()["inputs"] == {}
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
            agent.send_text(
                AudienceUtterance(
                    screen_id="demo-01",
                    seq=1,
                    measured_at=clock.now_utc(),
                    text="private synthetic test phrase",
                    language="es",
                    keyterms_hit=["café"],
                ).model_dump_json()
            )
            agent.send_text("{}")
            assert agent.receive_json()["type"] == "system.error"
            body = client.get("/health").json()
            status = body["inputs"]["demo-01"]
            assert status["camera_recent"] is True
            assert status["speech_enabled"] is True
            assert status["speech_recent"] is True
            assert status["last_utterance_at"] == clock.now_utc().isoformat()
            assert status["player_connected"] is False
            assert "private synthetic" not in str(body)
            assert "café" not in str(body)
            with client.websocket_connect("/ws/screen/demo-01"):
                assert client.get("/health").json()["inputs"]["demo-01"]["player_connected"]
            clock.advance(timedelta(seconds=6))
            assert not client.get("/health").json()["inputs"]["demo-01"]["camera_recent"]
            clock.advance(timedelta(seconds=60))
            assert not client.get("/health").json()["inputs"]["demo-01"]["speech_recent"]


def test_rejected_frames_do_not_mark_a_camera_as_live(tmp_path):
    container = build_container(AppSettings(_env_file=None, app_env="test", media_dir=tmp_path))
    with TestClient(create_app(container=container)) as client:
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_text('{"type":"audience.snapshot","screen_id":"demo-01"}')
            assert agent.receive_json()["type"] == "system.error"
        assert client.get("/health").json()["inputs"] == {}
