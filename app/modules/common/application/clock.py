from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from zoneinfo import ZoneInfo


class BaseClock(ABC):
    @abstractmethod
    def now_utc(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""

    def today(self, tz: str) -> date:
        """Return today's date in the given IANA timezone."""
        return self.now_utc().astimezone(ZoneInfo(tz)).date()
