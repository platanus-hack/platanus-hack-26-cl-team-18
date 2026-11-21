from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    ConsoleAuction,
    ConsoleLatency,
    ConsoleMoment,
    CreativeReady,
    PerceptionTiming,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock


def test_replay_reaches_player_and_falls_back_when_a_minor_is_likely(tmp_path) -> None:
    clock = FixedClock(datetime(2026, 11, 21, tzinfo=UTC))
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock)

    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        assert paid_brands(CreativeReady.model_validate(player.receive_json())) == ()

        for seq, (scene, expected) in enumerate(
            (
                ("co-brand", {"Voltio", "Café Patio"}),
                ("adult", {"Malta Sur"}),
                ("minor-safe", set()),
            ),
            start=1,
        ):
            now = clock.advance(timedelta(milliseconds=100))
            measured_at = now + timedelta(seconds=9) if scene == "adult" else now
            agent.send_text(snapshot_for(scene, seq, measured_at).model_dump_json())
            creative = CreativeReady.model_validate(player.receive_json())
            assert set(paid_brands(creative)) == expected
            assert creative.trace.measured_at == measured_at
            assert creative.trace.cleared_at == now
            assert creative.trace.ready_at == now

        with client.websocket_connect("/ws/screen/demo-01") as reconnect:
            assert CreativeReady.model_validate(reconnect.receive_json()) == creative

        player.send_text("{}")
        assert player.receive_json()["type"] == "system.error"


def test_live_loop_registers_for_prod_without_simulator() -> None:
    container = build_container(AppSettings(_env_file=None, app_env="prod", demo_simulator=False))
    assert container.router.has_handlers(AudienceSnapshot)


def test_console_explains_the_staged_basket_and_replays_latest_decision(tmp_path) -> None:
    clock = FixedClock(datetime(2026, 11, 21, tzinfo=UTC))
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock)

    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        timing = PerceptionTiming(detector="local", inference_ms=430, primary_attempt_ms=400)
        snapshot = snapshot_for("co-brand", 1, clock.now_utc()).model_copy(
            update={"perception": timing}
        )
        agent.send_text(snapshot.model_dump_json())
        creative = CreativeReady.model_validate(player.receive_json())
        moment = ConsoleMoment.model_validate(console.receive_json())
        auction = ConsoleAuction.model_validate(console.receive_json())
        latency = ConsoleLatency.model_validate(console.receive_json())
        assert all(
            message.trace.perception == timing for message in (creative, moment, auction, latency)
        )

        assert "$5.990" in creative.spec.slots[0].copy_text  # duo tier: 2 looking
        assert auction.auction_id == creative.auction_id
        assert latency.creative_id == creative.creative_id
        assert moment.moment_id == auction.moment_id
        assert {source for segment in moment.segments for source in segment.sources} == {
            "vision",
            "sound",
        }
        assert auction.coverage == 1
        # CLP per screen-second: the basket is worth more and earns more than one brand.
        assert auction.bundle_value.amount == "8.6"
        assert auction.single_brand_value.amount == "8"
        assert auction.bundle_revenue is not None and auction.bundle_revenue.amount == "6.95"
        assert auction.single_brand_revenue is not None
        assert auction.single_brand_revenue.amount == "6.5"
        # CLP per attention-second (payment / 2 looking), below each brand's bid.
        assert {winner.brand_name: winner.price_per_as.amount for winner in auction.winners} == {
            "Voltio": "2.275",
            "Café Patio": "1.2",
        }
        bids_by_id = {bid.campaign_id: bid.brand_name for bid in auction.bids}
        assert [
            (bids_by_id[item.campaign_id], item.reason, item.detail) for item in auction.exclusions
        ][:1] == [("Rayo", "competitor", "Rayo: misma categoría que Voltio")]
        # Other valued bidders in the moment lose with an honest reason too.
        assert all(item.reason for item in auction.exclusions)

        with client.websocket_connect("/ws/console") as reopened:
            assert ConsoleMoment.model_validate(reopened.receive_json()) == moment
            assert ConsoleAuction.model_validate(reopened.receive_json()) == auction
            assert ConsoleLatency.model_validate(reopened.receive_json()) == latency

        now = clock.advance(timedelta(milliseconds=100))
        agent.send_text(snapshot_for("minor-safe", 2, now).model_dump_json())
        assert ConsoleMoment.model_validate(console.receive_json()).minor_likely.value
        blocked = ConsoleAuction.model_validate(console.receive_json())
        assert blocked.winners == []
        assert blocked.exclusions[0].campaign_id == blocked.bids[0].campaign_id
        assert blocked.bids[0].brand_name == "Malta Sur"
        assert blocked.exclusions[0].reason == "safety"
