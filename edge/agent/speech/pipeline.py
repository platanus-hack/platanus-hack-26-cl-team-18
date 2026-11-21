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

Settled-turn commit: in a crowd the VAD never hears silence, so a segment
would only commit at the 15 s cap, and final-only intents would wait that
long. Babble doesn't add words, though: once the longest partial stops
growing for `settle_frames` of streamed audio (~1.2 s), has at least two
words, and doesn't end on a dangling word ("no", "sin", "quiero", "y"), the
segment is committed and the gate keeps listening. Every keyterm hallucination
measured in babble was one word ("Shop", "Sopaipilla"); a real request
("una chela") has two, so a lone hallucination never becomes a final. A quiet room never gets here: its VAD closes at 320 ms.
(Scribe's `turn_probability` was measured too noisy at 10 dB SNR to rely on.)

Low-confidence finals: Scribe's per-word logprob (mean over the segment)
separates real speech from babble hallucinations. Measured live: real speech
-0.30..-1.15 per word (segment means <= -0.95) in every condition, babble
inventions ("Un café", "Creo", "Hola, soy", "Bricksoma") -1.48..-2.92. A final
below `min_confidence` is treated as an empty commit: it retracts any
provisional partial and requests nothing. No confidence (no timestamped twin)
means the final counts as before.

Age-restricted intents are final-only (product invariant 2): a partial whose
meaning *wants* alcohol is not sent at all, because stabilization still lets
a babble hallucination through now and then and the backend re-reads the
partial's text. Rejecting beer early stays allowed: it can only make an ad safer.
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

# A turn ending on one of these is not finished: wait, never commit it early.
_DANGLING = frozenset(
    {"y", "o", "pero", "sin", "no", "ni", "quiero", "necesito", "un", "una", "con"}
    | {"and", "without", "want"}
)

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
    held_restricted_partials: int = 0
    settled_commits: int = 0
    low_confidence_finals: int = 0
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
        final_only_intents: frozenset[str] = frozenset({"cerveza"}),
        settle_frames: int | None = 38,  # ~1.2 s of streamed audio; None disables
        min_confidence: float | None = -1.3,  # mean word logprob; None disables
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
        self._final_only = final_only_intents
        if settle_frames is not None and settle_frames < 1:
            raise ValueError("settle_frames must be positive")
        self._settle_frames = settle_frames
        self._min_confidence = min_confidence
        self._longest = ""
        self._since_growth = 0
        self._dangling = False
        # After a settled commit, late partials still describe the committed
        # segment; they must not arm the settle timer for the crowd that follows.
        self._awaiting_settled_final = False
        self._candidate: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._candidate_seen = 0
        self.stats = PipelineStats()

    # ------------------------------------------------------------ audio side

    def on_frame(self, frame: bytes) -> None:
        for out in self._gate.feed(frame):
            if out.event is GateEvent.SPEECH_START:
                self.stats.segments += 1
                self._forget_growth()
            elif out.event is GateEvent.AUDIO:
                self._transcriber.send_audio(out.audio)
            elif out.event is GateEvent.SPEECH_END:
                self._transcriber.commit()
                self._forget_growth()
        if self._settle_frames is None or not self._gate.is_open or not self._longest:
            return
        self._since_growth += 1
        if (
            self._since_growth >= self._settle_frames
            and not self._dangling
            and len(self._longest.split()) >= 2
        ):
            if self._gate.end_segment():
                self._transcriber.commit()
                self.stats.settled_commits += 1
                self._awaiting_settled_final = True
            self._forget_growth()

    def _forget_growth(self) -> None:
        self._longest, self._since_growth, self._dangling = "", 0, False

    # ------------------------------------------------------------ transcript side

    def on_event(self, event: ScribeEvent) -> None:
        if isinstance(event, PartialTranscript):
            self._on_partial(event.text, event.turn_probability)
        elif isinstance(event, StreamReset):
            self._gate.reset()
            self._forget_candidate()
            self._forget_growth()
            self._awaiting_settled_final = False
            self._tracker.on_committed("")
            if self._last_meaning != ((), ()):
                self._send("", (), self._clock(), self._language)
            self._last_meaning = ((), ())
            self._utterance += 1
        elif isinstance(event, CommittedTranscript):
            if self._awaiting_settled_final:
                self._awaiting_settled_final = False
                self._forget_growth()
            text = event.text
            if (
                self._min_confidence is not None
                and event.confidence is not None
                and event.confidence < self._min_confidence
                and text.strip()
            ):
                self.stats.low_confidence_finals += 1
                text = ""  # an invention of the recognizer: request nothing
            self._on_committed(text, event.language_code)
        elif isinstance(event, ScribeError) and event.error_class is ErrorClass.FATAL:
            self.stats.degraded = True

    def finish(self) -> None:
        """Flush a finite audio source even when it has no trailing silence."""
        if self._gate.is_open:
            self._transcriber.commit()
            self._gate.reset()

    def _on_partial(self, text: str, turn_probability: float | None = None) -> None:
        norm = normalize(text)
        words = norm.split()
        if self._awaiting_settled_final:
            pass  # a late partial of the segment we already committed
        elif len(norm) > len(self._longest):
            self._longest, self._since_growth = norm, 0
        self._dangling = bool(words) and words[-1] in _DANGLING
        self._gate.hold_incomplete_turn(
            (turn_probability is not None and turn_probability < 0.5) or self._dangling
        )
        now = self._clock()
        self._act_on_partial(text, now)

    def _act_on_partial(self, text: str, now: datetime) -> None:
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
        if self._final_only.intersection(meaning.wanted):
            self.stats.held_restricted_partials += 1
            return  # the committed transcript decides; never the partial
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
