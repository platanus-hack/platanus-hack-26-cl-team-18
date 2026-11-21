from __future__ import annotations

import pytest

from app.modules.common.domain.value_objects.address import Address
from app.modules.common.domain.value_objects.country_code import CountryCode
from app.modules.common.domain.value_objects.name import StrName


def _base_kwargs() -> dict[str, str]:
    return {
        "line1": "Av. Providencia 1234",
        "city": "Santiago",
        "postal_code": "7500000",
        "country": "CL",
    }


def test_comuna_defaults_to_none() -> None:
    address = Address.create(**_base_kwargs())
    assert address.comuna is None


def test_comuna_is_normalized_to_strname() -> None:
    address = Address.create(**_base_kwargs(), comuna="  Providencia  ")
    assert address.comuna == StrName.create("Providencia")


def test_comuna_accepts_already_built_strname() -> None:
    comuna = StrName.create("Las Condes")
    address = Address.create(**_base_kwargs(), comuna=comuna)
    assert address.comuna is comuna


def test_comuna_participates_in_equality() -> None:
    with_comuna = Address.create(**_base_kwargs(), comuna="Providencia")
    without_comuna = Address.create(**_base_kwargs())
    assert with_comuna != without_comuna


def test_enforce_types_rejects_non_strname_comuna() -> None:
    # Direct construction is unguarded by contract; _enforce_types() is the
    # documented invariant check and must reject a raw-str comuna.
    address = Address(
        line1=StrName.create("Av. Providencia 1234"),
        line2=None,
        city=StrName.create("Santiago"),
        state=None,
        postal_code=StrName.create("7500000"),
        country=CountryCode.create("CL"),
        comuna="raw-str",  # type: ignore[arg-type]
    )
    with pytest.raises(TypeError):
        address._enforce_types()
