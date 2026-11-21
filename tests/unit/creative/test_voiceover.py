from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from app.modules.creative.application.ports.voice import (
    SynthesizedVoice,
    VoiceFailure,
    VoiceRequest,
    VoiceSynthesisError,
)
from app.modules.creative.application.voiceover import (
    InvalidSpokenLine,
    VoiceoverService,
    VoiceProfile,
    cache_key,
    spoken_line,
)
from app.modules.creative.infrastructure.media_store import FileMediaStore

PROFILE = VoiceProfile(
    model_id="eleven_flash_v2_5", language_code="es", output_format="mp3_44100_128"
)
VOICE = "JBFqnCBsd6RMkjVDRZzb"


class FakeSynth:
    def __init__(self, *, fail: VoiceFailure | None = None) -> None:
        self.requests: list[VoiceRequest] = []
        self.fail = fail

    async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
        self.requests.append(request)
        if self.fail is not None:
            raise VoiceSynthesisError(self.fail, "injected")
        return SynthesizedVoice(
            audio=b"ID3mp3",
            content_type="audio/mpeg",
            model_id=request.model_id,
            voice_id=request.voice_id,
            character_cost=len(request.text),
        )


def service(tmp_path: Path, synth: FakeSynth) -> VoiceoverService:
    return VoiceoverService(synth, FileMediaStore(tmp_path), PROFILE)


def test_spoken_line_normalizes_brand_separators_and_rejects_long_copy() -> None:
    assert spoken_line("Voltio · Energía para tu próximo entrenamiento.") == (
        "Voltio. Energía para tu próximo entrenamiento."
    )
    with pytest.raises(InvalidSpokenLine):
        spoken_line("   ")
    with pytest.raises(InvalidSpokenLine):
        spoken_line("palabra " * 25)


def test_cache_key_depends_on_line_voice_and_profile() -> None:
    k = cache_key("hola", VOICE, PROFILE)
    assert k == cache_key("hola", VOICE, PROFILE)
    assert k != cache_key("hola!", VOICE, PROFILE)
    assert k != cache_key("hola", "OtherVoiceId1234", PROFILE)
    assert k != cache_key("hola", VOICE, VoiceProfile("eleven_v3", "es", "mp3_44100_128"))


def test_miss_synthesizes_once_then_hits_the_cache(tmp_path: Path) -> None:
    synth = FakeSynth()
    svc = service(tmp_path, synth)
    copy = "Combo post-entreno · Voltio + Café Patio por $3.490"
    assert svc.cached_url(copy, VOICE) is None
    url = anyio.run(svc.voiceover_url, copy, VOICE)
    assert url is not None and url.startswith("/media/voiceovers/") and url.endswith(".mp3")
    assert anyio.run(svc.voiceover_url, copy, VOICE) == url
    assert svc.cached_url(copy, VOICE) == url
    assert len(synth.requests) == 1
    assert svc.stats.synthesized == 1 and svc.stats.cache_hits == 1
    assert synth.requests[0].text == "Combo post-entreno. Voltio + Café Patio por $3.490"
    assert (tmp_path / "voiceovers" / url.rsplit("/", 1)[1]).read_bytes() == b"ID3mp3"


def test_quota_or_auth_failure_disables_voice_for_the_session(tmp_path: Path) -> None:
    synth = FakeSynth(fail=VoiceFailure.QUOTA)
    svc = service(tmp_path, synth)
    assert anyio.run(svc.voiceover_url, "Hola.", VOICE) is None
    assert anyio.run(svc.voiceover_url, "Chao.", VOICE) is None
    assert len(synth.requests) == 1  # stopped spending calls
    assert svc.stats.disabled_by is VoiceFailure.QUOTA


def test_transient_failure_means_no_voice_this_time_only(tmp_path: Path) -> None:
    synth = FakeSynth(fail=VoiceFailure.UNAVAILABLE)
    svc = service(tmp_path, synth)
    assert anyio.run(svc.voiceover_url, "Hola.", VOICE) is None
    synth.fail = None
    assert anyio.run(svc.voiceover_url, "Hola.", VOICE) is not None


def test_media_store_is_atomic_and_rejects_traversal(tmp_path: Path) -> None:
    store = FileMediaStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("../../etc/passwd", "mp3", b"x")
    with pytest.raises(ValueError):
        store.put("a" * 32, "mp3", b"")
    store.put("a" * 32, "mp3", b"data")
    assert [p.name for p in (tmp_path / "voiceovers").iterdir()] == ["a" * 32 + ".mp3"]


