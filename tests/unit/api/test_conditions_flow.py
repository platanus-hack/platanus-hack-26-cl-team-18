"""Time of day × weather reach the live auction: a heat wave re-clears the moment on screen."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.bootstrap.demo import DEMO_SCREEN
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    ConditionsSet,
    ConsoleAuction,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

AFTERNOON = datetime(2026, 11, 22, 19, tzinfo=UTC)  # 4 p.m. in Santiago (UTC-3)


def _snapshot(at: datetime) -> AudienceSnapshot:
    return AudienceSnapshot(
        screen_id=DEMO_SCREEN,
        seq=1,
        measured_at=at,
        people=2,
        looking=2,
        tracklets=[
            Tracklet(id=f"t{i}", dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
            for i in (1, 2)
        ],
        objects=[DetectedObject(label="bottle", score=1)],
        minor_likely=MinorLikely(value=False, confidence=1),
    )


def _next_auction(console) -> ConsoleAuction:  # type: ignore[no-untyped-def]
    while True:
        message = console.receive_json()
        if message["type"] == "console.auction":
            return ConsoleAuction.model_validate(message)


def test_a_heat_wave_reprices_the_moment_with_its_reasons(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = FixedClock(AFTERNOON)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, demo_catalog="chile"
    )
    container = build_container(settings, clock=clock)
    assert container.demo is not None and container.demo.set_conditions is not None
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        agent.send_text(_snapshot(clock.now_utc()).model_dump_json())
        first = _next_auction(console)
        # No weather reading in tests: the afternoon alone shapes the bids.
        assert first.conditions is not None
        assert first.conditions.daypart == "afternoon"
        assert first.conditions.weather_source == "none"
        flat = {f.category: f.multiplier for f in first.conditions.factors}
        assert flat["ice_cream"] == "1.5" and flat["water"] == "1.3"

        assert container.demo.set_conditions(ConditionsSet(temperature_c=33))
        hot = _next_auction(console)
        assert hot.trigger == "conditions_changed"
        assert hot.conditions is not None
        assert hot.conditions.weather == ["hot"] and hot.conditions.weather_source == "override"
        factors = {f.category: f for f in hot.conditions.factors}
        assert factors["ice_cream"].multiplier == "2.4"
        assert factors["ice_cream"].reasons == ["afternoon×1.5", "hot×1.6"]
        assert factors["coffee"].multiplier == "0.68"
        for bid in hot.bids:
            expected = factors.get(bid.category)
            assert bid.context_multiplier == (expected.multiplier if expected else None)

        # Pinning the same weather again changes nothing; releasing it goes back to flat.
        assert not container.demo.set_conditions(ConditionsSet(temperature_c=33))
        assert container.demo.set_conditions(ConditionsSet(live=True))
        released = _next_auction(console)
        assert released.conditions is not None and released.conditions.weather == []
