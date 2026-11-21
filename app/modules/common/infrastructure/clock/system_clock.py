from __future__ import annotations

from datetime import UTC, datetime

from app.modules.common.application.clock import BaseClock


class SystemClock(BaseClock):
    def now_utc(self) -> datetime:
        return datetime.now(UTC)
