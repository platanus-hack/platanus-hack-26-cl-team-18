from decimal import Decimal

import pytest

from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate


def test_a_rate_holds_sub_peso_precision_that_money_cannot() -> None:
    with pytest.raises(ValueError):
        Money.create(currency="CLP", amount=Decimal("4.3"))
    assert MoneyRate.create(currency="CLP", amount="4.3").amount == Decimal("4.3000")


def test_rates_quantize_half_even_to_four_places() -> None:
    assert MoneyRate.create(currency="CLP", amount="1.00005").amount == Decimal("1.0000")
    assert MoneyRate.create(currency="CLP", amount="1.00015").amount == Decimal("1.0002")


def test_a_charge_rounds_once_half_even_to_the_currency_quantum() -> None:
    rate = MoneyRate.create(currency="CLP", amount="2.275")
    assert rate.total_for(Decimal("7.4")) == Money.create(currency="CLP", amount=17)  # 16.835
    assert rate.total_for(0) == Money.create(currency="CLP", amount=0)  # nobody looked: $0
    assert MoneyRate.create(currency="CLP", amount="0.5").total_for(5) == Money.create(
        currency="CLP", amount=2
    )  # 2.5 → 2 (half-even)


def test_rates_reject_negatives_floats_and_extra_precision() -> None:
    with pytest.raises(ValueError):
        MoneyRate.create(currency="CLP", amount=-1)
    with pytest.raises(ValueError):
        MoneyRate.create(currency="CLP", amount=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MoneyRate.create(currency="CLP", amount="1").total_for(-1)


def test_scaling_keeps_the_currency() -> None:
    rate = MoneyRate.create(currency="USD", amount="0.12") * Decimal("1.5")
    assert rate == MoneyRate.create(currency="USD", amount="0.18")
