"""Voice activity gating: only speech segments are streamed (docs/stack.md §3).

Audio is 16 kHz mono int16 in 32 ms frames (512 samples), Silero VAD's native
frame size. The gate adds hysteresis so a sentence isn't chopped at every
short pause, and pre-roll so the first syllable reaches the recognizer.
"""

from __future__ import annotations

import collections
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512  # 32 ms
FRAME_BYTES = FRAME_SAMPLES * 2


class VoiceActivityDetector(Protocol):
    def speech_probability(self, frame: bytes) -> float:
        """Probability in [0, 1] that this 32 ms int16 frame contains speech."""
        ...


class EnergyVad:
    """Dependency-free fallback: RMS energy against a noise floor. Good enough for tests."""

    def __init__(self, *, threshold_dbfs: float = -42.0) -> None:
        self._threshold = threshold_dbfs

    def speech_probability(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
        dbfs = 20 * np.log10(max(rms, 1e-9))
        return 1.0 if dbfs >= self._threshold else 0.0


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

    def speech_probability(self, frame: bytes) -> float:
        audio = np.frombuffer(frame, dtype=np.int16).astype(np.float32)[None, :] / 32768.0
        x = np.concatenate([self._context, audio], axis=1)
        out, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": self._sr}
        )
        self._context = audio[:, -64:]
        return float(out[0][0])


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

    - Opens after `start_frames` consecutive speech frames; emits the pre-roll.
    - Closes after `hangover_frames` consecutive non-speech frames.
    - Caps a segment at `max_segment_frames` so a noisy room can't stream forever.
    """

    def __init__(
        self,
        vad: VoiceActivityDetector,
        *,
        threshold: float = 0.5,
        start_frames: int = 3,  # ~96 ms
        hangover_frames: int = 16,  # ~512 ms
        preroll_frames: int = 10,  # ~320 ms
        max_segment_frames: int = 470,  # ~15 s
    ) -> None:
        self._vad = vad
        self._threshold = threshold
        self._start_frames = start_frames
        self._hangover_frames = hangover_frames
        self._max_segment_frames = max_segment_frames
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=preroll_frames)
        self._open = False
        self._voiced_run = 0
        self._silent_run = 0
        self._segment_frames = 0

    @property
    def is_open(self) -> bool:
        return self._open

    def feed(self, frame: bytes) -> Iterator[GateOutput]:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"expected {FRAME_BYTES}-byte frames, got {len(frame)}")
        voiced = self._vad.speech_probability(frame) >= self._threshold
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
            self._silent_run >= self._hangover_frames
            or self._segment_frames >= self._max_segment_frames
        ):
            self._open = False
            self._voiced_run = 0
            yield GateOutput(GateEvent.SPEECH_END)
