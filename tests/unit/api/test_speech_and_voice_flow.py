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
        elevenlabs_budget_file=tmp_path / "budget.json",
        voiceover_prewarm=kw.pop("voiceover_prewarm", False),
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
        text="un café, porfa",
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
        agent.send_text(utterance(1, "café"))  # "un café, porfa"
        flipped = creative(player)
        assert set(paid_brands(flipped)) == {"Voltio", "Café Patio"}
        moment = ConsoleMoment.model_validate(console.receive_json())
        coffee = next(s for s in moment.segments if s.label == "Café")
        assert [src.value for src in coffee.sources] == ["speech"]


def test_coffee_speech_changes_only_the_matching_screen(speech_app) -> None:  # type: ignore[no-untyped-def]
    client, clock = speech_app
    with (
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/screen/other") as other,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        creative(other)
        for screen, socket in (("demo-01", player), ("other", other)):
            snapshot = snapshot_for("fitness", 1, clock.now_utc()).model_copy(
                update={"screen_id": screen}
            )
            agent.send_text(snapshot.model_dump_json())
            assert paid_brands(creative(socket)) == ("Voltio",)
        clock.advance(timedelta(milliseconds=500))
        agent.send_text(utterance(1, "café"))
        assert set(paid_brands(creative(player))) == {"Voltio", "Café Patio"}
        with client.websocket_connect("/ws/screen/other") as reconnect:
            assert paid_brands(creative(reconnect)) == ("Voltio",)


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


def test_coffee_can_change_the_ad_repeatedly_after_each_intent_expires(tmp_path: Path) -> None:
    app, clock = app_for(tmp_path, speech_topic_ttl_s=2)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("fitness", 1, clock.now_utc()).model_dump_json())
        assert paid_brands(creative(player)) == ("Voltio",)
        ids = set()
        for seq in range(1, 6):
            agent.send_text(utterance(seq, "café"))
            changed = creative(player)
            assert set(paid_brands(changed)) == {"Voltio", "Café Patio"}
            assert changed.creative_id not in ids
            ids.add(changed.creative_id)
            now = clock.advance(timedelta(seconds=3))
            agent.send_text(snapshot_for("fitness", seq + 1, now).model_dump_json())
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
        # The approved offer, normalized for speech: no "$", no "+", price in words.
        assert "Voltio y Café Patio" in request.text and "pesos" in request.text
        assert "$" not in request.text and "+" not in request.text

        media = client.get(upgraded.spec.voiceover_url)
        assert media.status_code == 200 and media.content == MP3

        # A reconnecting player gets the upgraded creative, voice included.
        with client.websocket_connect("/ws/screen/demo-01") as again:
            assert creative(again).spec.voiceover_url == upgraded.spec.voiceover_url


def test_cached_voiceover_rides_in_creative_ready_without_a_new_call(tmp_path: Path) -> None:
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
        player.receive_json()  # first time: the voice arrives as an upgrade
        for seq, scene in ((2, "empty"), (3, "co-brand")):
            agent.send_text(
                snapshot_for(
                    scene, seq, clock.advance(timedelta(milliseconds=200))
                ).model_dump_json()
            )
            again = creative(player)
        # Second time: the cached voice is already in creative.ready (no upgrade round trip).
        assert again.spec.voiceover_url is not None
        assert len(synth.requests) == 1


def test_prewarmed_offers_play_with_their_voice_from_the_first_frame(tmp_path: Path) -> None:
    synth = FakeSynth()
    app, clock = app_for(tmp_path, synth=synth, voiceover_prewarm=True)
    warmed = threading.Event()
    startup = tuple(app.state.container.startup)

    async def complete_startup() -> None:
        for hook in startup:
            await hook()
        warmed.set()

    app.state.container.startup[:] = [complete_startup]
    with TestClient(app) as client:
        assert warmed.wait(5), "all prewarm results must be persisted before playback"
        with (
            client.websocket_connect("/ws/screen/demo-01") as player,
            client.websocket_connect("/ws/agent") as agent,
        ):
            creative(player)
            before = len(synth.requests)
            agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
            ready = creative(player)
            assert ready.spec.voiceover_url is not None  # voice from the very first frame
            assert len(synth.requests) == before  # nothing synthesized at play time
            assert all("$" not in r.text and "pesos" in r.text for r in synth.requests)


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


