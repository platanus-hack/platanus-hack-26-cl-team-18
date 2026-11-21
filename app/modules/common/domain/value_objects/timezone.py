from __future__ import annotations

from dataclasses import dataclass

from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.timezone.enum import TimeZoneEnum

# Asumiendo que estos existen en tu estructura de módulos
# from app.modules.common.domain.timezone.timezone_enum import TimeZoneEnum


@dataclass(frozen=True, slots=True)
class TimeZone(ValueObject):
    value: TimeZoneEnum

    # ---------- factories ----------

    @classmethod
    def create(
        cls,
        value: TimeZone | TimeZoneEnum | str,
    ) -> TimeZone:
        if isinstance(value, TimeZone):
            return value

        if isinstance(value, TimeZoneEnum):
            obj = cls(value)
            obj._enforce_types()
            return obj

        if isinstance(value, str):
            try:
                # Se normaliza para coincidir con los miembros del Enum
                obj = cls(TimeZoneEnum(value.strip()))
                obj._enforce_types()
                return obj
            except ValueError as exc:
                raise ValueError(f"Invalid time zone: {value!r}") from exc

        raise TypeError("Time zone must be TimeZone, TimeZoneEnum, or str")

    def __str__(self) -> str:
        return self.value.value

    def _enforce_types(self) -> None:
        if not isinstance(self.value, TimeZoneEnum):
            raise TypeError("TimeZone.value must be TimeZoneEnum; use TimeZone.create")
