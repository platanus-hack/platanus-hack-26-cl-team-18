from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp


@dataclass(frozen=True, slots=True)
class UtcDateTimeRange(ValueObject):
    """Value Object representing a UTC, timezone-aware instant range.

    Semantics: half-open interval [start, end)
    - start is inclusive
    - end is exclusive
    """

    start: UtcTimestamp
    end: UtcTimestamp

    @classmethod
    def create(
        cls,
        start: datetime | UtcTimestamp,
        end: datetime | UtcTimestamp,
        *,
        allow_empty: bool = False,
    ) -> UtcDateTimeRange:
        s = UtcTimestamp.create(start)
        e = UtcTimestamp.create(end)

        if allow_empty:
            if s.value > e.value:
                raise ValueError("UtcDateTimeRange.start cannot be after end")
        elif s.value >= e.value:
            raise ValueError("UtcDateTimeRange must be non-empty (start < end)")

        obj = cls(start=s, end=e)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.start, UtcTimestamp):
            raise TypeError("UtcDateTimeRange.start must be UtcTimestamp")
        if not isinstance(self.end, UtcTimestamp):
            raise TypeError("UtcDateTimeRange.end must be UtcTimestamp")
        # Defensive: validate wrapped datetimes even if UtcTimestamp was direct-constructed.
        _ = UtcTimestamp.create(self.start.value)
        _ = UtcTimestamp.create(self.end.value)

    def contains_instant(self, instant: datetime | UtcTimestamp) -> bool:
        t = UtcTimestamp.create(instant).value
        return self.start.value <= t < self.end.value  # [start, end)

    def overlaps(self, other: UtcDateTimeRange) -> bool:
        # Overlap for half-open intervals
        return self.start.value < other.end.value and other.start.value < self.end.value

    def intersection(self, other: UtcDateTimeRange) -> UtcDateTimeRange | None:
        if not self.overlaps(other):
            return None
        s = max(self.start.value, other.start.value)
        e = min(self.end.value, other.end.value)
        # intersection can be empty only if touching; for half-open, touching => empty
        if s >= e:
            return None
        return UtcDateTimeRange.create(s, e)

    def duration(self) -> timedelta:
        return self.end.value - self.start.value

    @property
    def seconds(self) -> int:
        return int(self.duration().total_seconds())

    def to_iso(self) -> tuple[str, str]:
        return (self.start.to_iso(), self.end.to_iso())

    def __str__(self) -> str:
        return f"[{self.start.to_iso()}, {self.end.to_iso()})"
