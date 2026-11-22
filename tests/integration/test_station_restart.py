"""A real Postgres restart: stable budgets, working old QR, deduped sale and visible receipt."""

import os
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.main import create_app
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

pytestmark = pytest.mark.integration


def test_budgets_qr_redemptions_and_receipt_survive_a_backend_restart(tmp_path):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("requires the local migrated Postgres")
    screen = "restart-" + str(IdGenerator().new_uuid7())
    at = datetime.now(UTC)
    clock = FixedClock(at)
    settings = AppSettings(
        _env_file=None,
        app_env="local",
        database_url=dsn,
        audience_ai=False,
        creative_ai_copy=False,
        voiceovers_enabled=False,
        demo_catalog="fictional",
        auction_swap_holds=False,
        media_dir=tmp_path,
        public_base_url="http://192.168.1.20:5188",
    )
    first = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=first)) as client,
        client.websocket_connect("/ws/screen/" + screen) as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        for seq in range(6):
            clock_value = at + timedelta(seconds=seq)
            clock.set(clock_value)
            snapshot = AudienceSnapshot(
                screen_id=screen,
                seq=seq + 1,
                measured_at=clock_value,
                people=2,
                looking=2,
                tracklets=[
                    Tracklet(
                        id=f"t{i}", dwell_s=seq + 1, looking=True, distance_band=DistanceBand.NEAR
                    )
                    for i in range(2)
                ],
                objects=[
                    DetectedObject(label="bottle", score=1),
                    DetectedObject(label="cup", score=1),
                ],
                minor_likely=MinorLikely(value=False, confidence=1),
            )
            agent.send_text(snapshot.model_dump_json())
            if seq == 0:
                while (creative := player.receive_json()).get("type") != "creative.ready":
                    pass
        code = creative["spec"]["redemption_code"]
        assert code
        redemption, created = first.demo.redeem(code, "restart-test-device-012345")
        assert created
        player.send_json(
            {
                "v": 1,
                "type": "playback.report",
                "screen_id": screen,
                "creative_id": creative["creative_id"],
                "seq": 1,
                "started_at": at.isoformat(),
                "ended_at": (at + timedelta(seconds=5)).isoformat(),
            }
        )
        while (receipt := console.receive_json()).get("type") != "console.receipt" or receipt.get(
            "screen_id"
        ) != screen:
            pass
        assert receipt["screen_id"] == screen and receipt["charges"]
        assert first.writer.flush(timeout=10)
        before = first.demo.book_view().model_dump(mode="json")
    second = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=second)) as client,
        client.websocket_connect("/ws/screen/" + screen) as player,
        client.websocket_connect("/ws/console") as console,
    ):
        after = second.demo.book_view().model_dump(mode="json")
        for original, restored in zip(before["lines"], after["lines"], strict=True):
            assert original["campaign_id"] == restored["campaign_id"]
            assert original["spent"] == restored["spent"]
            assert original["budget"] == restored["budget"]
        assert client.get("/r/" + code).status_code == 200
        again, created = second.demo.redeem(code, "restart-test-device-012345")
        assert not created and again == redemption
        while (restored_receipt := console.receive_json()).get(
            "type"
        ) != "console.receipt" or restored_receipt.get("screen_id") != screen:
            pass
        assert restored_receipt == receipt
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM redemption WHERE code=%s", (code,))
            assert cur.fetchone()[0] == 1
