from decimal import Decimal

import pytest

from app.modules.common.domain.value_objects.quantity import Quantity


def test_quantity_preserves_fractional_precision() -> None:
    quantity = Quantity.create("2.5")

    assert quantity.value == Decimal("2.5")
    assert quantity.is_positive


def test_quantity_direct_construction_enforces_invariants() -> None:
    with pytest.raises(ValueError):
        Quantity(Decimal("-1"))


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_quantity_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        Quantity.create(value)


@pytest.mark.parametrize("value", [True, 1.5])
def test_quantity_rejects_lossy_input_types(value: object) -> None:
    with pytest.raises(TypeError):
        Quantity.create(value)  # type: ignore[arg-type]
