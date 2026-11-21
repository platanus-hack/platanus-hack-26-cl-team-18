"""Audio sources yielding 32 ms, 16 kHz mono int16 frames.

`MicSource` reads the speakerphone through `sounddevice` (`edge[audio]` extra).
Its callback runs on PortAudio's thread and hands frames to asyncio through a
bounded queue that drops the oldest frame: the mic never blocks and stale
audio is never queued behind fresh audio.

`WavSource` replays a WAV file (rehearsal, demo insurance, live tests).
"""

from __future__ import annotations

import asyncio
import logging
import wave
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Lock

import numpy as np

from agent.speech.vad import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE

logger = logging.getLogger(__name__)


class WavSource:
    def __init__(
        self, path: Path, *, realtime: bool = True, trailing_silence_s: float = 1.0
    ) -> None:
        self._path = path
        self._realtime = realtime
        self._trailing = int(trailing_silence_s * SAMPLE_RATE / FRAME_SAMPLES)

    async def frames(self) -> AsyncIterator[bytes]:
        with wave.open(str(self._path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                raise ValueError(f"{self._path} must be 16 kHz mono 16-bit PCM")
            while chunk := wav.readframes(FRAME_SAMPLES):
                yield chunk.ljust(FRAME_BYTES, b"\0")
                if self._realtime:
                    await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE)
        for _ in range(self._trailing):  # let the VAD close the last segment
            yield bytes(FRAME_BYTES)
            if self._realtime:
                await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE)


class PcmFrameBuffer:
    """Thread-safe bounded PCM framing before scheduling any event-loop callback."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("microphone frame capacity must be positive")
        self._capacity = capacity
        self._frames: deque[bytes] = deque()
        self._tail = b""
        self._lock = Lock()
        self._notified = False
        self.dropped = 0

    def push(self, data: bytes, *, overflow: bool = False) -> bool:
        with self._lock:
            if overflow:
                self.dropped += 1 + len(self._frames)
                self._tail = b""
                self._frames.clear()
            combined = self._tail + data
            complete = len(combined) // FRAME_BYTES * FRAME_BYTES
            for offset in range(0, complete, FRAME_BYTES):
                if len(self._frames) >= self._capacity:
                    self._frames.popleft()
                    self.dropped += 1
                self._frames.append(combined[offset : offset + FRAME_BYTES])
            self._tail = combined[complete:]
            if self._notified or not self._frames:
                return False
            self._notified = True
            return True

    def pop(self) -> bytes | None:
        with self._lock:
            if self._frames:
                return self._frames.popleft()
            self._notified = False
            return None

    def clear(self) -> None:
        """Forget audio from a stream that died; it can't be stitched to the next one."""
        with self._lock:
            self._frames.clear()
            self._tail = b""
            self._notified = False


class Resampler:
    """Streaming int16 rate conversion for mics that can't open at 16 kHz.

    Windowed-sinc low-pass (anti-aliasing at 0.45 x the lower rate) followed by
    linear interpolation, with filter history and fractional phase carried
    across blocks so block boundaries never click. Plenty for speech-to-text.
    """

    def __init__(self, in_rate: int, out_rate: int = SAMPLE_RATE, *, taps: int = 63) -> None:
        if in_rate <= 0 or out_rate <= 0:
            raise ValueError("sample rates must be positive")
        self._ratio = in_rate / out_rate
        self._passthrough = in_rate == out_rate
        cutoff = 0.45 * min(in_rate, out_rate) / in_rate  # cycles per input sample
        n = np.arange(taps) - (taps - 1) / 2
        kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        self._kernel = (kernel / kernel.sum()).astype(np.float64)
        self._history = np.zeros(taps - 1)
        self._buffer = np.zeros(0)
        self._phase = 0.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self._passthrough:
            return samples.astype(np.int16)
        joined = np.concatenate([self._history, samples.astype(np.float64)])
        self._history = joined[len(joined) - len(self._history) :]
        filtered = np.convolve(joined, self._kernel, mode="valid")
        buffer = np.concatenate([self._buffer, filtered])
        if len(buffer) < 2:
            self._buffer = buffer
            return np.zeros(0, dtype=np.int16)
        positions = np.arange(self._phase, len(buffer) - 1, self._ratio)
        out = np.interp(positions, np.arange(len(buffer)), buffer)
        next_phase = positions[-1] + self._ratio if len(positions) else self._phase
        keep_from = min(int(next_phase), len(buffer) - 1)
        self._buffer = buffer[keep_from:]
        self._phase = next_phase - keep_from
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


class PcmConverter:
    """Interleaved int16 at any rate/channel count -> 16 kHz mono int16 bytes."""

    def __init__(self, rate: int, channels: int) -> None:
        if channels < 1:
            raise ValueError("a capture stream needs at least one channel")
        self._channels = channels
        self._resampler = Resampler(rate)
        self._odd = b""

    def __call__(self, data: bytes) -> bytes:
        width = 2 * self._channels
        data = self._odd + data
        whole = len(data) // width * width
        self._odd = data[whole:]
        samples = np.frombuffer(data[:whole], dtype=np.int16)
        if self._channels > 1:
            samples = samples.reshape(-1, self._channels).mean(axis=1)
        return self._resampler.process(samples).tobytes()


class MicUnavailableError(RuntimeError):
    """No capture format could be opened on the device (retried with backoff)."""


