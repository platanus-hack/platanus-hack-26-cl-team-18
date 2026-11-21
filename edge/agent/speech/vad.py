"""Voice activity gating: only speech segments are streamed (docs/stack.md §3).

Audio is 16 kHz mono int16 in 32 ms frames (512 samples), Silero VAD's native
frame size. The gate adds hysteresis so a sentence isn't chopped at every
short pause, and pre-roll so the first syllable reaches the recognizer.
"""

from __future__ import annotations

import collections
import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512  # 32 ms
FRAME_BYTES = FRAME_SAMPLES * 2

logger = logging.getLogger(__name__)


class VoiceActivityDetector(Protocol):
    def speech_probability(self, frame: bytes) -> float:
        """Probability in [0, 1] that this 32 ms int16 frame contains speech."""
        ...


def frame_dbfs(frame: bytes) -> float:
    """RMS level of an int16 frame in dBFS (digital silence is -180)."""
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    if not samples.size:
        return -180.0
    # Remove DC first: a cheap USB mic's offset must not read as loud speech.
    samples -= samples.mean()
    rms = float(np.sqrt(np.mean(samples**2)))
    return 20 * math.log10(max(rms, 1e-9))


class EnergyVad:
    """Dependency-free fallback: frame level against a threshold.

    Fixed mode (default): 1.0 at or above `threshold_dbfs`, else 0.0.

    Adaptive mode (`adaptive=True`, what the agent ships as its fallback):
    minimum-statistics noise tracking (Martin 2001). The noise floor is the
    minimum of the 3-frame smoothed level over the last `window_s`; the gaps
    between words reach it even during continuous speech, so talking never
    raises the floor, while a room that gets louder (music, a crowd) raises
    it within `window_s` instead of holding the gate open until the cap.
    The probability is graded around `floor + margin_db`, so the gate's
    open/close hysteresis applies to the energy VAD too. It must hear the room
    (silence included) before speech: `ResilientVad` feeds it every frame.
    """

    def __init__(
        self,
        *,
        threshold_dbfs: float = -42.0,
        adaptive: bool = False,
        margin_db: float = 6.0,
        slope_db: float = 6.0,
        min_threshold_dbfs: float = -58.0,
        window_s: float = 4.0,
    ) -> None:
        if slope_db <= 0 or window_s <= 0:
            raise ValueError("energy VAD slope and window must be positive")
        self._threshold = threshold_dbfs
        self._adaptive = adaptive
        self._margin = margin_db
        self._slope = slope_db
        self._min_threshold = min_threshold_dbfs
        self._levels: collections.deque[float] = collections.deque(maxlen=3)
        self._minima: collections.deque[float] = collections.deque(
            maxlen=max(1, round(window_s * SAMPLE_RATE / FRAME_SAMPLES))
        )

    @property
    def threshold_dbfs(self) -> float:
        if not self._adaptive:
            return self._threshold
        if not self._minima:
            return max(self._min_threshold, self._threshold)
        return max(self._min_threshold, min(self._minima) + self._margin)

    def speech_probability(self, frame: bytes) -> float:
        dbfs = frame_dbfs(frame)
        if not self._adaptive:
            return 1.0 if dbfs >= self._threshold else 0.0
        threshold = self.threshold_dbfs
        self._levels.append(dbfs)
        self._minima.append(sum(self._levels) / len(self._levels))
        # 0.5 at the threshold, saturating `slope_db` either side.
        return min(1.0, max(0.0, 0.5 + (dbfs - threshold) / (2 * self._slope)))

    def reset(self) -> None:
        """The room floor survives a stream reset: only the phrase was damaged."""


