from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock

from app.modules.common.application.clock import BaseClock


class FixedClock(BaseClock):
    """Deterministic clock for tests and replay: time moves only when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware start")
        self._now = start.astimezone(UTC)
        self._lock = Lock()

    def now_utc(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("time does not go backwards")
        with self._lock:
            self._now += delta
            return self._now

    def set(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware instant")
        with self._lock:
            self._now = instant.astimezone(UTC)
