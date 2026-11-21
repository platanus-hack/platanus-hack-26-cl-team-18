from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
)
from functools import cache

from app.modules.common.domain.primitives.value_object import ValueObject

_ALLOWED_ROUNDING_MODES = frozenset(
    {
        ROUND_HALF_UP,
        ROUND_HALF_EVEN,
        ROUND_HALF_DOWN,
        ROUND_UP,
        ROUND_DOWN,
        ROUND_CEILING,
        ROUND_FLOOR,
        ROUND_05UP,
    },
)


@dataclass(frozen=True, slots=True)
class RoundingPolicy(ValueObject):
    money_rounding: str
    fx_rate_rounding: str
    fx_rate_scale: int

    @classmethod
    def create(
        cls,
        *,
        money_rounding: str = ROUND_HALF_UP,
        fx_rate_rounding: str = ROUND_HALF_EVEN,
        fx_rate_scale: int = 12,
    ) -> RoundingPolicy:
        return _rounding_policy_for(money_rounding, fx_rate_rounding, fx_rate_scale)

    @property
    def fx_rate_quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.fx_rate_scale)

    def _enforce_types(self) -> None:
        if not isinstance(self.money_rounding, str):
            raise TypeError("RoundingPolicy.money_rounding must be str")
        if self.money_rounding not in _ALLOWED_ROUNDING_MODES:
            raise ValueError("RoundingPolicy.money_rounding is not a valid decimal rounding mode")

        if not isinstance(self.fx_rate_rounding, str):
            raise TypeError("RoundingPolicy.fx_rate_rounding must be str")
        if self.fx_rate_rounding not in _ALLOWED_ROUNDING_MODES:
            raise ValueError("RoundingPolicy.fx_rate_rounding is not a valid decimal rounding mode")

        if not isinstance(self.fx_rate_scale, int):
            raise TypeError("RoundingPolicy.fx_rate_scale must be int")
        if self.fx_rate_scale < 0:
            raise ValueError("RoundingPolicy.fx_rate_scale must be >= 0")
        if self.fx_rate_scale > 18:
            raise ValueError("RoundingPolicy.fx_rate_scale must be <= 18")


@cache
def _rounding_policy_for(
    money_rounding: str,
    fx_rate_rounding: str,
    fx_rate_scale: int,
) -> RoundingPolicy:
    policy = RoundingPolicy(
        money_rounding=money_rounding,
        fx_rate_rounding=fx_rate_rounding,
        fx_rate_scale=fx_rate_scale,
    )
    policy._enforce_types()
    return policy