def test_character_budget_stops_new_lines_but_cached_ones_keep_playing(tmp_path: Path) -> None:
    synth = FakeSynth()
    # Fits one demo line (~66 chars spoken) but not a second one (the solo line is ~46):
    # copy can change, so the test asserts the arithmetic instead of hard-coding lengths.
    budget = 80
    app, clock = app_for(tmp_path, synth=synth, elevenlabs_char_budget=budget)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        creative(player)
        agent.send_text(snapshot_for("co-brand", 1, clock.now_utc()).model_dump_json())
        creative(player)
        first = CreativeUpgraded.model_validate(player.receive_json())
        spent = len(synth.requests[0].text)
        assert spent <= budget < 2 * spent, "budget must fit exactly one line"

        # A new line no longer fits what is left: it plays silent, no provider call.
        agent.send_text(
            snapshot_for("fitness", 2, clock.advance(timedelta(milliseconds=200))).model_dump_json()
        )
        assert paid_brands(creative(player)) == ("Voltio",)

        # Back to the co-brand: the next frame is its creative, not a voice for fitness.
        agent.send_text(
            snapshot_for(
                "co-brand", 3, clock.advance(timedelta(milliseconds=200))
            ).model_dump_json()
        )
        again = creative(player)
        assert set(paid_brands(again)) == {"Voltio", "Café Patio"}
        # Cache hit: free, and the voice rides in creative.ready itself.
        assert again.spec.voiceover_url == first.spec.voiceover_url
        assert len(synth.requests) == 1
    assert f'"spent": {spent}' in (tmp_path / "budget.json").read_text()


@pytest.mark.parametrize("retire_subscriber", [False, True])
def test_shared_voice_result_reaches_each_screen_without_blocking_another_offer(
    tmp_path: Path,
    retire_subscriber: bool,
) -> None:
    import asyncio
    from contextlib import ExitStack

    class GatedSynth(FakeSynth):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()
            self.different_started = threading.Event()
            self.first_line: str | None = None

        async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
            if self.first_line is None:
                self.first_line = request.text
            if request.text == self.first_line:
                self.started.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.001)
            else:
                self.different_started.set()
            return await super().synthesize(request)

    synth = GatedSynth()
    app, _clock = app_for(
        tmp_path,
        synth=synth,
        creative_ai_copy=False,
        audience_ai=False,
        warm_concurrency={"elevenlabs": 2},
    )
    with TestClient(app) as client, ExitStack() as stack:
        players = [
            stack.enter_context(client.websocket_connect(f"/ws/screen/{name}"))
            for name in ("one", "two", "three")
        ]
        agent = stack.enter_context(client.websocket_connect("/ws/agent"))
        for player in players:
            assert creative(player).auction_id is None
        ready = []
        observed = []

        class Observer:
            async def send_text(self, text):
                import json

                observed.append(json.loads(text))

        observer = Observer()
        client.portal.call(app.state.container.hub.add_player, "two", observer)
        try:
            for index, scene in enumerate(("co-brand", "co-brand", "fitness")):
                snapshot = snapshot_for(scene, 1, T0).model_copy(
                    update={"screen_id": ("one", "two", "three")[index]}
                )
                agent.send_text(snapshot.model_dump_json())
                ready.append(creative(players[index]))
                assert ready[-1].auction_id is not None
                if index == 0:
                    assert synth.started.wait(1)
            assert synth.different_started.wait(0.5), (
                "shared-result waiters must release provider slots"
            )
            if retire_subscriber:
                now = _clock.advance(timedelta(milliseconds=100))
                snapshot = snapshot_for("empty", 2, now).model_copy(update={"screen_id": "two"})
                agent.send_text(snapshot.model_dump_json())
                assert creative(players[1]).auction_id is None
        finally:
            synth.release.set()
        active = (0, 2) if retire_subscriber else (0, 1, 2)
        upgrades = [CreativeUpgraded.model_validate(players[i].receive_json()) for i in active]
        for original, upgrade in zip([ready[i] for i in active], upgrades, strict=True):
            assert upgrade.creative_id == original.creative_id
            assert upgrade.upgrade_kind is UpgradeKind.VOICE
            assert upgrade.spec.voiceover_url is not None
        if not retire_subscriber:
            assert upgrades[0].spec.voiceover_url == upgrades[1].spec.voiceover_url
        else:
            client.portal.call(app.state.container.warm.drain)
            assert not any(m["type"] == "creative.upgraded" for m in observed)
        assert len(synth.requests) == 2
        client.portal.call(app.state.container.hub.remove_player, "two", observer)
