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
    StreamReset,
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


def make(echo: EchoGuard | None = None, *, stability: int = 1):  # type: ignore[no-untyped-def]
    """Meaning/retraction tests act on every partial (`stability=1`) on purpose;
    stabilization itself is covered by the `test_stabilized_*` tests below."""
    sent: list[AudienceUtterance] = []
    transcriber = RecordingTranscriber()
    pipeline = SpeechPipeline(
        screen_id="demo-01",
        gate=SpeechGate(EnergyVad(), start_frames=2, hangover_frames=3, preroll_frames=2),
        transcriber=transcriber,
        keyterms=DEFAULT_KEYTERMS,
        emit=sent.append,
        echo_guard=echo,
        partial_stability=stability,
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


def test_partial_updates_carry_the_complete_current_request_not_just_new_words() -> None:
    pipeline, _, sent = make()
    pipeline.on_event(PartialTranscript("un café"))
    pipeline.on_event(PartialTranscript("un café después del gimnasio"))
    assert [u.keyterms_hit for u in sent] == [["café"], ["café", "gym"]]
    pipeline.on_event(CommittedTranscript("un café después del gimnasio", "es"))
    pipeline.on_event(PartialTranscript("una cerveza"))
    assert sent[-1].keyterms_hit == ["cerveza"]


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
        await wait_for(lambda: any(m.is_final for m in sent))
        # Early, from the stabilized partial (the one-off prefix partial is not acted on).
        assert not sent[0].is_final and sent[0].keyterms_hit == ["hambre", "gym"]
        assert sent[-1].keyterms_hit == ["hambre", "gym"]  # final, from the commit
        assert sent[-1].is_final
        assert fake.chunks[-1]["commit"] is True
    finally:
        task.cancel()
        await fake.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_partial_correction_retracts_beer_and_keeps_one_anonymous_request_id():
    pipeline, _, sent = make()
    pipeline.on_event(PartialTranscript("quiero cerveza"))
    pipeline.on_event(PartialTranscript("no quiero cerveza"))
    pipeline.on_event(CommittedTranscript("no quiero cerveza, quiero café"))
    assert [m.keyterms_hit for m in sent] == [["cerveza"], [], ["café"]]
    assert [m.is_final for m in sent] == [False, False, True]
    assert len({m.utterance_id for m in sent}) == 1
    first = sent[0].utterance_id
    pipeline.on_event(CommittedTranscript("quiero cerveza"))
    assert sent[-1].utterance_id != first


def test_empty_commit_retracts_a_provisional_partial():
    pipeline, _, sent = make()
    pipeline.on_event(PartialTranscript("quiero cerveza"))
    pipeline.on_event(CommittedTranscript(""))
    assert len(sent) == 2
    assert sent[-1].is_final and sent[-1].keyterms_hit == []
    assert sent[-1].utterance_id == sent[0].utterance_id


def test_incomplete_phrase_survives_a_pause_then_commits_when_complete():
    pipeline, transcriber, _ = make()
    for frame in [VOICE] * 6:
        pipeline.on_frame(frame)
    pipeline.on_event(PartialTranscript("no quiero"))
    for frame in [SILENCE] * 12:  # 384 ms exceeds the normal hangover
        pipeline.on_frame(frame)
    assert transcriber.commits == 0
    for frame in [VOICE] * 4:
        pipeline.on_frame(frame)
    pipeline.on_event(PartialTranscript("no quiero cerveza"))
    for frame in [SILENCE] * 3:
        pipeline.on_frame(frame)
    assert transcriber.commits == 1


def test_stream_reset_retracts_partial_and_ignores_the_damaged_tail():
    from agent.speech.scribe import StreamReset

    pipeline, transcriber, sent = make()
    for frame in [VOICE] * 6:
        pipeline.on_frame(frame)
    pipeline.on_event(PartialTranscript("quiero cerveza"))
    pipeline.on_event(StreamReset("capture_gap"))
    assert sent[-1].text == "" and sent[-1].is_final
    audio = transcriber.audio
    for frame in [VOICE] * 10:
        pipeline.on_frame(frame)
    assert transcriber.audio == audio  # don't interpret the remaining half-word
    for frame in [SILENCE] * 3 + [VOICE] * 4 + [SILENCE] * 3:
        pipeline.on_frame(frame)
    assert transcriber.commits == 1


def test_finite_source_without_silence_still_commits_once():
    pipeline, transcriber, _ = make()
    for frame in [VOICE] * 6:
        pipeline.on_frame(frame)
    pipeline.finish()
    pipeline.finish()
    assert transcriber.commits == 1


def test_real_rehearsal_correction_overrides_old_interest_in_one_segment():
    pipeline, _, sent = make()
    pipeline.on_event(PartialTranscript("quiero una cerveza"))
    pipeline.on_event(PartialTranscript("quiero una cerveza mejor"))
    pipeline.on_event(PartialTranscript("quiero una cerveza mejor un café"))
    pipeline.on_event(CommittedTranscript("Quiero una cerveza, mejor un café."))
    assert [message.keyterms_hit for message in sent] == [["cerveza"], [], ["café"], ["café"]]
    assert len({message.utterance_id for message in sent}) == 1


# ------------------------------------------------------------ partial stabilization

# Recorded from `agent.speech.robustness --live` (crowd babble at 10 dB SNR, keyterm
# biasing on). Audio is synthetic: reversed TTS babble over a TTS line. Onset is
# the 4th partial; everything before it is the recognizer hallucinating our bias words.
BABBLE_TRACE = [
    "Shop",
    "Sopaip",
    "Sopaipilla",
    "Uf",
    "¡Uf, qué hambre!",
    "¡Uf, qué hambre!",
    "¡Uf, qué hambre!",
    "¡Uf, qué hambre! Vamos",
    "¡Uf, qué hambre! Vamos por un cafecito.",
    "¡Uf, qué hambre! Vamos por un café después de-",
    "¡Uf, qué hambre! Vamos por un café después del gimnasio.",
    "¡Uf, qué hambre! Vamos por un café después del gimnasio.",
]


def test_stabilized_babble_hallucinations_never_reach_the_auction() -> None:
    pipeline, _, sent = make(stability=2)
    for text in BABBLE_TRACE:
        pipeline.on_event(PartialTranscript(text))
    pipeline.on_event(CommittedTranscript(BABBLE_TRACE[-1], "es"))
    heard = {term for message in sent for term in message.keyterms_hit}
    assert heard == {"hambre", "café", "gym"}  # no "cerveza" (Shop), no "comida"
    assert not sent[0].is_final and sent[0].keyterms_hit == ["hambre"]  # still early
    assert sent[-1].is_final and sent[-1].keyterms_hit == ["hambre", "café", "gym"]
    assert pipeline.stats.unstable_partials >= 3


def test_stabilized_partial_emits_once_when_two_partials_agree() -> None:
    pipeline, _, sent = make(stability=2)
    pipeline.on_event(PartialTranscript("quiero un café"))
    assert sent == []
    pipeline.on_event(PartialTranscript("quiero un café."))  # same meaning, new punctuation
    assert [m.keyterms_hit for m in sent] == [["café"]]
    pipeline.on_event(PartialTranscript("quiero un café"))
    assert len(sent) == 1


def test_stabilized_flicker_between_meanings_emits_nothing() -> None:
    pipeline, _, sent = make(stability=2)
    for text in ["shop", "ahí lo", "sopaipilla", "aló", "shop"]:
        pipeline.on_event(PartialTranscript(text))
    assert sent == []


def test_stabilized_commit_is_authoritative_and_never_waits() -> None:
    pipeline, _, sent = make(stability=2)
    pipeline.on_event(PartialTranscript("una chela"))
    pipeline.on_event(CommittedTranscript("una chela", "es"))
    assert [(m.keyterms_hit, m.is_final) for m in sent] == [(["cerveza"], True)]


def test_stabilized_candidate_does_not_survive_a_commit_or_reset() -> None:
    pipeline, _, sent = make(stability=2)
    pipeline.on_event(PartialTranscript("una chela"))
    pipeline.on_event(CommittedTranscript("", "es"))  # turn ended without words
    pipeline.on_event(PartialTranscript("una chela"))  # a new turn: one sighting only
    assert sent == []
    pipeline.on_event(StreamReset("capture_gap"))
    pipeline.on_event(PartialTranscript("una chela"))
    assert sent == []


def test_stabilized_retraction_also_needs_agreement() -> None:
    pipeline, _, sent = make(stability=2)
    pipeline.on_event(PartialTranscript("quiero cerveza"))
    pipeline.on_event(PartialTranscript("quiero cerveza"))
    pipeline.on_event(PartialTranscript("quiero"))  # one revision: not yet a retraction
    assert [m.keyterms_hit for m in sent] == [["cerveza"]]
    pipeline.on_event(PartialTranscript("quiero"))
    assert [m.keyterms_hit for m in sent] == [["cerveza"], []]


def test_partial_stability_must_be_positive() -> None:
    with pytest.raises(ValueError):
        make(stability=0)
