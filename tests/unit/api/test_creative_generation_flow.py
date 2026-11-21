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

import anyio
import pytest
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
        # docs/demo.md beat 2: hunger brings the burger partner (Trío post-gym).
        assert set(paid_brands(flipped)) == {"Voltio", "Brasa Burger"}
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


def test_ai_copy_retains_the_baskets_qr_and_preapproved_loop(tmp_path: Path) -> None:
    loop = tmp_path / "loops" / "cafe-patio+voltio.mp4"
    loop.parent.mkdir()
    loop.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    app, clock = app_for(tmp_path, FakeCopywriter())
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        ready(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        first = ready(player)
        assert first.spec.qr_url and first.spec.redemption_code and first.spec.redeem_url
        assert first.spec.video_url == "/media/loops/cafe-patio+voltio.mp4"
        upgraded = CreativeUpgraded.model_validate(player.receive_json())
        assert upgraded.spec.video_url == first.spec.video_url
        assert upgraded.spec.qr_url == first.spec.qr_url
        assert upgraded.spec.redemption_code == first.spec.redemption_code
        assert upgraded.spec.redeem_url == first.spec.redeem_url
        assert upgraded.spec.offer == first.spec.offer
        assert upgraded.creative_id == first.creative_id
        assert client.get(upgraded.spec.video_url).content == loop.read_bytes()
        assert client.get(f"/r/{upgraded.spec.redemption_code}").status_code == 200
        redeemed = client.post(
            f"/r/{upgraded.spec.redemption_code}/redeem", json={"nonce": "copy-upgrade-device"}
        )
        assert redeemed.status_code == 200
        assert set(redeemed.json()["brands"]) == set(paid_brands(first))
        assert redeemed.json()["new"]
        again = client.post(
            f"/r/{upgraded.spec.redemption_code}/redeem", json={"nonce": "copy-upgrade-device"}
        )
        assert not again.json()["new"]


def test_ready_trace_includes_basket_asset_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, clock = app_for(tmp_path)

    def delayed_qr(url: str) -> str:
        clock.advance(timedelta(milliseconds=40))
        return "data:image/svg+xml,fixture"

    monkeypatch.setattr("app.bootstrap.demo.qr_data_url", delayed_qr)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        ready(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        first = ready(player)
        assert first.trace.ready_at == clock.now_utc()
        assert first.trace.received_at is not None
        assert (first.trace.ready_at - first.trace.received_at).total_seconds() >= 0.04


@pytest.mark.parametrize(
    "scenes", [("co-brand", "fitness", "adult"), ("co-brand", "fitness", "co-brand")]
)
def test_queued_copy_skips_retired_briefs_but_serves_the_current_one(
    tmp_path: Path, scenes: tuple[str, ...]
) -> None:
    writer = FakeCopywriter()
    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, warm_concurrency={"claude": 1}
    )
    container = build_container(settings, clock=clock, copywriter=writer)
    started, release = threading.Event(), threading.Event()

    async def occupy_provider() -> None:
        started.set()
        await anyio.to_thread.run_sync(release.wait)

    with TestClient(create_app(container=container)) as client:
        container.warm.submit_threadsafe("claude", "occupied", occupy_provider)
        assert started.wait(2)
        try:
            with (
                client.websocket_connect("/ws/screen/demo-01") as player,
                client.websocket_connect("/ws/agent") as agent,
            ):
                ready(player)
                for seq, scene in enumerate(scenes, 1):
                    clock.advance(timedelta(milliseconds=200))
                    agent.send_text(snapshot_for(scene, seq, clock.now_utc()).model_dump_json())
                    current = ready(player)
                release.set()
                upgraded = CreativeUpgraded.model_validate(player.receive_json())
                assert upgraded.creative_id == current.creative_id
                assert len(writer.briefs) == 1
                assert {b.name for b in writer.briefs[0].brands} == set(paid_brands(current))
        finally:
            release.set()


@pytest.mark.parametrize(
    "scenes", [("co-brand", "fitness", "adult"), ("co-brand", "fitness", "co-brand")]
)
def test_queued_voice_skips_retired_lines_but_serves_the_current_one(
    tmp_path: Path, scenes: tuple[str, ...]
) -> None:
    from tests.unit.api.test_speech_and_voice_flow import NARRATOR, FakeSynth

    synth = FakeSynth()
    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        media_dir=tmp_path,
        elevenlabs_budget_file=tmp_path / "budget.json",
        elevenlabs_narrator_voice_id=NARRATOR,
        voiceover_prewarm=False,
        warm_concurrency={"elevenlabs": 1},
    )
    container = build_container(settings, clock=clock, voice_synthesizer=synth)
    started, release = threading.Event(), threading.Event()

    async def occupy_provider() -> None:
        started.set()
        await anyio.to_thread.run_sync(release.wait)

    with TestClient(create_app(container=container)) as client:
        container.warm.submit_threadsafe("elevenlabs", "occupied", occupy_provider)
        assert started.wait(2)
        try:
            with (
                client.websocket_connect("/ws/screen/demo-01") as player,
                client.websocket_connect("/ws/agent") as agent,
            ):
                ready(player)
                for seq, scene in enumerate(scenes, 1):
                    clock.advance(timedelta(milliseconds=200))
                    agent.send_text(snapshot_for(scene, seq, clock.now_utc()).model_dump_json())
                    current = ready(player)
                release.set()
                upgraded = CreativeUpgraded.model_validate(player.receive_json())
                assert upgraded.upgrade_kind is UpgradeKind.VOICE
                assert upgraded.creative_id == current.creative_id
                assert upgraded.spec.voiceover_url
                assert len(synth.requests) == 1
        finally:
            release.set()


def test_an_already_running_copy_call_finishes_and_warms_a_returning_moment(tmp_path: Path) -> None:
    started, release = threading.Event(), threading.Event()

    class InFlightWriter(FakeCopywriter):
        async def write(self, brief: CreativeBrief) -> WrittenCopy:
            started.set()
            await anyio.to_thread.run_sync(release.wait)
            return await super().write(brief)

    writer = InFlightWriter()
    clock = FixedClock(T0)
    container = build_container(
        AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path),
        clock=clock,
        copywriter=writer,
    )
    with TestClient(create_app(container=container)) as client:
        try:
            with (
                client.websocket_connect("/ws/screen/demo-01") as player,
                client.websocket_connect("/ws/agent") as agent,
            ):
                ready(player)
                agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
                ready(player)
                assert started.wait(2)
                clock.advance(timedelta(milliseconds=200))
                agent.send_text(snapshot_for("empty", 2, clock.now_utc()).model_dump_json())
                assert ready(player).auction_id is None
                release.set()
                client.portal.call(container.warm.drain)
                clock.advance(timedelta(milliseconds=200))
                agent.send_text(snapshot_for("co-brand", 3, clock.now_utc()).model_dump_json())
                returning = ready(player)
                assert returning.spec.copy_source is CopySourceKind.CACHE
                assert len(writer.briefs) == 1
        finally:
            release.set()
