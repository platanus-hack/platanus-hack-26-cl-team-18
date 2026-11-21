"""Ephemeral approved ad copy + actual player windows; no audience text retained."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.contract.speech_matching import normalize

# Allow bounded VAD + network + transcription delay after audible playback ends.
# This is a content match, never a blanket microphone mute or speaker inference.
TRANSCRIPT_GRACE = timedelta(seconds=2.4)
MAX_WINDOWS = 8


@dataclass(frozen=True, slots=True)
class _Window:
    copy: str
    until: datetime


class PlaybackEcho:
    def __init__(self) -> None:
        self._windows: list[_Window] = []

    def start(self, copy: str, at: datetime, duration: timedelta) -> None:
        self._expire(at)
        # A new voice interrupts the previous one; keep its delayed transcripts
        # recognizable while the new creative starts talking.
        self.stop(at)
        self._windows.append(
            _Window(normalize(copy), at + min(duration, timedelta(seconds=120)) + TRANSCRIPT_GRACE)
        )
        self._windows = self._windows[-MAX_WINDOWS:]

    def stop(self, at: datetime) -> None:
        if self._windows:
            last = self._windows[-1]
            self._windows[-1] = _Window(last.copy, min(last.until, at + TRANSCRIPT_GRACE))

    def _expire(self, at: datetime) -> None:
        self._windows = [window for window in self._windows if at <= window.until]

    def matches(self, text: str, at: datetime) -> bool:
        self._expire(at)
        heard = normalize(text)
        if len(heard) < 8:
            return False
        return any(
            heard in window.copy or SequenceMatcher(None, heard, window.copy).ratio() >= 0.8
            for window in self._windows
        )
