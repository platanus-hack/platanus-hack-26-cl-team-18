"""Synthetic corrections through the actual agent -> auction -> player sockets."""

from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceSnapshot, AudienceUtterance, CreativeReady, PlayerVoice
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from tests.unit.api.test_speech_and_voice_flow import T0


def test_group_partial_retraction_does_not_leave_the_beer_ad_on_screen(tmp_path):
    container = build_container(
        AppSettings(
            _env_file=None, app_env="test", speech_mode_screens=["demo-01"], media_dir=tmp_path
        ),
        clock=FixedClock(T0),
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        agent.send_text(
            AudienceSnapshot(
                screen_id="demo-01",
                seq=1,
                measured_at=T0,
                people=2,
                looking=2,
                minor_likely={"value": False, "confidence": 1},
            ).model_dump_json()
        )
        for seq, text, request, final, expected in [
            (1, "quiero café", "first", True, ("Café Patio",)),
            (2, "quiero cerveza", "second", False, ("Malta Sur",)),
            (3, "no quiero cerveza, quiero café", "second", True, ("Café Patio",)),
        ]:
            agent.send_text(
                AudienceUtterance(
                    screen_id="demo-01",
                    seq=seq,
                    measured_at=T0,
                    text=text,
                    language="es",
                    utterance_id=request,
                    is_final=final,
                ).model_dump_json()
            )
            assert paid_brands(CreativeReady.model_validate(player.receive_json())) == expected


def test_actual_voice_events_are_accepted_on_the_player_socket(tmp_path):
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path), clock=FixedClock(T0)
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
    ):
        ad = CreativeReady.model_validate(player.receive_json())
        player.send_text(
            PlayerVoice(
                screen_id="demo-01",
                creative_id=ad.creative_id,
                seq=1,
                playing=True,
                duration_ms=1000,
            ).model_dump_json()
        )
        # A following invalid message gives an ordered barrier, so an unhandled
        # new contract cannot silently pass this socket test.
        player.send_text("{}")
        assert player.receive_json()["code"] == "message.invalid"