class MicStalledError(RuntimeError):
    """The capture callback stopped delivering audio (unplugged, suspended, wedged)."""


type OpenStream = Callable[..., AbstractContextManager[object]]
type QueryDevice = Callable[[int | str | None], Mapping[str, object]]


def _sd_open(**kwargs: object) -> AbstractContextManager[object]:
    import sounddevice as sd  # optional dependency: edge[audio]

    return sd.RawInputStream(dtype="int16", **kwargs)  # type: ignore[arg-type]


def _sd_query(device: int | str | None) -> Mapping[str, object]:
    import sounddevice as sd

    return sd.query_devices(device, "input")  # type: ignore[return-value]


def parse_device(device: int | str | None) -> int | str | None:
    """CLI device: "3" is PortAudio index 3, anything else a name substring."""
    if isinstance(device, str):
        stripped = device.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return int(stripped)
        return stripped
    return device


class MicSource:
    """The speakerphone mic, kept alive for the whole demo.

    - Opens 16 kHz mono when the device supports it, else the device's native
      rate and channel count, downmixed and resampled (RealtimeSTT/LiveKit
      practice: many USB speakerphones only expose 48 kHz).
    - If the device errors, disappears, or stops calling back for `stall_s`,
      the stream is closed and reopened with capped backoff. Each reopen bumps
      `discontinuities`, so the caller resets the transcriber: a phrase that
      spans a gap is never interpreted.
    """

    def __init__(
        self,
        *,
        device: int | str | None = None,
        max_queued_frames: int = 64,
        stall_s: float = 2.0,
        backoff: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0),
        open_stream: OpenStream = _sd_open,
        query_device: QueryDevice = _sd_query,
    ) -> None:
        if stall_s <= 0 or not backoff:
            raise ValueError("stall timeout and backoff must be positive")
        self._device = parse_device(device)
        self._buffer = PcmFrameBuffer(max_queued_frames)
        self._stall_s = stall_s
        self._backoff = backoff
        self._open_stream = open_stream
        self._query_device = query_device
        self.reopens = 0
        self.format: tuple[int, int] | None = None  # (rate, channels) actually opened

    @property
    def dropped(self) -> int:
        return self._buffer.dropped

    @property
    def discontinuities(self) -> int:
        """Changes whenever audio was lost: overflowed frames or a reopened stream."""
        return self._buffer.dropped + self.reopens

    def _candidates(self) -> list[tuple[int, int]]:
        formats = [(SAMPLE_RATE, 1)]
        try:
            info = self._query_device(self._device)
            rate = int(float(info.get("default_samplerate") or 0))  # type: ignore[arg-type]
            channels = int(info.get("max_input_channels") or 0)  # type: ignore[call-overload]
        except Exception as exc:
            logger.warning("mic query failed: %s", type(exc).__name__)
            return formats
        for candidate in ((rate, 1), (rate, min(channels, 2)), (SAMPLE_RATE, min(channels, 2))):
            if candidate[0] > 0 and candidate[1] > 0 and candidate not in formats:
                formats.append(candidate)
        return formats

    def _open(self, callback_for: Callable[[int, int], Callable[..., None]]) -> object:
        failures: list[str] = []
        for rate, channels in self._candidates():
            try:
                stream = self._open_stream(
                    samplerate=rate,
                    channels=channels,
                    blocksize=max(1, round(FRAME_SAMPLES * rate / SAMPLE_RATE)),
                    device=self._device,
                    callback=callback_for(rate, channels),
                )
            except Exception as exc:
                failures.append(f"{rate}Hz/{channels}ch: {type(exc).__name__}")
                continue
            if (rate, channels) != (SAMPLE_RATE, 1):
                logger.warning("mic opened at %d Hz x%d; resampling to 16 kHz mono", rate, channels)
            self.format = (rate, channels)
            return stream
        raise MicUnavailableError("no capture format opened: " + "; ".join(failures))

    async def frames(self) -> AsyncIterator[bytes]:
        attempt = 0
        while True:
            try:
                async for frame in self._stream_once():
                    attempt = 0
                    yield frame
            except (MicUnavailableError, MicStalledError, OSError) as exc:
                logger.error("mic lost (%s); reopening", type(exc).__name__)
            except Exception as exc:  # PortAudioError and friends: never end the demo
                logger.error("mic error (%s); reopening", type(exc).__name__)
            self.reopens += 1
            self._buffer.clear()
            await asyncio.sleep(self._backoff[min(attempt, len(self._backoff) - 1)])
            attempt += 1

    async def _stream_once(self) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        wakeup = asyncio.Event()

        def callback_for(rate: int, channels: int) -> Callable[..., None]:
            convert = PcmConverter(rate, channels)

            def callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
                pcm = convert(bytes(indata))
                if self._buffer.push(pcm, overflow=bool(status)):
                    # At most one outstanding notification, even if the event loop is
                    # stalled. The audio itself is bounded on the capture thread.
                    loop.call_soon_threadsafe(wakeup.set)

            return callback

        stream = self._open(callback_for)
        with stream:  # type: ignore[attr-defined]
            while True:
                try:
                    await asyncio.wait_for(wakeup.wait(), self._stall_s)
                except TimeoutError:
                    raise MicStalledError(f"no audio for {self._stall_s:.1f}s") from None
                wakeup.clear()
                while (frame := self._buffer.pop()) is not None:
                    yield frame
