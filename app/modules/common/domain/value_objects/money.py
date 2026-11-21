from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.modules.common.domain.currency.catalog import CURRENCY_DATA
from app.modules.common.domain.currency.enum import CurrencyCodeEnum
from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.currency_code import CurrencyCode
from app.modules.common.domain.value_objects.rounding_policy import RoundingPolicy

# Money columns are ``numeric(22,6)``: at most 16 digits before the decimal
# point. An amount at or beyond 10**16 can never be stored, and past ~10**26 the
# quantize step itself overflows the 28-digit decimal context and raises
# ``decimal.InvalidOperation`` — an ``ArithmeticError``, not a ``ValueError``, so
# callers that map bad input to 422 let it through as a 500. Money rejects such
# amounts at construction, as the same ``ValueError`` as any other invalid
# amount.
MONEY_MAX_INTEGER_DIGITS = 16


def parse_money_amount(value: Decimal | int | str) -> Decimal:
    """Parse a money amount and bound it to what a money column can hold.

    The currency-agnostic half of ``Money.create``: rejects non-numbers,
    NaN/Infinity and amounts of ``MONEY_MAX_INTEGER_DIGITS`` or more integer
    digits, all as ``ValueError``. Command layers that carry a bare ``Decimal``
    before the currency is known (e.g. a bid ceiling parsed before its campaign's currency is resolved) use it so an
    unrepresentable amount is refused at the boundary instead of deep inside a
    handler. Currency precision is still enforced by ``Money.create``.
    """
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int) and not isinstance(value, bool):
        amount = Decimal(value)
    elif isinstance(value, str):
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Amount must be a decimal number") from exc
    else:
        # Stringifying a float conceals precision already lost at the boundary.
        # Accept only the exact representations declared by this parser.
        raise ValueError("Amount must be a decimal number (Decimal, int or str)")
    if not amount.is_finite():
        raise ValueError("Amount must be a finite decimal")
    if amount and amount.adjusted() >= MONEY_MAX_INTEGER_DIGITS:
        raise ValueError(f"Amount exceeds the maximum of {MONEY_MAX_INTEGER_DIGITS} integer digits")
    return amount


