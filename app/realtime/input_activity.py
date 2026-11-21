"""Bounded transport diagnostics: timestamps/counts, never sensor content or utterances."""

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import Lock
from typing import TypedDict

from app.contract.messages import AudienceSnapshot, AudienceUtterance


@dataclass(frozen=True, slots=True)
class _Activity:
    snapshot_at: datetime | None = None
    snapshot_measured_at: datetime | None = None
    utterance_at: datetime | None = None
    keyterm_count: int | None = None


class InputStatus(TypedDict):
    camera_recent: bool
    last_snapshot_at: str | None
    # Server receipt minus the agent's measured_at for the latest snapshot. Beyond
    # the loop's skew bound every snapshot is dropped, so a wrong laptop clock shows here.
    camera_clock_skew_s: float | None
    speech_enabled: bool
    speech_recent: bool
    last_utterance_at: str | None
    last_keyterm_count: int | None
    player_connected: bool


class InputActivity:
    def __init__(self, *, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("input activity capacity must be positive")
        self._capacity = capacity
        self._screens: OrderedDict[str, _Activity] = OrderedDict()
        self._lock = Lock()

    def observe(self, message: AudienceSnapshot | AudienceUtterance, at: datetime) -> None:
        with self._lock:
            current = self._screens.get(message.screen_id, _Activity())
            current = (
                replace(current, snapshot_at=at, snapshot_measured_at=message.measured_at)
                if isinstance(message, AudienceSnapshot)
                else replace(current, utterance_at=at, keyterm_count=len(message.keyterms_hit))
            )
            self._screens[message.screen_id] = current
            self._screens.move_to_end(message.screen_id)
            while len(self._screens) > self._capacity:
                self._screens.popitem(last=False)

    def last_snapshot(self, screen_id: str) -> tuple[datetime, datetime] | None:
        """(server received_at, device measured_at) of the screen's latest admitted snapshot."""
        with self._lock:
            activity = self._screens.get(screen_id)
            if activity is None or activity.snapshot_at is None:
                return None
            assert activity.snapshot_measured_at is not None
            return activity.snapshot_at, activity.snapshot_measured_at

    def snapshot(
        self, *, at: datetime, speech_screens: list[str], players: list[str]
    ) -> dict[str, InputStatus]:
        with self._lock:
            return {
                screen: {
                    "camera_recent": activity.snapshot_at is not None
                    and timedelta(0) <= at - activity.snapshot_at <= timedelta(seconds=5)
                    and activity.snapshot_measured_at is not None
                    and abs(at - activity.snapshot_measured_at) <= timedelta(seconds=5),
                    "last_snapshot_at": activity.snapshot_at.isoformat()
                    if activity.snapshot_at
                    else None,
                    "camera_clock_skew_s": round(
                        (activity.snapshot_at - activity.snapshot_measured_at).total_seconds(), 1
                    )
                    if activity.snapshot_at and activity.snapshot_measured_at
                    else None,
                    "speech_enabled": screen in speech_screens,
                    "speech_recent": activity.utterance_at is not None
                    and timedelta(0) <= at - activity.utterance_at <= timedelta(seconds=60),
                    "last_utterance_at": activity.utterance_at.isoformat()
                    if activity.utterance_at
                    else None,
                    "player_connected": screen in players,
                    "last_keyterm_count": activity.keyterm_count,
                }
                # A connected player in standby has no input yet; it is still connected.
                for screen, activity in (
                    self._screens | {p: _Activity() for p in players if p not in self._screens}
                ).items()
            }
