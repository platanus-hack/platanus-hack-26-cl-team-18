"""Where sound-scene labels come from: the room mic, a WAV replay, or a fixed fake.

`SoundSceneSource.current(now)` is synchronous and never blocks, so the vision
loop can ask for the latest labels on every frame without awaiting anything
(hot path). Each implementation classifies ~1 s windows every 0.5 s with
`agent.sound.scene` and keeps only the latest labels.

Privacy: audio lives only in a 1 s in-memory ring buffer that is overwritten
continuously and zeroed on close. Nothing is written, logged or sent except the
labels. This is not speech mode: no VAD, no recognizer, no words (ADR 0005 is
unaffected, ADR 0008 D13).
"""

from __future__ import annotations

import threading
import time
import wave
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from agent.sound.scene import SAMPLE_RATE, SceneConfig, SceneLabel, classify_window, to_float


class SoundSceneSource(Protocol):
    def current(self, now: datetime) -> tuple[SceneLabel, ...]:
        """The latest labels, or () when there is no fresh window. Never blocks."""
        ...

    def close(self) -> None: ...


class StaticSoundScene:
    """A fake for tests and rehearsals: returns whatever `labels` is set to."""

    def __init__(self, labels: Sequence[SceneLabel] = ()) -> None:
        self.labels = tuple(labels)

    def current(self, now: datetime) -> tuple[SceneLabel, ...]:
        return self.labels

    def close(self) -> None:
        pass


class WavSoundScene:
    """Replays a 16 kHz mono 16-bit WAV on the vision clock (deterministic).

    The first `current()` call anchors t=0; each call returns the labels of the
    last full window ending at or before the elapsed time, and () after the
    file ends. Labels are computed up front; the samples are then discarded.
    """

    def __init__(
        self,
        path: Path,
        *,
        window_s: float = 1.0,
        hop_s: float = 0.5,
        config: SceneConfig | None = None,
    ) -> None:
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                raise ValueError(f"{path} must be 16 kHz mono 16-bit PCM")
            samples = to_float(wav.readframes(wav.getnframes()))
        self._windows = scene_timeline(samples, window_s=window_s, hop_s=hop_s, config=config)
        self._duration_s = len(samples) / SAMPLE_RATE
        self._start: datetime | None = None

    def current(self, now: datetime) -> tuple[SceneLabel, ...]:
        if self._start is None:
            self._start = now
        elapsed = (now - self._start).total_seconds()
        if elapsed > self._duration_s:
            return ()
        labels: tuple[SceneLabel, ...] = ()
        for end_s, window_labels in self._windows:
            if end_s > elapsed:
                break
            labels = window_labels
        return labels

    def close(self) -> None:
        self._windows = []


def scene_timeline(
    samples: np.ndarray,
    *,
    window_s: float = 1.0,
    hop_s: float = 0.5,
    config: SceneConfig | None = None,
) -> list[tuple[float, tuple[SceneLabel, ...]]]:
    """(window end in seconds, labels) for each hop over a mono float signal."""
    window, hop = int(window_s * SAMPLE_RATE), int(hop_s * SAMPLE_RATE)
    return [
        (
            (start + window) / SAMPLE_RATE,
            classify_window(samples[start : start + window], config=config),
        )
        for start in range(0, len(samples) - window + 1, hop)
    ]


class MicSoundScene:
    """The room mic through `sounddevice` (`edge[audio]` extra).

    PortAudio's callback copies each block into a 1 s ring buffer; every hop it
    classifies the window (well under 1 ms of numpy) and stores the labels
    behind a lock. Labels older than `stale_after_s` are not reported, so a
    dead mic yields no sound scene instead of a stuck one.
    """

    def __init__(
        self,
        *,
        device: int | str | None = None,
        window_s: float = 1.0,
        hop_s: float = 0.5,
        stale_after_s: float = 3.0,
        config: SceneConfig | None = None,
    ) -> None:
        self._device = device
        self._ring = np.zeros(int(window_s * SAMPLE_RATE), dtype=np.float32)
        self._hop = int(hop_s * SAMPLE_RATE)
        self._filled = 0
        self._since_hop = 0
        self._stale_after = stale_after_s
        self._config = config
        self._lock = threading.Lock()
        self._latest: tuple[float, tuple[SceneLabel, ...]] | None = None
        self._stream: Any = None

    def start(self) -> None:
        import sounddevice as sd  # optional dependency: edge[audio]

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=512,
            device=self._device,
            callback=self._on_audio,
        )
        self._stream.start()

    def feed(self, block: np.ndarray) -> None:
        """Append mono float samples (the PortAudio callback; also used by tests)."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)[-len(self._ring) :]
        self._ring = np.roll(self._ring, -len(block))
        self._ring[-len(block) :] = block
        self._filled = min(len(self._ring), self._filled + len(block))
        self._since_hop += len(block)
        if self._filled == len(self._ring) and self._since_hop >= self._hop:
            self._since_hop = 0
            labels = classify_window(self._ring, config=self._config)
            with self._lock:
                self._latest = (time.monotonic(), labels)

    def _on_audio(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        self.feed(indata[:, 0])

    def current(self, now: datetime) -> tuple[SceneLabel, ...]:
        with self._lock:
            latest = self._latest
        if latest is None or time.monotonic() - latest[0] > self._stale_after:
            return ()
        return latest[1]

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._ring[:] = 0
        with self._lock:
            self._latest = None