@dataclass(frozen=True, slots=True)
class Money(ValueObject):
    """Canonical money value object used across the domain.

    Design intent:
    - Domain stores money in major units as `Decimal` (`amount`), never float.
    - Currency precision is enforced by `CurrencyCode.quantum`.
    - Boundaries (APIs/providers) may speak minor units (e.g., cents), and
      should convert through `from_minor_units` / `to_minor_units`.

    Boundary guidance:
    - API payload uses major units (e.g., "12.34"): call `Money.create(...)`.
    - Provider payload uses minor units (e.g., 1234 cents): call
      `Money.from_minor_units(...)`.
    - Sending money to an integration that expects minor units: call
      `money.to_minor_units()`.
    """

    currency: CurrencyCode
    amount: Decimal

    # ---------- factories ----------

    @classmethod
    def create(
        cls,
        *,
        currency: CurrencyCode | CurrencyCodeEnum | str,
        amount: Decimal | int | str,
        rounded: bool = False,
        rounding_policy: RoundingPolicy | None = None,
    ) -> Money:
        cc = cls._coerce_currency(currency)
        policy = rounding_policy or RoundingPolicy.create()
        amt = cls._normalize_amount(
            amount,
            cc,
            strict=not rounded,
            rounding=policy.money_rounding,
        )
        obj = cls(currency=cc, amount=amt)
        obj._enforce_types()
        return obj

    @classmethod
    def from_minor_units(
        cls,
        *,
        currency: CurrencyCode | CurrencyCodeEnum | str,
        minor: int,
    ) -> Money:
        """Build Money from integer minor units (e.g., cents).

        Examples:
        - USD minor=1234 -> amount=12.34
        - CLP minor=1234 -> amount=1234

        Intended use:
        - Application/API adapters that receive integration payloads in minor
          units should normalize here before constructing domain entities.

        """
        if isinstance(minor, bool) or not isinstance(minor, int):
            raise TypeError("minor must be int")

        cc = cls._coerce_currency(currency)
        amount = Decimal(minor).scaleb(-cc.decimals)
        return cls.create(currency=cc, amount=amount)

    @classmethod
    def from_db(
        cls,
        *,
        currency: CurrencyCodeEnum,
        amount: Decimal,
        rounding_policy: RoundingPolicy | None = None,
    ) -> Money:
        """For ORM / persistence: assumes DB stores (currency_code_enum, decimal).
        Enforces: finite decimal and already-quantized amount for that currency.
        """
        if not isinstance(currency, CurrencyCodeEnum):
            raise TypeError("currency must be CurrencyCodeEnum")
        if not isinstance(amount, Decimal):
            raise TypeError("amount must be Decimal")
        amount = parse_money_amount(amount)

        cc = CurrencyCode.create(currency)  # uses your unified normalization
        policy = rounding_policy or RoundingPolicy.create()
        quantized = amount.quantize(cc.quantum, rounding=policy.money_rounding)
        if quantized != amount:
            raise ValueError("Amount is not correctly quantized for currency")

        obj = cls(currency=cc, amount=quantized.copy_abs() if quantized.is_zero() else amount)
        obj._enforce_types()
        return obj

    @classmethod
    def zero(
        cls,
        *,
        currency: CurrencyCode | CurrencyCodeEnum | str,
    ) -> Money:
        return cls.create(currency=currency, amount=Decimal(0))

    # ---------- derived props ----------

    @property
    def decimals(self) -> int:
        return self.currency.decimals

    @property
    def quantum(self) -> Decimal:
        return self.currency.quantum

    def to_minor_units(self) -> int:
        """Convert Money to integer minor units (e.g., cents).

        Intended use:
        - Use when serializing domain values to external systems that require
          integer minor units.

        Raises:
        - ValueError if amount is not exactly representable in minor units.

        """
        # Defensive check: Money should already be quantized by construction.
        quantized = self.amount.quantize(self.currency.quantum)
        if quantized != self.amount:
            raise ValueError("Amount is not correctly quantized for currency")

        scaled = self.amount.scaleb(self.currency.decimals)
        if scaled != scaled.to_integral_value():
            raise ValueError("Amount cannot be represented as integer minor units")
        return int(scaled)

    def to_plain(self) -> dict[str, str]:
        """Canonical JSON/persistence shape for a money fact."""
        return {"currency": self.currency.value.value, "amount": str(self.amount)}

    @classmethod
    def from_plain(cls, payload: object) -> Money:
        """Strict inverse of ``to_plain``.

        Missing or malformed money fields are persistence corruption, not values
        the mapper may fabricate.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("Money payload must be a JSON object")
        _require_exact_keys(payload, {"currency", "amount"}, field="Money")
        currency = payload["currency"]
        amount = payload["amount"]
        if not isinstance(currency, str):
            raise TypeError("Money.currency must be str")
        if not isinstance(amount, str):
            raise TypeError("Money.amount must be str")
        return cls.create(currency=currency, amount=amount)

    # ---------- invariants ----------

    # ---------- arithmetic ----------

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return Money.create(currency=self.currency, amount=self.amount + other.amount)

    def __radd__(self, other: Any) -> Money:
        if other == 0:
            return self
        return NotImplemented

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return Money.create(currency=self.currency, amount=self.amount - other.amount)

    def __mul__(self, other: Decimal | int) -> Money:
        if isinstance(other, bool):
            return NotImplemented
        if isinstance(other, (int, Decimal)):
            # rounded=True: multiplication by a rate rarely produces an exact result at the
            # currency quantum; strict mode would raise ValueError on legitimate pricing math.
            return Money.create(
                currency=self.currency,
                amount=self.amount * Decimal(other),
                rounded=True,
            )
        return NotImplemented

    def __rmul__(self, other: Decimal | int) -> Money:
        return self.__mul__(other)

    def __truediv__(self, other: Decimal | int) -> Money:
        if isinstance(other, bool):
            return NotImplemented
        if isinstance(other, (int, Decimal)):
            d = Decimal(other)
            if d == 0:
                raise ZeroDivisionError("Cannot divide by zero")
            # rounded=True: division almost never produces an exact result at the
            # currency quantum; strict mode would raise ValueError on legitimate pricing math.
            return Money.create(currency=self.currency, amount=self.amount / d, rounded=True)
        return NotImplemented

    def __neg__(self) -> Money:
        return Money.create(currency=self.currency, amount=-self.amount)

    def __abs__(self) -> Money:
        return Money.create(currency=self.currency, amount=abs(self.amount))

    # ---------- comparisons ----------

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount >= other.amount

    # ---------- display ----------

    def __str__(self) -> str:
        return f"{self.currency.value.value} {self.amount}"

    def format(
        self,
        *,
        use_symbol: bool = True,
        symbol_position: str = "before",
        symbol_sep: str = "",
        thousands_sep: str = ",",
        decimal_sep: str = ".",
        force_decimals: int | None = None,
        code_fallback: bool = True,
        rounding_policy: RoundingPolicy | None = None,
    ) -> str:
        if symbol_position not in {"before", "after"}:
            raise ValueError("symbol_position must be 'before' or 'after'")

        code = self.currency.value.value  # CurrencyCodeEnum -> str
        _, symbol, _ = CURRENCY_DATA.get(code, (None, None, None))
        decimals = force_decimals if force_decimals is not None else self.decimals

        amount_str = _format_amount(
            self.amount,
            decimals=decimals,
            thousands_sep=thousands_sep,
            decimal_sep=decimal_sep,
            rounding=(rounding_policy or RoundingPolicy.create()).money_rounding,
        )

        if use_symbol and symbol:
            if symbol_position == "before":
                return f"{symbol}{symbol_sep}{amount_str}"
            return f"{amount_str}{symbol_sep}{symbol}"

        if code_fallback:
            return f"{code} {amount_str}"
        return amount_str

    # ---------- internals ----------
    @staticmethod
    def _coerce_currency(value: CurrencyCode | CurrencyCodeEnum | str) -> CurrencyCode:
        return value if isinstance(value, CurrencyCode) else CurrencyCode.create(value)

    @staticmethod
    def _normalize_amount(
        value: Decimal | int | str,
        currency: CurrencyCode,
        *,
        strict: bool = True,
        rounding: str = ROUND_HALF_UP,
    ) -> Decimal:
        amount = parse_money_amount(value)
        quantized = amount.quantize(currency.quantum, rounding=rounding)
        # Strict mode must reject values that would change when rounded to the
        # currency quantum, but still accept equivalent DB values like 1000.00
        # for zero-decimal currencies (CLP).
        if strict and quantized != amount:
            raise ValueError("Amount has too many decimal places for currency")

        # Zero has one canonical form. ``-0.01 * 0``, ``-(0)`` or a -0.004 that
        # rounds away all yield a signed zero, which compares equal to 0.00 but
        # serialises as "-0.00" — onto ledger rows, payloads and hashes.
        if quantized.is_zero():
            return quantized.copy_abs()
        # Rounding can carry an otherwise valid amount over the storage bound.
        # Validate the resulting money fact as well as the input.
        return parse_money_amount(quantized)

    def _ensure_same_currency(self, other: Money) -> None:
        if other.currency != self.currency:
            raise ValueError(
                f"Cannot operate on {self.currency.value.value} and {other.currency.value.value}",
            )

    def _enforce_types(self) -> None:
        if not isinstance(self.currency, CurrencyCode):
            raise TypeError("Money.currency must be CurrencyCode; use Money.create")
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be Decimal; use Money.create")


def _format_amount(
    amount: Decimal,
    *,
    decimals: int,
    thousands_sep: str,
    decimal_sep: str,
    rounding: str = ROUND_HALF_UP,
) -> str:
    quantizer = Decimal(1).scaleb(-decimals)
    quantized = amount.quantize(quantizer, rounding=rounding)
    text = format(abs(quantized), "f")

    if "." in text:
        integer, fraction = text.split(".", 1)
    else:
        integer, fraction = text, ""

    integer = _add_thousands_sep(integer, thousands_sep)

    if decimals > 0:
        text = f"{integer}{decimal_sep}{fraction.ljust(decimals, '0')}"
    else:
        text = integer

    return f"-{text}" if quantized < 0 else text


def _add_thousands_sep(value: str, sep: str) -> str:
    if not sep:
        return value
    parts: list[str] = []
    while value:
        parts.append(value[-3:])
        value = value[:-3]
    return sep.join(reversed(parts))


def _require_exact_keys(
    payload: Mapping[object, object], expected: set[str], *, field: str
) -> None:
    if any(not isinstance(key, str) for key in payload):
        raise ValueError(f"{field} payload keys must be strings")
    actual = {str(key) for key in payload}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{field} payload keys invalid; missing={missing}, extra={extra}")
