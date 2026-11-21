"""Speech mode: mic frames -> VAD gate -> Scribe -> keyterms -> `audience.utterance`.

Privacy (ADR 0005, binding):
- Speech mode is opt-in per screen; the caller shows a listening indicator.
- Text lives in memory just long enough to build one utterance message. It is
  never logged or persisted here (tests assert it).
- Our own voice-overs are filtered by the echo guard before they count.

Latency: a keyterm on a *partial* transcript emits an utterance early (the
"it heard me" signal); the committed transcript follows with all hits.

Partial stabilization: realtime partials are revised freely, and keyterm
biasing makes the recognizer hallucinate exactly our bias words in crowd
noise ("Shop", "Sopaipilla" from babble, measured by
`agent.speech.robustness --live`). Real words persist across partials;
hallucinations flicker. So a partial's meaning is acted on only once
`partial_stability` consecutive partials agree (AWS Transcribe calls this
partial-results stabilization). It costs one partial interval (~300 ms) on the
early hit; the commit stays authoritative and is never delayed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from agent.contract import AudienceUtterance
from agent.speech.echo_guard import EchoGuard
from agent.speech.keyterms import KeytermCatalog, PartialHitTracker, normalize
from agent.speech.scribe import (
    CommittedTranscript,
    ErrorClass,
    PartialTranscript,
    ScribeError,
    ScribeEvent,
    StreamReset,
)
from agent.speech.vad import GateEvent, SpeechGate

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]
type Emit = Callable[[AudienceUtterance], None]


class Transcriber(Protocol):
    """What the pipeline needs from Scribe (lets tests drive the pipeline directly)."""

    def send_audio(self, pcm: bytes) -> None: ...
    def commit(self) -> None: ...


@dataclass
class PipelineStats:
    segments: int = 0
    early_utterances: int = 0
    committed_utterances: int = 0
    echoes_dropped: int = 0
    unstable_partials: int = 0
    empty_commits: int = 0
    degraded: bool = False


class SpeechPipeline:
    def __init__(
        self,
        *,
        screen_id: str,
        gate: SpeechGate,
        transcriber: Transcriber,
        keyterms: KeytermCatalog,
        emit: Emit,
        echo_guard: EchoGuard | None = None,
        language: str = "es",
        partial_stability: int = 2,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if partial_stability < 1:
            raise ValueError("partial_stability must be at least 1")
        self._screen_id = screen_id
        self._gate = gate
        self._transcriber = transcriber
        self._tracker = PartialHitTracker(keyterms)
        self._keyterms = keyterms
        self._emit = emit
        self._echo = echo_guard or EchoGuard()
        self._language = language
        self._clock = clock
        self._session = uuid4().hex  # anonymous, regenerated on process start
        self._utterance = 0
        self._seq = 0
        self._last_meaning: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self._stability = partial_stability
        self._candidate: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._candidate_seen = 0
        self.stats = PipelineStats()

    # ------------------------------------------------------------ audio side

    def on_frame(self, frame: bytes) -> None:
        for out in self._gate.feed(frame):
            if out.event is GateEvent.SPEECH_START:
                self.stats.segments += 1
            elif out.event is GateEvent.AUDIO:
                self._transcriber.send_audio(out.audio)
            elif out.event is GateEvent.SPEECH_END:
                self._transcriber.commit()

    # ------------------------------------------------------------ transcript side

    def on_event(self, event: ScribeEvent) -> None:
        if isinstance(event, PartialTranscript):
            self._on_partial(event.text, event.turn_probability)
        elif isinstance(event, StreamReset):
            self._gate.reset()
            self._forget_candidate()
            self._tracker.on_committed("")
            if self._last_meaning != ((), ()):
                self._send("", (), self._clock(), self._language)
            self._last_meaning = ((), ())
            self._utterance += 1
        elif isinstance(event, CommittedTranscript):
            self._on_committed(event.text, event.language_code)
        elif isinstance(event, ScribeError) and event.error_class is ErrorClass.FATAL:
            self.stats.degraded = True

    def finish(self) -> None:
        """Flush a finite audio source even when it has no trailing silence."""
        if self._gate.is_open:
            self._transcriber.commit()
            self._gate.reset()

    def _on_partial(self, text: str, turn_probability: float | None = None) -> None:
        words = normalize(text).split()
        self._gate.hold_incomplete_turn(
            (turn_probability is not None and turn_probability < 0.5)
            or (
                bool(words)
                and words[-1]
                in {
                    "y",
                    "o",
                    "pero",
                    "sin",
                    "no",
                    "ni",
                    "quiero",
                    "necesito",
                    "un",
                    "una",
                    "con",
                    "and",
                    "without",
                    "want",
                }
            )
        )
        now = self._clock()
        if self._echo.is_echo(text, now):
            self.stats.echoes_dropped += 1
            return
        meaning = self._keyterms.understand(text)
        current = (meaning.wanted, meaning.rejected)
        if current == self._candidate:
            self._candidate_seen += 1
        else:
            self._candidate, self._candidate_seen = current, 1
        if current != self._last_meaning and self._candidate_seen < self._stability:
            self.stats.unstable_partials += 1
            return
        if current != self._last_meaning:
            self._last_meaning = current
            self.stats.early_utterances += 1
            # Each update describes the whole current request. Sending only newly
            # matched words would erase its earlier interests when the backend
            # replaces a single viewer's previous request.
            self._send(text, meaning.wanted, now, self._language, is_final=False)

    def _forget_candidate(self) -> None:
        self._candidate, self._candidate_seen = None, 0

    def _on_committed(self, text: str, language: str | None) -> None:
        now = self._clock()
        self._forget_candidate()
        had_partial = self._last_meaning != ((), ())
        self._last_meaning = ((), ())
        if not text.strip():
            self.stats.empty_commits += 1
            self._tracker.on_committed("")
            if had_partial:
                self._send("", (), now, language or self._language)
            self._utterance += 1
            return
        if self._echo.is_echo(text, now):
            self.stats.echoes_dropped += 1
            self._tracker.on_committed("")
            return
        hits = self._tracker.on_committed(text)
        self.stats.committed_utterances += 1
        self._send(text, hits, now, language or self._language)
        self._utterance += 1

    def _send(
        self,
        text: str,
        hits: tuple[str, ...],
        at: datetime,
        language: str,
        *,
        is_final: bool = True,
    ) -> None:
        self._seq += 1
        self._emit(
            AudienceUtterance(
                screen_id=self._screen_id,
                seq=self._seq,
                measured_at=at,
                text=text[:1000],
                language=language[:16] if len(language) >= 2 else self._language,
                keyterms_hit=list(hits),
                is_final=is_final,
                utterance_id=f"{self._session}:{self._utterance}",
            )
        )
        # Counts only: never the text.
        logger.info("utterance seq=%d keyterms=%d", self._seq, len(hits))
