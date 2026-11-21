from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import cache

from app.modules.common.domain.currency.catalog import CURRENCY_DATA
from app.modules.common.domain.currency.enum import CurrencyCodeEnum
from app.modules.common.domain.primitives.value_object import ValueObject

CURRENCY_DECIMALS: dict[CurrencyCodeEnum, int] = {
    CurrencyCodeEnum(code): decimals for code, (_, _, decimals) in CURRENCY_DATA.items()
}
CURRENCY_QUANTA: dict[CurrencyCodeEnum, Decimal] = {
    code: Decimal(1).scaleb(-decimals) for code, decimals in CURRENCY_DECIMALS.items()
}


@dataclass(frozen=True, slots=True)
class CurrencyCode(ValueObject):
    value: CurrencyCodeEnum

    @classmethod
    def create(cls, value: CurrencyCodeEnum | str) -> CurrencyCode:
        return _currency_code_for(cls._normalize_currency_code(value))

    @classmethod
    def _normalize_currency_code(cls, value: CurrencyCodeEnum | str) -> CurrencyCodeEnum:
        if isinstance(value, CurrencyCodeEnum):
            return value

        if isinstance(value, str):
            try:
                return CurrencyCodeEnum(value.strip().upper())
            except ValueError as exc:
                raise ValueError(f"Invalid currency code: {value!r}") from exc

        raise TypeError("Currency code must be CurrencyCodeEnum or str")

    def __str__(self) -> str:
        return self.value.value

    @property
    def decimals(self) -> int:
        try:
            return CURRENCY_DECIMALS[self.value]
        except KeyError as exc:
            raise ValueError(f"Unsupported currency code: {self.value}") from exc

    @property
    def quantum(self) -> Decimal:
        try:
            return CURRENCY_QUANTA[self.value]
        except KeyError as exc:
            raise ValueError(f"Unsupported currency code: {self.value}") from exc

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CurrencyCode):
            return self.value == other.value
        if isinstance(other, CurrencyCodeEnum):
            return self.value == other
        if isinstance(other, str):
            return self.value.value == other.strip().upper()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def _enforce_types(self) -> None:
        if not isinstance(self.value, CurrencyCodeEnum):
            raise TypeError("CurrencyCode.value must be CurrencyCodeEnum; use CurrencyCode.create")


@cache
def _currency_code_for(value: CurrencyCodeEnum) -> CurrencyCode:
    obj = CurrencyCode(value)
    obj._enforce_types()
    if obj.value not in CURRENCY_DECIMALS:
        raise ValueError(f"Unsupported currency code: {obj.value}")
    return obj
