"""Creative generation over the real ASGI app (docs/creative-generation.md).

agent socket -> audience -> auction -> brief (angle, group offer) -> stock copy -> player,
then warm: CopyWriter -> guard -> score -> creative.upgraded(copy) on the same creative.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceUtterance,
    CopySourceKind,
    CreativeReady,
    CreativeStageName,
    CreativeUpgraded,
    UpgradeKind,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.application.ports.copywriter import TokenUsage, WrittenCopy
from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopySource

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
AI_CLOSE = "Muestra el QR en caja · válido 10 min"


class FakeCopywriter:
    """Stock facts with a fresher close line: safe, on-target, so it must air."""

    name = "fake"

    def __init__(self, *, close: str = AI_CLOSE) -> None:
        self.close = close
        self.briefs: list[CreativeBrief] = []
        self.lock = threading.Lock()

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        with self.lock:
            self.briefs.append(brief)
        draft = replace(
            StockCopywriter().compose(brief),
            close=self.close,
            source=CopySource.AI,
            model_version="fake@low",
        )
        return WrittenCopy(draft=draft, usage=TokenUsage(input_tokens=900, output_tokens=150))


def app_for(tmp_path: Path, copywriter: FakeCopywriter | None = None):  # type: ignore[no-untyped-def]
    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, speech_mode_screens=["demo-01"]
    )
    container = build_container(settings, clock=clock, copywriter=copywriter)
    return create_app(container=container), clock


def ready(player: Any) -> CreativeReady:  # type: ignore[misc]
    return CreativeReady.model_validate(player.receive_json())


def test_two_people_get_a_group_sized_offer_with_stages(tmp_path: Path) -> None:
    app, clock = app_for(tmp_path)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        ready(player)  # house ad
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        creative = ready(player)
        spec = creative.spec
        assert set(paid_brands(creative)) == {"Voltio", "Café Patio"}
        assert spec.offer is not None and spec.offer.party_size == 2
        assert (spec.offer.price.amount, spec.offer.label) == ("5990", "Dúo post-entreno")
        assert [s.name for s in spec.stages] == list(CreativeStageName)
        assert "los 2" in spec.stages[0].copy_text
        assert spec.copy_source is CopySourceKind.STOCK


def test_it_heard_me_hungry_turns_the_copy_to_food(tmp_path: Path) -> None:
    app, clock = app_for(tmp_path)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        ready(player)
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        assert paid_brands(ready(player)) == ("Voltio",)
        clock.advance(timedelta(milliseconds=500))
        agent.send_text(
            AudienceUtterance(
                screen_id="demo-01",
                seq=1,
                measured_at=clock.now_utc(),
                text="uf, qué hambre",
                language="es",
                keyterms_hit=["hambre"],
            ).model_dump_json()
        )
        flipped = ready(player)
        assert set(paid_brands(flipped)) == {"Voltio", "Café Patio"}
        assert flipped.spec.angle == "hunger"
        assert "Hambre" in flipped.spec.stages[0].copy_text


def test_ai_copy_upgrades_the_creative_in_place(tmp_path: Path) -> None:
    writer = FakeCopywriter()
    app, clock = app_for(tmp_path, writer)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        ready(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        first = ready(player)
        assert first.spec.copy_source is CopySourceKind.STOCK  # the hot path never waits

        upgraded = CreativeUpgraded.model_validate(player.receive_json())
        assert upgraded.upgrade_kind is UpgradeKind.COPY
        assert upgraded.creative_id == first.creative_id
        assert upgraded.spec.copy_source is CopySourceKind.AI
        assert upgraded.spec.stages[2].copy_text == AI_CLOSE
        assert upgraded.spec.offer == first.spec.offer  # AI never touches the offer
        assert upgraded.trace.upgraded_at is not None
        assert len(writer.briefs) == 1


def test_rejected_ai_copy_never_reaches_the_screen(tmp_path: Path) -> None:
    writer = FakeCopywriter(close="Mejor que Rayo · QR")  # competitor name: guard rejects
    app, clock = app_for(tmp_path, writer)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        ready(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        first = ready(player)
        # The next message the player gets is the next decision, not an upgrade.
        now = clock.advance(timedelta(seconds=3))
        agent.send_text(snapshot_for("fitness", 2, now).model_dump_json())
        nxt = player.receive_json()
        assert nxt["type"] == "creative.ready" and nxt["creative_id"] != first.creative_id
