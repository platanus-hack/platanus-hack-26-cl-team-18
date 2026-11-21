"""A finite, non-negative decimal count of billable units."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class Quantity(ValueObject):
    """A quantity with no implicit rounding or unit of measure.

    Quantities may be fractional and zero. The owning model decides whether a
    particular use requires a strictly positive value.
    """

    value: Decimal

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(cls, value: Decimal | int | str) -> Quantity:
        if isinstance(value, (bool, float)):
            raise TypeError("Quantity.create accepts Decimal, int, or str")
        try:
            normalized = value if isinstance(value, Decimal) else Decimal(value)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Quantity must be decimal-coercible") from exc
        return cls(value=normalized)

    def _enforce_types(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("Quantity.value must be Decimal")
        if not self.value.is_finite():
            raise ValueError("Quantity.value must be finite")
        if self.value < 0:
            raise ValueError("Quantity.value must not be negative")

    @property
    def is_positive(self) -> bool:
        return self.value > 0

    def to_plain(self) -> dict[str, str]:
        return {"value": str(self.value)}

    @classmethod
    def from_plain(cls, payload: object) -> Quantity:
        if not isinstance(payload, Mapping):
            raise ValueError("Quantity payload must be a JSON object")
        if set(payload) != {"value"} or any(not isinstance(key, str) for key in payload):
            raise ValueError("Quantity payload keys invalid")
        value = payload["value"]
        if not isinstance(value, str):
            raise TypeError("Quantity.value must be str")
        return cls.create(value)

    def __str__(self) -> str:
        return str(self.value)
