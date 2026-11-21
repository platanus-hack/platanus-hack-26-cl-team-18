"""The demo catalog must understand what the real perception agent sends.

The simulator uses fixture labels ("sports ball", "tap handle fixture"); a live
camera sends COCO labels ("cup", "bottle") and held props as "product:<slug>"
(edge/agent/vision); held props boost partners rather than adding a segment. Regression: with only fixture labels, every live moment
had zero segments and the screen stayed on the house ad.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    CreativeReady,
    DetectedObject,
    MinorLikely,
    Tracklet,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock


def _agent_snapshot(seq: int, at: datetime, labels: dict[str, float]) -> AudienceSnapshot:
    """Shaped like edge/agent/vision/snapshot.py output: tracklets + nearby objects."""
    return AudienceSnapshot(
        screen_id="demo-01",
        seq=seq,
        measured_at=at,
        people=2,
        looking=2,
        tracklets=[
            Tracklet(id="t1", dwell_s=2.0, looking=True, distance_band="mid"),
            Tracklet(id="t2", dwell_s=1.5, looking=True, distance_band="near"),
        ],
        objects=[DetectedObject(label=label, score=score) for label, score in labels.items()],
        minor_likely=MinorLikely(value=False, confidence=0.0),
    )


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ({"cup": 0.81}, {"Café Patio"}),
        # A held prop boosts its basket partners, it never sells itself
        # (docs/demo.md beat 3, ADR 0008 D2): alone, the house ad keeps playing.
        ({"product:cafe-patio": 1.0}, set()),
        ({"bottle": 0.7, "cup": 0.8}, {"Voltio", "Café Patio"}),
        ({"laptop": 0.9}, set()),  # not an interest the seed sells: house ad
    ],
)
def test_live_agent_labels_reach_a_paid_creative(
    tmp_path, labels: dict[str, float], expected: set[str]
) -> None:
    clock = FixedClock(datetime(2026, 11, 21, tzinfo=UTC))
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock)

    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        assert paid_brands(CreativeReady.model_validate(player.receive_json())) == ()
        now = clock.advance(timedelta(milliseconds=100))
        agent.send_text(_agent_snapshot(1, now, labels).model_dump_json())
        if not expected:
            return  # no paid change; the house ad keeps playing
        creative = CreativeReady.model_validate(player.receive_json())
        assert set(paid_brands(creative)) == expected
