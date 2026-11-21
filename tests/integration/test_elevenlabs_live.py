"""LIVE ElevenLabs TTS through our adapter and voice-over service. Run on purpose:

    ELEVENLABS_API_KEY=... uv run pytest -m live -s tests/integration/test_elevenlabs_live.py

Proves the real API accepts exactly what the adapter sends (Creator tier,
Flash v2.5, mp3_44100_128, Spanish), that errors translate, and prints latency.
"""

from __future__ import annotations

import time
from pathlib import Path

import anyio
import pytest

from app.config.settings import AppSettings
from app.modules.creative.application.ports.voice import (
    VoiceFailure,
    VoiceRequest,
    VoiceSynthesisError,
)
from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from app.modules.creative.infrastructure.media_store import FileMediaStore

pytestmark = pytest.mark.live

SETTINGS = AppSettings()
KEY = SETTINGS.elevenlabs_api_key.get_secret_value() if SETTINGS.elevenlabs_api_key else ""
if not KEY:
    pytest.skip("ELEVENLABS_API_KEY not set", allow_module_level=True)


# Premade voice reachable with a TTS-only key (no voices_read permission needed).
PREMADE_VOICE = "JBFqnCBsd6RMkjVDRZzb"


def voice_id() -> str:
    return SETTINGS.elevenlabs_narrator_voice_id or PREMADE_VOICE


PROFILE = VoiceProfile(
    model_id=SETTINGS.elevenlabs_tts_model,
    language_code=SETTINGS.elevenlabs_language,
    output_format=SETTINGS.elevenlabs_output_format,
)


# Persistent (gitignored) cache: only the first run ever spends characters.
LIVE_CACHE = Path(__file__).resolve().parents[2] / "var" / "live-cache"


def test_real_voiceover_is_synthesized_cached_and_reused() -> None:
    async def run() -> tuple[str | None, str | None, float, VoiceoverService]:
        synth = ElevenLabsVoiceSynthesizer(KEY)
        service = VoiceoverService(synth, FileMediaStore(LIVE_CACHE), PROFILE)
        # Short on purpose (~35 chars): the account has ~10k credits.
        line = "Voltio y Café Patio, a dos minutos."
        started = time.perf_counter()
        first = await service.voiceover_url(line, voice_id())
        elapsed = time.perf_counter() - started
        second = await service.voiceover_url(line, voice_id())
        await synth.aclose()
        return first, second, elapsed, service

    first, second, elapsed, service = anyio.run(run)
    assert first is not None and first == second
    assert service.stats.synthesized <= 1  # 0 on every rerun: the cache answers
    assert service.stats.synthesized + service.stats.cache_hits == 2
    data = (LIVE_CACHE / "voiceovers" / first.rsplit("/", 1)[1]).read_bytes()
    assert data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")  # MP3
    print(
        f"\nTTS {PROFILE.model_id}: {elapsed * 1000:.0f} ms, {len(data)} bytes, "
        f"characters spent this run: {service.stats.characters}"
    )


def test_real_api_errors_translate() -> None:
    async def run(key: str, voice: str) -> VoiceFailure:
        synth = ElevenLabsVoiceSynthesizer(key, retry_delays=())
        try:
            await synth.synthesize(
                VoiceRequest(
                    text="hola",
                    voice_id=voice,
                    model_id=PROFILE.model_id,
                    language_code="es",
                    output_format=PROFILE.output_format,
                )
            )
        except VoiceSynthesisError as exc:
            return exc.failure
        finally:
            await synth.aclose()
        raise AssertionError("expected an error")

    assert anyio.run(run, "sk_definitely_not_a_key", voice_id()) is VoiceFailure.AUTH
    assert anyio.run(run, KEY, "AAAAAAAAAAAAAAAAAAAA") is VoiceFailure.INVALID_REQUEST
