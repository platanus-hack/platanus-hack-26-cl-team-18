"""The real startup serves independent screens without a simulator flag."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceSnapshot, CreativeReady
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock


@pytest.mark.parametrize("env", ["local", "prod"])
def test_live_startup_registers_the_loop_without_simulator(env, tmp_path):
    settings = AppSettings(
        _env_file=None,
        app_env=env,
        demo_simulator=False,
        media_dir=tmp_path,
        voiceovers_enabled=False,
        auction_swap_holds=False,
    )
    container = build_container(settings)
    assert container.router.has_handlers(AudienceSnapshot)
    with TestClient(create_app(container=container)) as client:
        with client.websocket_connect("/ws/screen/venue-42") as player:
            assert not paid_brands(CreativeReady.model_validate(player.receive_json()))
            with client.websocket_connect("/ws/agent") as agent:
                reading = snapshot_for("co-brand", 1, datetime.now(UTC)).model_copy(
                    update={"screen_id": "venue-42"}
                )
                agent.send_text(reading.model_dump_json())
                creative = CreativeReady.model_validate(player.receive_json())
                assert set(paid_brands(creative)) == {"Voltio", "Café Patio"}
                assert creative.screen_id == "venue-42"


def test_screens_keep_audience_stock_and_playback_separate_but_share_campaigns(tmp_path):
    clock = FixedClock(datetime(2026, 11, 21, 15, tzinfo=UTC))
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path), clock=clock
    )
    with TestClient(create_app(container=container)) as client:
        with (
            client.websocket_connect("/ws/screen/north") as north,
            client.websocket_connect("/ws/screen/south") as south,
            client.websocket_connect("/ws/agent") as agent,
        ):
            north.receive_json(), south.receive_json()
            for seq, screen, scene, player in [
                (1, "north", "fitness", north),
                (1, "south", "co-brand", south),
            ]:
                at = clock.advance(timedelta(seconds=1))
                reading = snapshot_for(scene, seq, at).model_copy(update={"screen_id": screen})
                agent.send_text(reading.model_dump_json())
                creative = CreativeReady.model_validate(player.receive_json())
                assert creative.screen_id == screen
            book = client.get("/api/book").json()
            voltio = next(b for b in book["lines"] if b["brand_name"] == "Voltio")
            # One global budget reserves both screens' exposure.
            assert int(voltio["reserved"]["amount"]) > 98
            patio = next(b for b in book["lines"] if b["brand_name"] == "Café Patio")
            response = client.post(
                "/api/clerk/stock",
                json={
                    "type": "stock.set",
                    "v": 1,
                    "screen_id": "south",
                    "brand_id": patio["brand_id"],
                    "in_stock": False,
                },
            )
            assert response.status_code == 200
            assert set(paid_brands(CreativeReady.model_validate(south.receive_json()))) == {
                "Voltio"
            }
            south_book = client.get("/api/book?screen_id=south").json()
            north_book = client.get("/api/book?screen_id=north").json()
            assert not next(b for b in south_book["lines"] if b["brand_name"] == "Café Patio")[
                "in_stock"
            ]
            assert next(b for b in north_book["lines"] if b["brand_name"] == "Café Patio")[
                "in_stock"
            ]
            # Reconnect replays each screen's own last creative.
            with client.websocket_connect("/ws/screen/north") as reconnected:
                assert paid_brands(CreativeReady.model_validate(reconnected.receive_json())) == (
                    "Voltio",
                )


def test_missing_providers_are_visible_and_stock_ads_still_work(tmp_path):
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        media_dir=tmp_path,
        anthropic_api_key=None,
        elevenlabs_api_key=None,
        elevenlabs_narrator_voice_id=None,
        fal_key=None,
        fal_video_model=None,
    )
    with TestClient(create_app(settings)) as client:
        status = client.get("/health").json()["ads"]
    assert status["enabled"] is True
    assert status["providers"]["ai_copy"] == {
        "enabled": True,
        "configured": False,
        "missing": ["ANTHROPIC_API_KEY"],
    }
    assert status["providers"]["voice"]["missing"] == [
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_NARRATOR_VOICE_ID",
    ]
    assert "cafe-patio+voltio" in status["media"]["missing_loops"]
    assert status["fallbacks"]["copy"] == "approved_stock"


def test_prod_runs_ads_without_exposing_operator_mutations(tmp_path):
    settings = AppSettings(
        _env_file=None,
        app_env="prod",
        media_dir=tmp_path,
        voiceovers_enabled=False,
        audience_ai=False,
        creative_ai_copy=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["ads"]["enabled"]
        assert (
            client.post(
                "/api/clerk/stock",
                json={
                    "type": "stock.set",
                    "v": 1,
                    "screen_id": "north",
                    "brand_id": "unknown",
                    "in_stock": False,
                },
            ).status_code
            == 404
        )


def test_shared_bid_updates_reauction_every_screen_and_codes_redeem(tmp_path):
    clock = FixedClock(datetime(2026, 11, 21, 15, tzinfo=UTC))
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path), clock=clock
    )
    with TestClient(create_app(container=container)) as client:
        with (
            client.websocket_connect("/ws/screen/north") as north,
            client.websocket_connect("/ws/screen/south") as south,
            client.websocket_connect("/ws/agent") as agent,
        ):
            north.receive_json(), south.receive_json()
            for screen, player in [("north", north), ("south", south)]:
                agent.send_text(
                    snapshot_for("co-brand", 1, clock.now_utc())
                    .model_copy(update={"screen_id": screen})
                    .model_dump_json()
                )
                creative = CreativeReady.model_validate(player.receive_json())
                assert "Voltio" in paid_brands(creative)
                code = creative.spec.redemption_code
                assert client.get(f"/r/{code}").status_code == 200
                result = client.post(f"/r/{code}/redeem", json={"nonce": "same-browser-nonce"})
                assert set(result.json()["brands"]) == {"Voltio", "Café Patio"}
            book = client.get("/api/book").json()
            rayo = next(b for b in book["lines"] if b["brand_name"] == "Rayo")
            response = client.post(
                "/api/advertiser/bid-multiplier",
                json={
                    "type": "advertiser.bid_multiplier",
                    "v": 1,
                    "campaign_id": rayo["campaign_id"],
                    "multiplier": "3",
                    "seq": 1,
                },
            )
            assert response.status_code == 200
            for player in (north, south):
                brands = set(paid_brands(CreativeReady.model_validate(player.receive_json())))
                assert "Rayo" in brands and "Voltio" not in brands
