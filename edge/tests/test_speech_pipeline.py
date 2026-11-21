from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from agent.contract import AGENT_INBOUND, AudienceUtterance
from agent.speech.echo_guard import AudioWindow, EchoGuard
from agent.speech.keyterms import DEFAULT_KEYTERMS
from agent.speech.pipeline import SpeechPipeline
from agent.speech.scribe import (
    CommittedTranscript,
    ErrorClass,
    PartialTranscript,
    ScribeConfig,
    ScribeError,
    ScribeRealtime,
)
from agent.speech.vad import FRAME_BYTES, FRAME_SAMPLES, EnergyVad, SpeechGate
from tests.fake_scribe import FakeScribe, wait_for

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
SILENCE = bytes(FRAME_BYTES)
VOICE = (np.sin(np.arange(FRAME_SAMPLES) / 3) * 9000).astype(np.int16).tobytes()


class RecordingTranscriber:
    def __init__(self) -> None:
        self.audio = 0
        self.commits = 0

    def send_audio(self, pcm: bytes) -> None:
        self.audio += len(pcm)

    def commit(self) -> None:
        self.commits += 1


def make(echo: EchoGuard | None = None):  # type: ignore[no-untyped-def]
    sent: list[AudienceUtterance] = []
    transcriber = RecordingTranscriber()
    pipeline = SpeechPipeline(
        screen_id="demo-01",
        gate=SpeechGate(EnergyVad(), start_frames=2, hangover_frames=3, preroll_frames=2),
        transcriber=transcriber,
        keyterms=DEFAULT_KEYTERMS,
        emit=sent.append,
        echo_guard=echo,
        clock=lambda: T0,
    )
    return pipeline, transcriber, sent


def test_only_speech_reaches_the_transcriber_and_each_segment_commits_once() -> None:
    pipeline, transcriber, _ = make()
    for frame in [SILENCE] * 10 + [VOICE] * 6 + [SILENCE] * 10:
        pipeline.on_frame(frame)
    assert transcriber.commits == 1
    # pre-roll (2) + 4 more voiced + 3 hangover frames; the leading silence is not streamed
    assert transcriber.audio == 9 * FRAME_BYTES
    assert pipeline.stats.segments == 1


def test_keyterm_on_a_partial_emits_early_then_commit_emits_final() -> None:
    pipeline, _, sent = make()
    pipeline.on_event(PartialTranscript("uf qué"))
    assert sent == []
    pipeline.on_event(PartialTranscript("uf qué hambre"))
    pipeline.on_event(PartialTranscript("uf qué hambre vamos"))  # same hit: no repeat
    pipeline.on_event(CommittedTranscript("Uf, qué hambre. Vamos por un café.", "es"))
    assert [u.keyterms_hit for u in sent] == [["hambre"], ["hambre", "café"]]
    assert [u.seq for u in sent] == [1, 2]
    for u in sent:  # every message is valid on the wire
        assert AGENT_INBOUND.validate_json(u.model_dump_json()) == u


def test_committed_without_keyterms_is_still_sent_for_topic_extraction() -> None:
    pipeline, _, sent = make()
    pipeline.on_event(CommittedTranscript("¿dónde queda el metro?", "es"))
    assert len(sent) == 1 and sent[0].keyterms_hit == []


@pytest.mark.parametrize("phrase", ["quiero un café", "Quiero un cafe.", "un cafecito", "un CAFÉ"])
def test_coffee_request_emits_a_canonical_intent_before_the_final_transcript(phrase: str) -> None:
    pipeline, _, sent = make()
    pipeline.on_event(PartialTranscript(phrase))
    assert len(sent) == 1 and sent[0].keyterms_hit == ["café"]
    assert sent[0].screen_id == "demo-01"
    pipeline.on_event(PartialTranscript(phrase))
    assert len(sent) == 1
    pipeline.on_event(CommittedTranscript(phrase, "es"))
    assert sent[-1].keyterms_hit == ["café"]


def test_empty_commit_and_our_own_voiceover_are_dropped() -> None:
    guard = EchoGuard()
    guard.announce(
        AudioWindow(T0 - timedelta(seconds=1), T0 + timedelta(seconds=2), "Voltio y Café Patio")
    )
    pipeline, _, sent = make(echo=guard)
    pipeline.on_event(CommittedTranscript("   "))
    pipeline.on_event(PartialTranscript("voltio y café"))
    pipeline.on_event(CommittedTranscript("Voltio y Café Patio"))
    assert sent == []
    assert pipeline.stats.empty_commits == 1 and pipeline.stats.echoes_dropped == 2


def test_fatal_provider_error_marks_speech_degraded() -> None:
    pipeline, _, _ = make()
    pipeline.on_event(ScribeError("quota_exceeded", "out of hours", ErrorClass.FATAL))
    assert pipeline.stats.degraded


def test_utterance_text_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    pipeline, _, _ = make()
    secret = "mi número es nueve ocho siete"
    pipeline.on_event(PartialTranscript(f"{secret} qué hambre"))
    pipeline.on_event(CommittedTranscript(f"{secret} qué hambre"))
    assert caplog.records, "the pipeline should log counts"
    assert all(secret not in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_end_to_end_audio_to_utterance_over_the_scribe_protocol() -> None:
    """Mic frames -> gate -> real ScribeRealtime -> fake Scribe server -> utterances."""
    fake = FakeScribe(transcripts=["uf qué hambre, vamos al gimnasio"])
    sent: list[AudienceUtterance] = []
    pipeline: SpeechPipeline | None = None
    client = ScribeRealtime(
        ScribeConfig(
            api_key="test-key", url=await fake.start(), keyterms=DEFAULT_KEYTERMS.prompt_terms()
        ),
        lambda e: pipeline.on_event(e) if pipeline else None,
        idle_close_s=None,
    )
    pipeline = SpeechPipeline(
        screen_id="demo-01",
        gate=SpeechGate(EnergyVad(), start_frames=2, hangover_frames=3, preroll_frames=2),
        transcriber=client,
        keyterms=DEFAULT_KEYTERMS,
        emit=sent.append,
    )
    task = asyncio.create_task(client.run())
    try:
        await client.wait_connected(2)
        for frame in [SILENCE] * 5 + [VOICE] * 12 + [SILENCE] * 6:
            pipeline.on_frame(frame)
        await wait_for(lambda: len(sent) >= 2)
        assert sent[0].keyterms_hit == ["hambre"]  # early, from the partial
        assert sent[-1].keyterms_hit == ["hambre", "gym"]  # final, from the commit
        assert fake.chunks[-1]["commit"] is True
    finally:
        task.cancel()
        await fake.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
