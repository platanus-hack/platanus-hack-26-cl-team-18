from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import ClassVar

from app.modules.common.domain.currency.enum import CurrencyCodeEnum
from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.currency_code import CurrencyCode
from app.modules.common.domain.value_objects.money import Money, parse_money_amount
from app.modules.common.domain.value_objects.rounding_policy import RoundingPolicy


@dataclass(frozen=True, slots=True)
class MoneyRate(ValueObject):
    """Money per unit (e.g. per attention-second) at sub-currency precision.

    A rate is not a charge. CLP ``Money`` has zero decimals, so a bid of
    CLP 4.3 per attention-second cannot be ``Money``. Rates keep ``SCALE``
    (4 dp) and become ``Money`` only through ``total_for``, which rounds once,
    half-even, to the currency quantum at the ledger boundary.
    """

    currency: CurrencyCode
    amount: Decimal

    SCALE: ClassVar[Decimal] = Decimal("0.0001")
    _VO_NAME: ClassVar[str] = "MoneyRate"

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(
        cls, *, currency: CurrencyCode | CurrencyCodeEnum | str, amount: Decimal | int | str
    ) -> MoneyRate:
        code = currency if isinstance(currency, CurrencyCode) else CurrencyCode.create(currency)
        value = parse_money_amount(amount).quantize(cls.SCALE, rounding=ROUND_HALF_EVEN)
        return cls(currency=code, amount=value)

    @classmethod
    def zero(cls, currency: CurrencyCode | CurrencyCodeEnum | str) -> MoneyRate:
        return cls.create(currency=currency, amount=0)

    def __mul__(self, factor: Decimal | int) -> MoneyRate:
        if isinstance(factor, bool) or not isinstance(factor, Decimal | int):
            raise TypeError("MoneyRate can only be multiplied by Decimal or int")
        return MoneyRate.create(currency=self.currency, amount=self.amount * factor)

    __rmul__ = __mul__

    def total_for(self, quantity: Decimal | int) -> Money:
        """Charge for ``quantity`` units, rounded half-even to the currency quantum."""
        if isinstance(quantity, bool) or not isinstance(quantity, Decimal | int):
            raise TypeError("quantity must be Decimal or int")
        if quantity < 0:
            raise ValueError("quantity cannot be negative")
        return Money.create(
            currency=self.currency,
            amount=self.amount * quantity,
            rounded=True,
            rounding_policy=RoundingPolicy.create(money_rounding=ROUND_HALF_EVEN),
        )

    def _enforce_types(self) -> None:
        if not isinstance(self.currency, CurrencyCode):
            raise TypeError("MoneyRate.currency must be a CurrencyCode")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise TypeError("MoneyRate.amount must be a finite Decimal")
        if self.amount < 0:
            raise ValueError("MoneyRate cannot be negative")
        if self.amount != self.amount.quantize(self.SCALE):
            raise ValueError("MoneyRate.amount exceeds 4 decimal places")

    def __str__(self) -> str:
        return f"{self.currency} {self.amount}"
