"""Audio sources yielding 32 ms, 16 kHz mono int16 frames.

`MicSource` reads the speakerphone through `sounddevice` (`edge[audio]` extra).
Its callback runs on PortAudio's thread and hands frames to asyncio through a
bounded queue that drops the oldest frame: the mic never blocks and stale
audio is never queued behind fresh audio.

`WavSource` replays a WAV file (rehearsal, demo insurance, live tests).
"""

from __future__ import annotations

import asyncio
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from agent.speech.vad import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE


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


class MicSource:
    def __init__(self, *, device: int | str | None = None, max_queued_frames: int = 64) -> None:
        self._device = device
        self._max = max_queued_frames
        self.dropped = 0

    async def frames(self) -> AsyncIterator[bytes]:
        import sounddevice as sd  # optional dependency: edge[audio]

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self._max)

        def push(frame: bytes) -> None:
            if queue.full():
                queue.get_nowait()
                self.dropped += 1
            queue.put_nowait(frame)

        def callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
            loop.call_soon_threadsafe(push, bytes(indata))

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=callback,
        ):
            while True:
                yield await queue.get()
