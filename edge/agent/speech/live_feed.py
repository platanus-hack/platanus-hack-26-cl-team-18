"""Feed captured audio to the speech pipeline only while the live switch listens.

The mic stream is opened when `listening` turns on and closed (the generator is
closed, which closes the capture stream) as soon as it turns off. A phrase cut
by the switch is never transcribed: `on_cut` discards the provider's buffer.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from agent.live import LiveGate

logger = logging.getLogger(__name__)


class AudioSource(Protocol):
    def frames(self) -> AsyncIterator[bytes]: ...


async def feed_while_listening(
    source: AudioSource,
    live: LiveGate,
    *,
    on_frame: Callable[[bytes], None],
    on_cut: Callable[[], None],
    on_state: Callable[[bool], None] = lambda _listening: None,
) -> None:
    """Return when a finite source (a WAV) ends; a live mic runs until cancelled.

    A source exposing `discontinuities` (the mic) resets the transcriber on a gap,
    so the tail of a discontinuous phrase is never interpreted.
    """
    gaps = getattr(source, "discontinuities", 0)
    while True:
        if not live.listening:
            on_state(False)
            await live.wait_listening()
        on_state(True)
        cut = False
        frames = source.frames()
        try:
            async for frame in frames:
                if not live.listening:
                    cut = True
                    break
                now = getattr(source, "discontinuities", 0)
                if now != gaps:
                    gaps = now
                    on_cut()
                on_frame(frame)
        finally:
            close = getattr(frames, "aclose", None)
            if close is not None:
                await close()
        if not cut:
            return
        on_cut()
        gaps = getattr(source, "discontinuities", 0)
