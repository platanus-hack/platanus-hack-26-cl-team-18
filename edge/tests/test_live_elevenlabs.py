"""LIVE round trip against the real ElevenLabs API (Creator tier). Run on purpose:

    cd edge && ELEVENLABS_API_KEY=... uv run pytest -m live -s

TTS speaks a Chilean intent line as 16 kHz PCM, then that audio streams through
our own VAD gate, Scribe Realtime client and speech pipeline. It proves the
whole transcription path end to end, and prints the measured latencies.
Costs a few hundred characters and a few seconds of realtime STT.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

from agent.contract import AudienceUtterance
from agent.speech.keyterms import DEFAULT_KEYTERMS, normalize
from agent.speech.pipeline import SpeechPipeline
from agent.speech.scribe import CommittedTranscript, PartialTranscript, ScribeConfig, ScribeRealtime
from agent.speech.vad import FRAME_BYTES, EnergyVad, SpeechGate

pytestmark = [pytest.mark.live, pytest.mark.anyio]

# Short on purpose: the account has ~10k credits. The synthesized audio is cached
# in tests/fixtures, so only the first run spends TTS characters (~34).
LINE = "Uf, qué hambre. Vamos por un café."
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "live_line_pcm16k.raw"
# Premade voice reachable with a TTS-only key (no voices_read permission needed).
PREMADE_VOICE = "JBFqnCBsd6RMkjVDRZzb"


def _env(name: str) -> str | None:
    if value := os.environ.get(name):
        return value
    for env_file in (
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ):
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                key, _, val = line.partition("=")
                if key.strip() == name and val.strip():
                    return val.strip().strip('"')
    return None


API_KEY = _env("ELEVENLABS_API_KEY")
if not API_KEY:
    pytest.skip("ELEVENLABS_API_KEY not set", allow_module_level=True)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request(path: str, *, body: dict[str, object] | None = None) -> bytes:
    req = urllib.request.Request(
        f"https://api.elevenlabs.io{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"xi-api-key": API_KEY or "", "content-type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # fixed https host
        return resp.read()


def _voice_id() -> str:
    return _env("ELEVENLABS_NARRATOR_VOICE_ID") or PREMADE_VOICE


def _spoken_pcm16k() -> bytes:
    if FIXTURE.exists():
        return FIXTURE.read_bytes()  # free: no TTS characters spent
    pcm = _request(
        f"/v1/text-to-speech/{_voice_id()}?output_format=pcm_16000",
        body={"text": LINE, "model_id": "eleven_flash_v2_5", "language_code": "es"},
    )
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_bytes(pcm)
    return pcm


async def test_tts_audio_round_trips_through_our_transcription_pipeline() -> None:
    pcm = _spoken_pcm16k()
    assert len(pcm) > 16_000 * 2, "expected over a second of 16 kHz audio"

    sent: list[AudienceUtterance] = []
    stamps: dict[str, float] = {}
    pipeline: SpeechPipeline | None = None

    def on_event(event: object) -> None:
        if isinstance(event, PartialTranscript) and event.text:
            stamps.setdefault("first_partial", time.perf_counter())
        if isinstance(event, CommittedTranscript):
            stamps["committed"] = time.perf_counter()
        if pipeline is not None:
            pipeline.on_event(event)  # type: ignore[arg-type]

    scribe = ScribeRealtime(
        ScribeConfig(api_key=API_KEY or "", keyterms=DEFAULT_KEYTERMS.prompt_terms()),
        on_event,
        idle_close_s=None,
    )
    pipeline = SpeechPipeline(
        screen_id="demo-01",
        gate=SpeechGate(EnergyVad(), hangover_frames=20),
        transcriber=scribe,
        keyterms=DEFAULT_KEYTERMS,
        emit=sent.append,
    )
    task = asyncio.create_task(scribe.run())
    try:
        await scribe.wait_connected(10)
        frames = [
            pcm[i : i + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")
            for i in range(0, len(pcm), FRAME_BYTES)
        ]
        frames += [bytes(FRAME_BYTES)] * 40  # 1.3 s of silence: the gate closes and commits
        for frame in frames:  # real time, like the mic
            pipeline.on_frame(frame)
            if scribe.stats.commits and "commit_sent" not in stamps:
                stamps["commit_sent"] = time.perf_counter()  # our VAD closed the segment
            await asyncio.sleep(0.032)
        async with asyncio.timeout(15):
            while "committed" not in stamps:
                await asyncio.sleep(0.05)
    finally:
        task.cancel()

    hits = {k for u in sent for k in u.keyterms_hit}
    assert {"hambre", "café"} <= hits, f"keyterms heard: {hits}"
    assert sent[0].keyterms_hit, "the first utterance should be the early keyterm signal"
    print(
        f"\nscribe: commit -> committed transcript {1000 * (stamps['committed'] - stamps['commit_sent']):.0f} ms; "
        f"utterances {len(sent)}, keyterms {sorted(hits)}, sessions {scribe.stats.sessions}"
    )


# The exact line the demo airs for the Voltio + Café Patio duo, as produced by
# app/modules/creative/application/speech_text.py (prices/symbols spelled out).
OFFER_LINE = (
    "Dúo post-entreno: Voltio y Café Patio para dos por cinco mil novecientos noventa pesos"
)
OFFER_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "offer_line_pcm16k.raw"


async def test_the_aired_offer_line_is_pronounced_as_written() -> None:
    """Speech generation quality: TTS the line we air, transcribe it back, compare."""
    if OFFER_FIXTURE.exists():
        pcm = OFFER_FIXTURE.read_bytes()  # free on reruns
    else:
        pcm = _request(
            f"/v1/text-to-speech/{_voice_id()}?output_format=pcm_16000",
            body={"text": OFFER_LINE, "model_id": "eleven_flash_v2_5", "language_code": "es"},
        )
        OFFER_FIXTURE.write_bytes(pcm)

    finals: list[str] = []
    scribe = ScribeRealtime(
        ScribeConfig(api_key=API_KEY or "", keyterms=("Voltio", "Café Patio", "post-entreno")),
        lambda e: finals.append(e.text) if isinstance(e, CommittedTranscript) else None,
        idle_close_s=None,
    )
    task = asyncio.create_task(scribe.run())
    try:
        await scribe.wait_connected(10)
        for i in range(0, len(pcm), FRAME_BYTES * 3):
            scribe.send_audio(pcm[i : i + FRAME_BYTES * 3])
            await asyncio.sleep(0.096)
        scribe.commit()
        async with asyncio.timeout(15):
            while not finals:
                await asyncio.sleep(0.05)
    finally:
        task.cancel()

    heard = normalize(" ".join(finals))
    assert "voltio" in heard and "patio" in heard, heard
    assert "pesos" in heard, heard
    # Scribe may write the price as digits or words; either proves it was spoken as a price.
    assert any(form in heard.replace(" ", "") for form in ("5990", "cincomilnovecientosnoventa")), (
        heard
    )
    print(f"\naired line transcribed back as: {' '.join(finals)!r}")


async def test_live_transcription_honors_an_explicit_correction():
    """A real STT segment may contain an old want and its spoken replacement."""
    line = "Quiero una cerveza, mejor un café."
    fixture = FIXTURE.with_name("correction_line_pcm16k.raw")
    if fixture.exists():
        pcm = fixture.read_bytes()
    else:
        pcm = _request(
            f"/v1/text-to-speech/{_voice_id()}?output_format=pcm_16000",
            body={"text": line, "model_id": "eleven_flash_v2_5", "language_code": "es"},
        )
        fixture.write_bytes(pcm)  # authored synthetic speech, never microphone audio
    sent: list[AudienceUtterance] = []
    pipeline: SpeechPipeline | None = None
    scribe = ScribeRealtime(
        ScribeConfig(api_key=API_KEY or "", keyterms=DEFAULT_KEYTERMS.prompt_terms()),
        lambda event: pipeline.on_event(event) if pipeline else None,
        idle_close_s=None,
    )
    pipeline = SpeechPipeline(
        screen_id="demo-01",
        gate=SpeechGate(EnergyVad()),
        transcriber=scribe,
        keyterms=DEFAULT_KEYTERMS,
        emit=sent.append,
    )
    task = asyncio.create_task(scribe.run())
    try:
        await scribe.wait_connected(10)
        for offset in range(0, len(pcm), FRAME_BYTES):
            pipeline.on_frame(pcm[offset : offset + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
            await asyncio.sleep(0.032)
        for _ in range(40):
            pipeline.on_frame(bytes(FRAME_BYTES))
            await asyncio.sleep(0.032)
        pipeline.finish()
        async with asyncio.timeout(10):
            while not any(message.is_final for message in sent):
                await asyncio.sleep(0.02)
        final = [message for message in sent if message.is_final][-1]
        assert final.keyterms_hit == ["café"]
        print(f"\nlive correction: final coffee-only intent; {scribe.stats.sessions} session")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
