"""Warm persistence must not stall sensor dispatch or duplicate shared synthesis."""

from __future__ import annotations

import asyncio
import threading

import anyio
import pytest

from app.modules.creative.application.voiceover import VoiceoverService
from tests.unit.creative.test_voiceover import PROFILE, VOICE, FakeSynth


@pytest.mark.parametrize("stage", ["lookup", "put", "remaining", "spend"])
def test_warm_voice_io_does_not_block_the_event_loop(stage):
    entered, release = threading.Event(), threading.Event()

    def disk(operation):
        if operation == stage:
            entered.set()
            release.wait(2)

    class Store:
        def url_if_cached(self, key, extension):
            disk("lookup")
            return None

        def put(self, key, extension, data):
            disk("put")
            return "/media/voiceovers/result.mp3"

    class Budget:
        def remaining(self):
            disk("remaining")
            return 10000

        def spend(self, characters):
            disk("spend")

    async def run():
        svc = VoiceoverService(FakeSynth(), Store(), PROFILE, Budget())
        task = asyncio.create_task(svc.voiceover_url("Approved offer", VOICE))
        # Release from a different thread even on the broken implementation, so
        # the assertion reports starvation instead of hanging the whole runner.
        timer = threading.Timer(0.4, release.set)
        timer.start()
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            assert not release.is_set(), f"{stage} monopolized the event loop"
        finally:
            release.set()
            timer.cancel()
            await task

    anyio.run(run)


def test_unrelated_agent_snapshot_reaches_player_during_slow_voice_persistence(tmp_path):
    import time
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient
    from scripts.replay_audience import snapshot_for

    from app.bootstrap.container import build_container
    from app.config.settings import AppSettings
    from app.main import create_app
    from app.modules.creative.infrastructure.media_store import FileMediaStore

    entered, release = threading.Event(), threading.Event()
    timers = []

    class SlowStore(FileMediaStore):
        def put(self, key, extension, data):
            entered.set()
            timer = threading.Timer(0.4, release.set)
            timers.append(timer)
            timer.start()
            release.wait(2)
            return super().put(key, extension, data)

    settings = AppSettings(
        app_env="test",
        demo_simulator=True,
        creative_ai_copy=False,
        audience_ai=False,
        media_dir=tmp_path,
        elevenlabs_narrator_voice_id=VOICE,
        elevenlabs_budget_file=tmp_path / "budget.json",
    )
    container = build_container(settings, voice_synthesizer=FakeSynth())
    container.voiceovers._store = SlowStore(tmp_path)
    with TestClient(create_app(container=container)) as client:
        with (
            client.websocket_connect("/ws/screen/one") as one,
            client.websocket_connect("/ws/screen/two") as two,
            client.websocket_connect("/ws/agent") as agent,
        ):
            assert one.receive_json()["auction_id"] is None
            assert two.receive_json()["auction_id"] is None
            try:
                first = snapshot_for("co-brand", 1, datetime.now(UTC)).model_copy(
                    update={"screen_id": "one"}
                )
                agent.send_text(first.model_dump_json())
                assert entered.wait(1)
                started = time.perf_counter()
                second = snapshot_for("fitness", 1, datetime.now(UTC)).model_copy(
                    update={"screen_id": "two"}
                )
                agent.send_text(second.model_dump_json())
                assert two.receive_json()["auction_id"] is not None
                assert time.perf_counter() - started < 0.2
                assert not release.is_set()
            finally:
                release.set()
                for timer in timers:
                    timer.cancel()


def test_slow_lookup_installs_one_flight_before_yielding_and_notifies_subscribers():
    entered, release = threading.Event(), threading.Event()

    class Store:
        lookups = 0
        committed = False

        def url_if_cached(self, key, extension):
            self.lookups += 1
            entered.set()
            release.wait(2)
            return None

        def put(self, key, extension, data):
            self.committed = True
            return "/media/result.mp3"

    async def run():
        store, synth = Store(), FakeSynth()
        svc = VoiceoverService(synth, store, PROFILE)
        first = asyncio.create_task(svc.voiceover_url("Approved offer", VOICE))
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            delivered = []

            def notify(url):
                assert store.committed
                delivered.append(url)

            assert svc.subscribe_pending(
                "Approved offer", VOICE, consumer="screen", on_ready=notify
            )
            second = asyncio.create_task(svc.voiceover_url("Approved offer", VOICE))
            await asyncio.sleep(0)
            release.set()
            urls = await asyncio.gather(first, second)
            assert urls == ["/media/result.mp3"] * 2
            assert delivered == ["/media/result.mp3"]
            assert store.lookups == len(synth.requests) == 1
        finally:
            release.set()
            await first

    anyio.run(run)


def test_provider_disabled_during_budget_read_cannot_start_another_call():
    from app.modules.creative.application.ports.voice import VoiceFailure

    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()

    class Budget:
        reads = 0

        def remaining(self):
            with lock:
                self.reads += 1
                first = self.reads == 1
            if first:
                entered.set()
                release.wait(2)
            return 10000

        def spend(self, characters):
            raise AssertionError("failed requests must not spend characters")

    class Store:
        def url_if_cached(self, key, extension):
            return None

        def put(self, key, extension, data):
            raise AssertionError("failed requests must not store audio")

    async def run():
        synth = FakeSynth(fail=VoiceFailure.QUOTA)
        svc = VoiceoverService(synth, Store(), PROFILE, Budget())
        first = asyncio.create_task(svc.voiceover_url("First offer", VOICE))
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            assert await svc.voiceover_url("Other offer", VOICE) is None
            assert svc.stats.disabled_by is VoiceFailure.QUOTA
            release.set()
            assert await first is None
            assert len(synth.requests) == 1
            assert synth.requests[0].text == "Other offer"
        finally:
            release.set()
            await first

    anyio.run(run)
