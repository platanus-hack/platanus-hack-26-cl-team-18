"""Money refuses amounts no money column can hold and has one form of zero.

* ``Money.create(amount="1E+100000")`` raised ``decimal.InvalidOperation`` from
  ``quantize`` — an ``ArithmeticError``, so every route mapping ``ValueError`` to
  422 answered 500. Amounts that fit Decimal but not ``numeric(22,6)`` were
  accepted and only failed at INSERT.
* ``-Money`` of zero, ``Money * 0`` of a negative and ``-0.004`` rounded away all
  produced ``Decimal("-0.00")``, which is ``== 0`` yet serialises as ``"-0.00"``
  onto ledger rows, payloads and idempotency fingerprints.
"""

from __future__ import annotations

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
    localcontext,
)

import pytest

from app.modules.common.domain.currency.enum import CurrencyCodeEnum
from app.modules.common.domain.value_objects.currency_code import CurrencyCode
from app.modules.common.domain.value_objects.money import (
    MONEY_MAX_INTEGER_DIGITS,
    Money,
    parse_money_amount,
)
from app.modules.common.domain.value_objects.rounding_policy import RoundingPolicy

USD = CurrencyCode.create("USD")


@pytest.mark.parametrize(
    "amount",
    [
        "1E+100000",
        "100000000000000000000000000000",
        Decimal(10) ** MONEY_MAX_INTEGER_DIGITS,
        -(Decimal(10) ** MONEY_MAX_INTEGER_DIGITS),
        "NaN",
        "Infinity",
        "-Infinity",
        "abc",
        "",
    ],
)
def test_unrepresentable_amounts_are_value_errors(amount: object) -> None:
    with pytest.raises(ValueError):
        Money.create(currency=USD, amount=amount)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        parse_money_amount(amount)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "amount",
    ["9999999999999999.99", "-9999999999999999.99", "0E+100", "0.01"],
)
def test_amounts_within_the_column_are_accepted(amount: str) -> None:
    Money.create(currency=USD, amount=amount)


@pytest.mark.parametrize(
    "money",
    [
        pytest.param(lambda: Money.create(currency=USD, amount="-0"), id="literal -0"),
        pytest.param(lambda: Money.create(currency=USD, amount="-0.00"), id="literal -0.00"),
        pytest.param(
            lambda: Money.create(currency=USD, amount="-0.004", rounded=True), id="rounds to -0"
        ),
        pytest.param(lambda: -Money.zero(currency=USD), id="negated zero"),
        pytest.param(lambda: Money.create(currency=USD, amount="-1.00") * 0, id="negative * 0"),
    ],
)
def test_zero_has_a_single_unsigned_form(money: object) -> None:
    result = money()  # type: ignore[operator]
    assert str(result.amount) == "0.00"
    assert not result.amount.is_signed()


# validate the money result after rounding, not only its input.
@pytest.mark.parametrize("currency", ["CLP", "USD", "CLF"])
@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("precision", [28, 50])
@pytest.mark.parametrize(
    "mode",
    [
        ROUND_HALF_UP,
        ROUND_HALF_EVEN,
        ROUND_HALF_DOWN,
        ROUND_UP,
        ROUND_DOWN,
        ROUND_CEILING,
        ROUND_FLOOR,
        ROUND_05UP,
    ],
)
def test_rounding_near_storage_limit_never_constructs_unstorable_money(
    currency: str,
    sign: int,
    precision: int,
    mode: str,
) -> None:
    cc = CurrencyCode.create(currency)
    bound = Decimal(10) ** MONEY_MAX_INTEGER_DIGITS
    with localcontext() as context:
        context.prec = precision
        amount = sign * (bound - cc.quantum / 2)
        expected = amount.quantize(cc.quantum, rounding=mode)
        if abs(expected) >= bound:
            with pytest.raises(ValueError, match="maximum"):
                Money.create(
                    currency=cc,
                    amount=amount,
                    rounded=True,
                    rounding_policy=RoundingPolicy.create(money_rounding=mode),
                )
        else:
            result = Money.create(
                currency=cc,
                amount=amount,
                rounded=True,
                rounding_policy=RoundingPolicy.create(money_rounding=mode),
            )
            assert result.amount == expected
            assert Money.from_plain(result.to_plain()) == result


@pytest.mark.parametrize("amount", ["1E+100000", "10000000000000000", "-10000000000000000"])
def test_database_factory_enforces_the_same_storage_bound(amount: str) -> None:
    with pytest.raises(ValueError, match="maximum"):
        Money.from_db(currency=CurrencyCodeEnum.USD, amount=Decimal(amount))


@pytest.mark.parametrize(
    "currency", [CurrencyCodeEnum.CLP, CurrencyCodeEnum.USD, CurrencyCodeEnum.CLF]
)
def test_database_zero_uses_the_same_canonical_form(currency: CurrencyCodeEnum) -> None:
    result = Money.from_db(currency=currency, amount=Decimal("-0.000000"))
    assert not result.amount.is_signed()
    assert result == Money.zero(currency=currency)


@pytest.mark.parametrize("sign", [1, -1])
def test_arithmetic_cannot_round_across_the_storage_limit(sign: int) -> None:
    money = Money.create(currency=USD, amount=sign * Decimal("9999999999999999.99"))
    with pytest.raises(ValueError, match="maximum"):
        money / Decimal("0.9999999999999999994")


# reject floats before precision loss is concealed.
@pytest.mark.parametrize("rounded", [False, True])
@pytest.mark.parametrize("amount", [0.0, 0.1, -0.1, 12.34, 0.1 + 0.2, 9007199254740993.0])
def test_money_rejects_binary_floats_before_any_rounding(amount: float, rounded: bool) -> None:
    with pytest.raises(ValueError, match="decimal number"):
        parse_money_amount(amount)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="decimal number"):
        Money.create(currency=USD, amount=amount, rounded=rounded)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "amount", [9007199254740993, "9007199254740993", Decimal("9007199254740993")]
)
def test_exact_inputs_above_binary_float_integer_precision_keep_every_peso(
    amount: Decimal | int | str,
) -> None:
    result = Money.create(currency="CLP", amount=amount)
    assert result.amount == Decimal("9007199254740993")
    assert result.to_minor_units() == 9007199254740993
