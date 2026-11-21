"""Stop the screen from re-targeting itself with its own voice-over (docs/stack.md §3).

Hardware echo cancellation does most of the work. This guard is the second
line: it drops transcripts that land inside a playback window the backend
announced, or that fuzzy-match a voice-over line currently on air.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from agent.speech.keyterms import normalize

# Speech heard just after the voice-over ends may still be its room echo/reverb.
TAIL = timedelta(milliseconds=400)
SIMILARITY = 0.72


@dataclass(frozen=True)
class AudioWindow:
    starts_at: datetime
    ends_at: datetime
    text: str


class EchoGuard:
    def __init__(self, *, tail: timedelta = TAIL, similarity: float = SIMILARITY) -> None:
        self._windows: list[AudioWindow] = []
        self._tail = tail
        self._similarity = similarity
        self.dropped = 0

    def announce(self, window: AudioWindow) -> None:
        if window.ends_at < window.starts_at:
            raise ValueError("audio window ends before it starts")
        self._windows.append(window)

    def is_echo(self, text: str, heard_at: datetime) -> bool:
        self._expire(heard_at)
        norm = normalize(text)
        if not norm:
            return False
        for window in self._windows:
            on_air = window.starts_at <= heard_at <= window.ends_at + self._tail
            if on_air or self._resembles(norm, normalize(window.text)):
                self.dropped += 1
                return True
        return False

    def _resembles(self, heard: str, spoken: str) -> bool:
        if not spoken:
            return False
        if heard in spoken:  # a fragment of our own line
            return len(heard) >= 8
        return SequenceMatcher(None, heard, spoken).ratio() >= self._similarity

    def _expire(self, now: datetime) -> None:
        horizon = now - timedelta(seconds=30)
        self._windows = [w for w in self._windows if w.ends_at >= horizon]
