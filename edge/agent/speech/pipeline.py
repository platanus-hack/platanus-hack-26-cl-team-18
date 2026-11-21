"""Speech mode: mic frames -> VAD gate -> Scribe -> keyterms -> `audience.utterance`.

Privacy (ADR 0005, binding):
- Speech mode is opt-in per screen; the caller shows a listening indicator.
- Text lives in memory just long enough to build one utterance message. It is
  never logged or persisted here (tests assert it).
- Our own voice-overs are filtered by the echo guard before they count.

Latency: a keyterm on a *partial* transcript emits an utterance immediately
(the "it heard me" signal); the committed transcript follows with all hits.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from agent.contract import AudienceUtterance
from agent.speech.echo_guard import EchoGuard
from agent.speech.keyterms import KeytermCatalog, PartialHitTracker
from agent.speech.scribe import (
    CommittedTranscript,
    ErrorClass,
    PartialTranscript,
    ScribeError,
    ScribeEvent,
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
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._screen_id = screen_id
        self._gate = gate
        self._transcriber = transcriber
        self._tracker = PartialHitTracker(keyterms)
        self._emit = emit
        self._echo = echo_guard or EchoGuard()
        self._language = language
        self._clock = clock
        self._seq = 0
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
            self._on_partial(event.text)
        elif isinstance(event, CommittedTranscript):
            self._on_committed(event.text, event.language_code)
        elif isinstance(event, ScribeError) and event.error_class is ErrorClass.FATAL:
            self.stats.degraded = True

    def _on_partial(self, text: str) -> None:
        now = self._clock()
        if self._echo.is_echo(text, now):
            self.stats.echoes_dropped += 1
            return
        hits = self._tracker.on_partial(text)
        if hits:
            self.stats.early_utterances += 1
            self._send(text, hits, now, self._language)

    def _on_committed(self, text: str, language: str | None) -> None:
        now = self._clock()
        if not text.strip():
            self.stats.empty_commits += 1
            self._tracker.on_committed("")
            return
        if self._echo.is_echo(text, now):
            self.stats.echoes_dropped += 1
            self._tracker.on_committed("")
            return
        hits = self._tracker.on_committed(text)
        self.stats.committed_utterances += 1
        self._send(text, hits, now, language or self._language)

    def _send(self, text: str, hits: tuple[str, ...], at: datetime, language: str) -> None:
        self._seq += 1
        self._emit(
            AudienceUtterance(
                screen_id=self._screen_id,
                seq=self._seq,
                measured_at=at,
                text=text[:1000],
                language=language[:16] if len(language) >= 2 else self._language,
                keyterms_hit=list(hits),
            )
        )
        # Counts only: never the text.
        logger.info("utterance seq=%d keyterms=%d", self._seq, len(hits))
