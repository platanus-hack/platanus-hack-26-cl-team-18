from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class DateRange(ValueObject):
    """Value Object representing a business date range.
    Uses 'date' (Business Date) rather than 'datetime' (Instant).
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        self._enforce_types()
        self._enforce_invariants()

    @classmethod
    def create(cls, start: date, end: date) -> DateRange:
        return cls(start=start, end=end)

    def _enforce_types(self) -> None:
        if not isinstance(self.start, date):
            raise TypeError("DateRange.start must be date")
        if not isinstance(self.end, date):
            raise TypeError("DateRange.end must be date")

    def _enforce_invariants(self) -> None:
        if self.start > self.end:
            raise ValueError("DateRange.start cannot be after end")

    def contains(self, target_date: date) -> bool:
        return self.start <= target_date <= self.end

    @property
    def days_count(self) -> int:
        return (self.end - self.start).days + 1

    def to_plain(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def from_plain(cls, payload: object) -> DateRange:
        if not isinstance(payload, Mapping):
            raise ValueError("DateRange payload must be a JSON object")
        if set(payload) != {"start", "end"} or any(not isinstance(key, str) for key in payload):
            raise ValueError("DateRange payload keys invalid")
        start = payload["start"]
        end = payload["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("DateRange start/end must be str")
        try:
            return cls.create(start=date.fromisoformat(start), end=date.fromisoformat(end))
        except ValueError as exc:
            raise ValueError("DateRange start/end must be ISO dates") from exc
