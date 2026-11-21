from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import ClassVar

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class Percentage(ValueObject):
    """Percentage value object in the closed interval [0, 100].

    Canonical form:
    - stored as Decimal percentage units
      example: 19 -> 19%
    - quantized to SCALE using ROUNDING

    Notes:
    - float inputs are rejected to avoid binary floating-point surprises
    - bool inputs are rejected explicitly because bool is a subclass of int
    - suitable for revenue shares, discounts, commissions, allocations, etc.

    """

    value: Decimal

    _VO_NAME: ClassVar[str] = "Percentage"

    MIN: ClassVar[Decimal] = Decimal(0)
    MAX: ClassVar[Decimal] = Decimal(100)
    HUNDRED: ClassVar[Decimal] = Decimal(100)

    # 4 dp is a good default for money percentages
    SCALE: ClassVar[Decimal] = Decimal("0.0001")
    ROUNDING: ClassVar[str] = ROUND_HALF_EVEN

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(cls, raw: Decimal | str | int) -> Percentage:
        if isinstance(raw, bool):
            raise TypeError(f"{cls._VO_NAME}.create does not accept bool")

        if isinstance(raw, float):
            raise TypeError(f"{cls._VO_NAME}.create does not accept float")

        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"{cls._VO_NAME} invalid value") from exc

        if value.is_nan() or value.is_infinite():
            raise ValueError(f"{cls._VO_NAME} cannot be NaN or infinite")

        if value < cls.MIN:
            raise ValueError(f"{cls._VO_NAME} cannot be negative")

        if value > cls.MAX:
            raise ValueError(f"{cls._VO_NAME} cannot exceed 100")

        value = value.quantize(cls.SCALE, rounding=cls.ROUNDING)

        # Canonicalize -0.0000 -> 0.0000
        if value == cls.MIN:
            value = cls.MIN.quantize(cls.SCALE, rounding=cls.ROUNDING)

        return cls(value=value)

    @classmethod
    def zero(cls) -> Percentage:
        return cls.create("0")

    @classmethod
    def one_hundred(cls) -> Percentage:
        return cls.create("100")

    def _enforce_types(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(f"{self._VO_NAME}.value must be Decimal")

        if self.value.is_nan() or self.value.is_infinite():
            raise ValueError(f"{self._VO_NAME}.value cannot be NaN or infinite")

        if self.value < self.MIN or self.value > self.MAX:
            raise ValueError(f"{self._VO_NAME} must be in [{self.MIN}, {self.MAX}]")

        canonical = self.value.quantize(self.SCALE, rounding=self.ROUNDING)
        if self.value != canonical:
            raise ValueError(f"{self._VO_NAME}.value must be quantized to scale {self.SCALE}")

    def is_zero(self) -> bool:
        return self.value == self.MIN

    def is_one_hundred(self) -> bool:
        return self.value == self.MAX.quantize(self.SCALE, rounding=self.ROUNDING)

    def to_ratio(self) -> Decimal:
        """15% -> 0.15

        Ratio is quantized to the same SCALE for deterministic downstream math.
        """
        return (self.value / self.HUNDRED).quantize(self.SCALE, rounding=self.ROUNDING)

    def to_plain(self) -> dict[str, str]:
        return {"value": str(self.value)}

    @classmethod
    def from_plain(cls, payload: object) -> Percentage:
        if not isinstance(payload, Mapping):
            raise ValueError("Percentage payload must be a JSON object")
        if set(payload) != {"value"} or any(not isinstance(key, str) for key in payload):
            raise ValueError("Percentage payload keys invalid")
        value = payload["value"]
        if not isinstance(value, str):
            raise TypeError("Percentage.value must be str")
        return cls.create(value)

    def apply_to(
        self,
        amount: Decimal,
        *,
        scale: Decimal = Decimal("0.01"),
        rounding: str = ROUND_HALF_UP,
    ) -> Decimal:
        """Returns this percentage applied to the given amount.

        Example:
            Percentage.create("19").apply_to(Decimal("1000")) -> Decimal("190.00")

        Rounds exactly once, at ``scale``. We deliberately multiply by the full-
        precision ``value / HUNDRED`` rather than the pre-quantized ``to_ratio()``:
        rounding the ratio first (to 4 dp) and only then scaling up by the amount
        amplifies the truncation into a material monetary drift for fractional
        rates over large bases. ``to_ratio()`` keeps its quantized
        contract for divisor/display use.

        Default rounding is ROUND_HALF_UP — the house *money* convention — because
        every caller applies a percentage to a money amount (revenue shares, discounts). A
        banker's-rounding default (ROUND_HALF_EVEN) was a footgun: a caller that
        omitted ``rounding`` diverged by a minor unit from the rest of the system
        at a ``.5`` quantum boundary (the discount path hit exactly this). The
        class-level ``ROUNDING`` (HALF_EVEN) stays the default only for
        ``to_ratio()``, which is a divisor/display helper, not a money result.

        """
        if isinstance(amount, bool):
            raise TypeError("Percentage.apply_to does not accept bool amount")
        if isinstance(amount, float):
            raise TypeError("Percentage.apply_to does not accept float amount")
        if not isinstance(amount, Decimal):
            raise TypeError("Percentage.apply_to amount must be Decimal")

        if amount.is_nan() or amount.is_infinite():
            raise ValueError("Percentage.apply_to amount cannot be NaN or infinite")

        return (amount * self.value / self.HUNDRED).quantize(scale, rounding=rounding)

    def __str__(self) -> str:
        return f"{self.value}%"
