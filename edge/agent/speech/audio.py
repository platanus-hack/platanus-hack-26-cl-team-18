"""Sound-scene port; no recording or inference is enabled by default."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from os import environ
from typing import Protocol


@dataclass(frozen=True)
class AudioWindow:
    start: float
    end: float
    samples: bytes


@dataclass(frozen=True)
class AudioEvent:
    label: str
    observed_at: float
    confidence: float
    source: str


class AudioSource(Protocol):
    def windows(self) -> AsyncIterator[AudioWindow]: ...


class AudioAnalyzer(Protocol):
    async def analyze(self, window: AudioWindow) -> list[AudioEvent]: ...


def audio_enabled() -> bool:
    return environ.get("AUDIO_ENABLED", "false").lower() == "true"


async def process_audio(source: AudioSource, analyzer: AudioAnalyzer, publish, *, enabled=False):
    if not enabled:
        return
    async for window in source.windows():
        for event in await analyzer.analyze(window):
            publish(event)
