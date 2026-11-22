"""Ephemeral approved ad copy + actual player windows; no audience text retained."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.contract.speech_matching import normalize

# Allow bounded VAD + network + transcription delay after audible playback ends.
# This is a content match, never a blanket microphone mute or speaker inference.
TRANSCRIPT_GRACE = timedelta(seconds=2.4)
MAX_WINDOWS = 8
# A partial of our own line with one recognition error ("nescafe un cafes rapido")
# is neither a substring nor globally similar to a long line. Local alignment: the
# share of the heard letters (spaces ignored, so "nes cafe" == "nescafe") found in
# order in the line. Real requests over a voice-over measured <= 0.70 on the demo
# catalog; one-error echo partials >= 0.87.
LOCAL_ALIGNMENT = 0.85
MIN_LOCAL_CHARS = 12


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
            heard in window.copy
            or SequenceMatcher(None, heard, window.copy).ratio() >= 0.8
            or _locally_aligned(heard, window.copy)
            for window in self._windows
        )


def _locally_aligned(heard: str, copy: str) -> bool:
    # Realtime partials stutter ("agua agua fria"): a repeated word is one word.
    words = heard.split()
    heard = "".join(w for i, w in enumerate(words) if i == 0 or w != words[i - 1])
    copy = copy.replace(" ", "")
    if len(heard) < MIN_LOCAL_CHARS:
        return False
    blocks = SequenceMatcher(None, heard, copy, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(heard) >= LOCAL_ALIGNMENT
