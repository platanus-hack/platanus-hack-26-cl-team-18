from __future__ import annotations

from dataclasses import dataclass

from app.modules.common.domain.country.catalog import COUNTRY_DATA
from app.modules.common.domain.country.enum import CountryEnum
from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class CountryCode(ValueObject):
    value: CountryEnum

    # ---------- factories ----------

    @classmethod
    def create(
        cls,
        value: CountryCode | CountryEnum | str,
    ) -> CountryCode:
        if isinstance(value, CountryCode):
            return value

        if isinstance(value, CountryEnum):
            obj = cls(value)
            obj._enforce_types()
            if obj.value not in COUNTRY_DATA:
                raise ValueError(f"Unsupported country code: {obj.value}")
            return obj

        if isinstance(value, str):
            try:
                obj = cls(CountryEnum(value.strip().upper()))
                obj._enforce_types()
                if obj.value not in COUNTRY_DATA:
                    raise ValueError(f"Unsupported country code: {obj.value}")
                return obj
            except ValueError as exc:
                raise ValueError(f"Invalid country code: {value!r}") from exc

        raise TypeError("Country code must be CountryCode, CountryEnum, or str")

    # ---------- derived data ----------

    @property
    def name(self) -> str:
        # Canonical country name (e.g. "Chile")
        return COUNTRY_DATA[self.value]

    def __str__(self) -> str:
        return self.value.value

    def to_plain(self) -> str:
        return self.value.value

    @classmethod
    def from_plain(cls, payload: object) -> CountryCode:
        if not isinstance(payload, str):
            raise TypeError("CountryCode payload must be str")
        return cls.create(payload)

    def _enforce_types(self) -> None:
        if not isinstance(self.value, CountryEnum):
            raise TypeError("CountryCode.value must be CountryEnum; use CountryCode.create")
