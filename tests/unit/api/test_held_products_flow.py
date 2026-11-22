"""Complete the basket (ADR 0008 D2): a prop marker reaches the auction as a held product."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import ConsoleAuction, ConsoleMoment, DetectedObject
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock


def test_a_held_cafe_patio_cup_boosts_its_partner_and_shows_on_the_console(tmp_path) -> None:
    clock = FixedClock(datetime(2026, 11, 21, tzinfo=UTC))
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock)

    def voltio_value(auction: ConsoleAuction) -> Decimal:
        (line,) = (bid for bid in auction.bids if bid.brand_name == "Voltio")
        return Decimal(line.value_per_s.amount)

    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        plain = snapshot_for("fitness", 1, clock.now_utc())
        agent.send_text(plain.model_dump_json())
        before = ConsoleMoment.model_validate(console.receive_json())
        baseline = voltio_value(ConsoleAuction.model_validate(console.receive_json()))
        assert before.held_products == []
        console.receive_json()  # latency

        now = clock.advance(timedelta(milliseconds=100))
        marker = DetectedObject(label="product:cafe-patio", score=0.95)
        holding = snapshot_for("fitness", 2, now).model_copy(
            update={"objects": [*plain.objects, marker]}
        )
        agent.send_text(holding.model_dump_json())
        moment = ConsoleMoment.model_validate(console.receive_json())
        auction = ConsoleAuction.model_validate(console.receive_json())

        assert [(p.category, p.brand_ref, p.score) for p in moment.held_products] == [
            ("coffee", "cafe-patio", 0.95)
        ]
        # The marker is a held product, never an audience segment.
        assert [s.label for s in moment.segments] == ["Deporte y ejercicio"]
        assert voltio_value(auction) > baseline