class SileroVad:
    """Silero VAD v5 via onnxruntime (`edge[audio]` extra). Model file is downloaded once."""

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort  # optional dependency

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(str(model_path), sess_options=options)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        # Fail at startup, not on the first word: a corrupt or wrong-version
        # model raises here. The first ONNX run is also the slow one.
        for _ in range(3):
            self.speech_probability(bytes(FRAME_BYTES))
        self.reset()

    def reset(self) -> None:
        """Clear recurrent state between independent streams (Silero's `reset_states`)."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def speech_probability(self, frame: bytes) -> float:
        audio = np.frombuffer(frame, dtype=np.int16).astype(np.float32)[None, :] / 32768.0
        x = np.concatenate([self._context, audio], axis=1)
        out, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": self._sr}
        )
        self._context = audio[:, -64:]
        return float(out[0][0])


class ResilientVad:
    """The VAD the agent actually runs: a volume floor, the model, and a fallback.

    - Frames below `min_dbfs` are silence without asking the model (Pipecat's
      `min_volume`): a muted mic or near-digital-silence can't open a segment.
    - A model that raises or returns garbage (NaN, out of range) is replaced by
      the fallback for the rest of the run, logged once. The fallback is fed
      every frame so it is calibrated to the room before it is ever needed. Speech mode degrades
      to the energy VAD instead of crashing mid-demo.
    """

    def __init__(
        self,
        primary: VoiceActivityDetector,
        fallback: VoiceActivityDetector | None = None,
        *,
        min_dbfs: float = -60.0,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or EnergyVad(adaptive=True)
        self._min_dbfs = min_dbfs
        self.failed_over = False

    def speech_probability(self, frame: bytes) -> float:
        # The fallback hears every frame, silence included: its noise floor is
        # then already right for this room on the day the model fails over.
        fallback = self._fallback.speech_probability(frame)
        if frame_dbfs(frame) < self._min_dbfs:
            return 0.0
        if self._primary is self._fallback:
            return fallback
        if not self.failed_over:
            try:
                probability = float(self._primary.speech_probability(frame))
            except Exception as exc:  # any model failure: degrade, never crash
                logger.error("VAD model failed (%s); using energy VAD", type(exc).__name__)
            else:
                if math.isfinite(probability) and 0.0 <= probability <= 1.0:
                    return probability
                logger.error("VAD model returned an invalid probability; using energy VAD")
            self.failed_over = True
        return fallback

    def reset(self) -> None:
        for vad in (self._primary, self._fallback):
            _reset(vad)


def _reset(vad: VoiceActivityDetector) -> None:
    reset = getattr(vad, "reset", None)
    if callable(reset):
        try:
            reset()
        except Exception:  # a reset must never take speech mode down
            logger.warning("VAD reset failed", exc_info=False)


class GateEvent(StrEnum):
    SPEECH_START = "speech_start"
    AUDIO = "audio"
    SPEECH_END = "speech_end"


@dataclass(frozen=True)
class GateOutput:
    event: GateEvent
    audio: bytes = b""


class SpeechGate:
    """Turns a frame stream into speech segments with pre-roll and hangover.

    - Opens after `start_frames` consecutive frames at or above `threshold`;
      emits the pre-roll.
    - Hysteresis (Silero's VADIterator): once open, a frame only counts as
      silence below `end_threshold` (default `threshold - 0.15`, floor 0.01),
      so soft syllables and trailing consonants don't chop the phrase.
    - Closes after `hangover_frames` consecutive non-speech frames.
    - Caps a segment at `max_segment_frames` so a noisy room can't stream forever.
    """

    def __init__(
        self,
        vad: VoiceActivityDetector,
        *,
        threshold: float = 0.5,
        end_threshold: float | None = None,
        start_frames: int = 3,  # ~96 ms
        hangover_frames: int = 10,  # ~320 ms: speech end -> final 227 ms vs 431 ms at 512 ms (bench)
        preroll_frames: int = 10,  # ~320 ms
        max_segment_frames: int = 470,  # ~15 s
    ) -> None:
        if not 0 < threshold <= 1 or min(start_frames, hangover_frames, max_segment_frames) < 1:
            raise ValueError("VAD thresholds and frame windows must be positive")
        if preroll_frames < start_frames:
            raise ValueError("pre-roll must include all speech-start frames")
        self._vad = vad
        self._threshold = threshold
        self._end_threshold = (
            max(threshold - 0.15, 0.01) if end_threshold is None else end_threshold
        )
        if not 0 < self._end_threshold <= threshold:
            raise ValueError("end_threshold must be in (0, threshold]")
        self._start_frames = start_frames
        self._hangover_frames = hangover_frames
        self._max_segment_frames = max_segment_frames
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=preroll_frames)
        self._open = False
        self._voiced_run = 0
        self._silent_run = 0
        self._segment_frames = 0
        self._waiting_for_silence = False
        self._extended_hangover = hangover_frames

    def reset(self) -> None:
        """A discontinuity invalidates the phrase; wait for a clean new turn."""
        self._open = False
        self._voiced_run = self._silent_run = self._segment_frames = 0
        self._preroll.clear()
        self._waiting_for_silence = True
        self._extended_hangover = self._hangover_frames
        _reset(self._vad)

    def hold_incomplete_turn(self, incomplete: bool) -> None:
        # Preserve normal 320 ms latency, but allow up to ~1.2 s for a dangling
        # conjunction/negation or a model-reported incomplete turn.
        self._extended_hangover = (
            max(self._hangover_frames, 38) if incomplete else self._hangover_frames
        )

    @property
    def is_open(self) -> bool:
        return self._open

    def feed(self, frame: bytes) -> Iterator[GateOutput]:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"expected {FRAME_BYTES}-byte frames, got {len(frame)}")
        probability = self._vad.speech_probability(frame)
        if not math.isfinite(probability):
            probability = 0.0
        voiced = probability >= (self._end_threshold if self._open else self._threshold)
        if self._waiting_for_silence:
            self._silent_run = 0 if voiced else self._silent_run + 1
            if self._silent_run >= self._hangover_frames:
                self._waiting_for_silence = False
                self._silent_run = 0
            return
        if not self._open:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= self._start_frames:
                self._open = True
                self._silent_run = 0
                self._segment_frames = len(self._preroll)
                yield GateOutput(GateEvent.SPEECH_START)
                yield GateOutput(GateEvent.AUDIO, b"".join(self._preroll))
                self._preroll.clear()
            return
        self._segment_frames += 1
        self._silent_run = 0 if voiced else self._silent_run + 1
        yield GateOutput(GateEvent.AUDIO, frame)
        if (
            self._silent_run >= self._extended_hangover
            or self._segment_frames >= self._max_segment_frames
        ):
            self._open = False
            self._voiced_run = 0
            self._extended_hangover = self._hangover_frames
            yield GateOutput(GateEvent.SPEECH_END)
