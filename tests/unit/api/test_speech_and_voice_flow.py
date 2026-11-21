"""The full loop over the real ASGI app: speech topics flip the auction, voice-overs upgrade creatives.

agent socket -> Audience (speech evidence) -> Auction -> Creative -> player socket,
and Creative -> warm worker -> VoiceSynthesizer -> media cache -> creative.upgraded -> /media.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceUtterance,
    ConsoleMoment,
    CreativeReady,
    CreativeUpgraded,
    UpgradeKind,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.application.ports.voice import (
    SynthesizedVoice,
    VoiceFailure,
    VoiceRequest,
    VoiceSynthesisError,
)

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
NARRATOR = "NarratorVoice0001"
MP3 = b"ID3\x04fake-mp3"


class FakeSynth:
    def __init__(self, *, fail: VoiceFailure | None = None) -> None:
        self.requests: list[VoiceRequest] = []
        self.fail = fail
        self.lock = threading.Lock()

    async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
        with self.lock:
            self.requests.append(request)
        if self.fail is not None:
            raise VoiceSynthesisError(self.fail, "injected")
        return SynthesizedVoice(
            audio=MP3,
            content_type="audio/mpeg",
            model_id=request.model_id,
            voice_id=request.voice_id,
        )


def app_for(tmp_path: Path, *, speech: bool = True, synth: FakeSynth | None = None, **kw: Any):  # type: ignore[no-untyped-def]
    clock = FixedClock(T0)
    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        media_dir=tmp_path,
        speech_mode_screens=["demo-01"] if speech else [],
        elevenlabs_narrator_voice_id=NARRATOR if synth else None,
        **kw,
    )
    container = build_container(settings, clock=clock, voice_synthesizer=synth)
    return create_app(container=container), clock


def utterance(seq: int, *keyterms: str) -> str:
    return AudienceUtterance(
        screen_id="demo-01",
        seq=seq,
        measured_at=T0,
        text="uf, qué hambre",
        language="es",
        keyterms_hit=list(keyterms),
    ).model_dump_json()


@pytest.fixture
def speech_app(tmp_path: Path) -> Iterator[tuple[TestClient, FixedClock]]:
    app, clock = app_for(tmp_path)
    with TestClient(app) as client:
        yield client, clock


def creative(player: Any) -> CreativeReady:  # type: ignore[misc]
    return CreativeReady.model_validate(player.receive_json())


def test_it_heard_me_speech_flips_the_auction_to_a_co_brand(speech_app) -> None:  # type: ignore[no-untyped-def]
    client, clock = speech_app
    with (
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)  # house ad
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        assert paid_brands(creative(player)) == ("Voltio",)
        for _ in range(3):
            console.receive_json()

        clock.advance(timedelta(milliseconds=500))
        agent.send_text(utterance(1, "café"))  # "uf, qué hambre… un café"
        flipped = creative(player)
        assert set(paid_brands(flipped)) == {"Voltio", "Café Patio"}
        moment = ConsoleMoment.model_validate(console.receive_json())
        coffee = next(s for s in moment.segments if s.label == "Coffee")
        assert [src.value for src in coffee.sources] == ["speech"]


def test_speech_expires_and_the_screen_reverts(tmp_path: Path) -> None:
    app, clock = app_for(tmp_path, speech_topic_ttl_s=5)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        creative(player)
        agent.send_text(utterance(1, "café"))
        assert set(paid_brands(creative(player))) == {"Voltio", "Café Patio"}
        now = clock.advance(timedelta(seconds=6))
        agent.send_text(snapshot_for("fitness", 2, now).model_dump_json())
        assert paid_brands(creative(player)) == ("Voltio",)


def test_speech_mode_off_ignores_utterances(tmp_path: Path) -> None:
    app, clock = app_for(tmp_path, speech=False)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        creative(player)
        agent.send_text(utterance(1, "café"))
        clock.advance(timedelta(milliseconds=100))
        agent.send_text(snapshot_for("empty", 2, clock.now_utc()).model_dump_json())
        # The next thing on screen is the house ad from the empty scene, not a co-brand.
        assert paid_brands(creative(player)) == ()


def test_voiceover_upgrades_the_creative_and_is_served_from_media(tmp_path: Path) -> None:
    synth = FakeSynth()
    app, clock = app_for(tmp_path, synth=synth)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        ready = creative(player)
        assert ready.spec.voiceover_url is None  # never waits for the voice
        upgraded = CreativeUpgraded.model_validate(player.receive_json())
        assert upgraded.creative_id == ready.creative_id
        assert upgraded.upgrade_kind is UpgradeKind.VOICE
        assert upgraded.spec.voiceover_url and upgraded.trace.upgraded_at is not None
        assert upgraded.spec.slots == ready.spec.slots  # only the voice changed
        (request,) = synth.requests
        assert request.voice_id == NARRATOR and request.model_id == "eleven_flash_v2_5"
        assert "Voltio + Café Patio" in request.text

        media = client.get(upgraded.spec.voiceover_url)
        assert media.status_code == 200 and media.content == MP3

        # A reconnecting player gets the upgraded creative, voice included.
        with client.websocket_connect("/ws/screen/demo-01") as again:
            assert creative(again).spec.voiceover_url == upgraded.spec.voiceover_url


def test_cached_voiceover_is_applied_on_the_hot_path_without_a_new_call(tmp_path: Path) -> None:
    synth = FakeSynth()
    app, clock = app_for(tmp_path, synth=synth)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        creative(player)
        player.receive_json()  # voice upgrade
        for seq, scene in ((2, "empty"), (3, "co-brand")):
            agent.send_text(
                snapshot_for(
                    scene, seq, clock.advance(timedelta(milliseconds=200))
                ).model_dump_json()
            )
            creative(player)
        upgraded = CreativeUpgraded.model_validate(player.receive_json())
        assert upgraded.spec.voiceover_url is not None
        assert len(synth.requests) == 1  # second time: cache hit


def test_quota_exhaustion_degrades_to_silent_creatives_and_tells_the_console(
    tmp_path: Path,
) -> None:
    synth = FakeSynth(fail=VoiceFailure.QUOTA)
    app, clock = app_for(tmp_path, synth=synth)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        assert set(paid_brands(creative(player))) == {"Voltio", "Café Patio"}
        types = [console.receive_json()["type"] for _ in range(4)]
        assert types[-1] == "console.degradation"