class SlowSynth(FakeSynth):
    """Holds every synthesis open until released, like a real provider round trip."""

    def __init__(self) -> None:
        super().__init__()
        self.release = anyio.Event()

    async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
        await self.release.wait()
        return await super().synthesize(request)


def test_concurrent_requests_for_one_line_share_a_single_synthesis(tmp_path: Path) -> None:
    """Startup prewarm and a live moment asking for the same offer line must pay once.

    Found by the pipeline eval (evals/pipeline.py): on a cold start the same
    line was synthesized twice, spending characters twice.
    """
    synth = SlowSynth()
    voiceovers = service(tmp_path, synth)
    urls: list[str | None] = []

    async def run() -> None:
        async def ask() -> None:
            urls.append(await voiceovers.voiceover_url("Dúo: Voltio y Café Patio", VOICE))

        async with anyio.create_task_group() as group:
            group.start_soon(ask)
            group.start_soon(ask)
            await anyio.sleep(0.01)
            synth.release.set()

    anyio.run(run)
    assert len(synth.requests) == 1
    assert urls[0] is not None and urls == [urls[0], urls[0]]
    assert voiceovers.stats.synthesized == 1
    assert voiceovers.stats.coalesced == 1


def test_shared_voice_subscribers_do_not_occupy_worker_slots_for_other_lines(
    tmp_path: Path,
) -> None:
    import asyncio

    from app.realtime.warm_worker import WarmWorker

    async def run() -> None:
        worker = WarmWorker({"elevenlabs": 2})
        started, release, fresh_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Synth(FakeSynth):
            async def synthesize(self, request: VoiceRequest) -> SynthesizedVoice:
                if request.text == "Shared offer":
                    started.set()
                    await release.wait()
                else:
                    fresh_started.set()
                return await super().synthesize(request)

        synth = Synth()
        svc = service(tmp_path, synth)
        delivered = []

        async def request(screen: str, line: str) -> None:
            if svc.subscribe_pending(line, VOICE, consumer=screen, on_ready=delivered.append):
                return
            delivered.append(await svc.voiceover_url(line, VOICE))

        worker.submit("elevenlabs", "first", lambda: request("one", "Shared offer"))
        async with asyncio.timeout(1):
            await started.wait()
        worker.submit("elevenlabs", "second", lambda: request("two", "Shared offer"))
        worker.submit("elevenlabs", "different", lambda: request("three", "Different offer"))
        try:
            async with asyncio.timeout(0.1):
                await fresh_started.wait()
            assert not release.is_set()
        finally:
            release.set()
            await worker.drain()
        assert len(synth.requests) == 2
        assert len(delivered) == 3 and all(delivered)

    anyio.run(run)


def test_voice_subscriptions_replace_the_same_consumer_and_isolate_callback_failure(
    tmp_path: Path,
) -> None:
    import asyncio

    async def run() -> None:
        synth = SlowSynth()
        svc = service(tmp_path, synth)
        owner = asyncio.create_task(svc.voiceover_url("Shared offer", VOICE))
        await asyncio.sleep(0)
        delivered = []

        def broken(_url):
            raise RuntimeError("subscriber failed")

        assert svc.subscribe_pending("Shared offer", VOICE, consumer="one", on_ready=broken)
        assert svc.subscribe_pending(
            "Shared offer", VOICE, consumer="one", on_ready=delivered.append
        )
        assert svc.subscribe_pending("Shared offer", VOICE, consumer="broken", on_ready=broken)
        assert svc.subscribe_pending(
            "Shared offer", VOICE, consumer="two", on_ready=delivered.append
        )
        synth.release.set()
        url = await owner
        assert delivered == [url, url]
        assert len(synth.requests) == 1
        assert not svc.subscribe_pending("Shared offer", VOICE, consumer="one", on_ready=broken)

    anyio.run(run)


def test_provider_quota_failure_notifies_every_voice_subscriber_without_retry(
    tmp_path: Path,
) -> None:
    import asyncio

    async def run() -> None:
        synth = SlowSynth()
        synth.fail = VoiceFailure.QUOTA
        svc = service(tmp_path, synth)
        owner = asyncio.create_task(svc.voiceover_url("Shared offer", VOICE))
        await asyncio.sleep(0)
        delivered = []
        assert svc.subscribe_pending(
            "Shared offer", VOICE, consumer="one", on_ready=delivered.append
        )
        assert svc.subscribe_pending(
            "Shared offer", VOICE, consumer="two", on_ready=delivered.append
        )
        synth.release.set()
        assert await owner is None
        assert delivered == [None, None]
        assert svc.stats.disabled_by is VoiceFailure.QUOTA
        assert len(synth.requests) == 1

    anyio.run(run)
