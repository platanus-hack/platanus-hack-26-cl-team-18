"""Shared UTC timestamp value object for domain-level instant invariants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class UtcTimestamp(ValueObject):
    """UTC instant value object.

    Architectural intent:
    - Centralize timezone-aware datetime invariants for domain instants.
    - Prevent primitive obsession by wrapping raw datetimes in a shared VO.
    - Keep all instant normalization deterministic at creation time.
    """

    value: datetime

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(cls, value: datetime | UtcTimestamp) -> UtcTimestamp:
        """Create a UTC timestamp from a datetime or existing VO."""
        if isinstance(value, UtcTimestamp):
            return value

        if not isinstance(value, datetime):
            raise TypeError("UtcTimestamp value must be datetime")

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UtcTimestamp must be timezone-aware")
        if value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("UtcTimestamp must be UTC (offset +00:00)")

        obj = cls(value=value.astimezone(UTC))
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        """Enforce direct-construction invariants for datetime and UTC offset."""
        if not isinstance(self.value, datetime):
            raise TypeError("UtcTimestamp.value must be datetime")
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("UtcTimestamp.value must be timezone-aware")
        if self.value.utcoffset() != UTC.utcoffset(self.value):
            raise ValueError("UtcTimestamp.value must be normalized to UTC")

    def to_iso(self) -> str:
        """Return an ISO-8601 representation of the normalized UTC instant."""
        return self.value.isoformat()

    def __str__(self) -> str:
        return self.value.isoformat()
