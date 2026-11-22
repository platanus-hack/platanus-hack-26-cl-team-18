"""A promo the team wrote word for word airs that line, never an AI rewrite of it.

Catalog.pinned_copy (Chile: the hackathon promo) keeps the warm AI copywriter off any
creative that includes the brand; every other campaign still gets its AI upgrade.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    AudienceUtterance,
    CreativeReady,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.contract.speech_matching import DEFAULT_KEYTERMS
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from tests.unit.api.test_creative_generation_flow import FakeCopywriter

AT = datetime(2026, 11, 21, 5, tzinfo=UTC)


def _air(tmp_path: Path, *, objects: list[str], text: str | None) -> tuple[list[str], str, list]:
    writer = FakeCopywriter()
    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        media_dir=tmp_path,
        demo_catalog="chile",
        speech_mode_screens=["demo-01"],
    )
    container = build_container(settings, clock=FixedClock(AT), copywriter=writer)
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
                measured_at=AT,
                people=2,
                looking=2,
                tracklets=[
                    Tracklet(id=f"t{i}", dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
                    for i in (1, 2)
                ],
                objects=[DetectedObject(label=label, score=1) for label in objects],
                minor_likely=MinorLikely(value=False, confidence=1),
            ).model_dump_json()
        )
        if text is not None:
            agent.send_text(
                AudienceUtterance(
                    screen_id="demo-01",
                    seq=2,
                    measured_at=AT,
                    text=text,
                    language="es",
                    keyterms_hit=list(DEFAULT_KEYTERMS.understand(text).wanted),
                    utterance_id="u1",
                ).model_dump_json()
            )
        time.sleep(0.3)
        assert client.portal is not None
        client.portal.call(container.warm.drain)
        with client.websocket_connect("/ws/screen/demo-01") as fresh:
            shown = CreativeReady.model_validate(fresh.receive_json())
    teaser = next(s.copy_text for s in shown.spec.stages if s.name == "teaser")
    return [slot.brand_name for slot in shown.spec.slots], teaser, writer.briefs


def test_the_hackathon_promo_keeps_the_teams_line_and_never_calls_the_ai(tmp_path: Path) -> None:
    brands, teaser, briefs = _air(tmp_path, objects=[], text="esto es Platanus")
    assert brands == ["Monster Energy"]
    assert teaser == "¿No duermes? 6x Monster por $1.990."
    assert not any("Monster Energy" in brief.names for brief in briefs)


def test_other_campaigns_still_get_their_ai_upgrade(tmp_path: Path) -> None:
    brands, _, briefs = _air(tmp_path, objects=["energy drink can", "cup"], text=None)
    assert sorted(brands) == ["Juan Valdez", "Score"]
    assert briefs  # the warm copywriter was asked for the hero basket
