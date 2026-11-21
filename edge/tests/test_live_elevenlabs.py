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
from agent.speech.keyterms import DEFAULT_KEYTERMS
from agent.speech.pipeline import SpeechPipeline
from agent.speech.scribe import CommittedTranscript, PartialTranscript, ScribeConfig, ScribeRealtime
from agent.speech.vad import FRAME_BYTES, EnergyVad, SpeechGate

pytestmark = [pytest.mark.live, pytest.mark.anyio]

LINE = "Uf, qué hambre. Vamos por un café después del gimnasio."


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
    if configured := _env("ELEVENLABS_NARRATOR_VOICE_ID"):
        return configured
    voices = json.loads(
        _request("/v2/voices?category=premade&page_size=10&include_total_count=false")
    )
    return str(voices["voices"][0]["voice_id"])


def _spoken_pcm16k() -> bytes:
    return _request(
        f"/v1/text-to-speech/{_voice_id()}?output_format=pcm_16000",
        body={"text": LINE, "model_id": "eleven_flash_v2_5", "language_code": "es"},
    )


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
        ScribeConfig(api_key=API_KEY or "", keyterms=DEFAULT_KEYTERMS.prompt_terms()), on_event
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
            await asyncio.sleep(0.032)
        stamps["speech_end"] = time.perf_counter()
        async with asyncio.timeout(15):
            while "committed" not in stamps:
                await asyncio.sleep(0.05)
    finally:
        task.cancel()

    hits = {k for u in sent for k in u.keyterms_hit}
    assert {"hambre", "café"} <= hits, f"keyterms heard: {hits}"
    assert sent[0].keyterms_hit, "the first utterance should be the early keyterm signal"
    print(
        f"\nscribe: speech_end -> committed {1000 * (stamps['committed'] - stamps['speech_end']):.0f} ms; "
        f"utterances {len(sent)}, keyterms {sorted(hits)}, sessions {scribe.stats.sessions}"
    )
