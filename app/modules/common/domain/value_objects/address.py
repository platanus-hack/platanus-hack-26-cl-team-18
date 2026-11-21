from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.country_code import CountryCode
from app.modules.common.domain.value_objects.name import StrName
from app.modules.common.domain.value_objects.plain_payload import require_plain_str


@dataclass(frozen=True, slots=True)
class Address(ValueObject):
    """Postal address value object.

    Uses CountryCode VO for strong typing.
    """

    _VO_NAME: ClassVar[str] = "Address"

    line1: StrName
    line2: StrName | None
    city: StrName
    state: StrName | None
    postal_code: StrName | None
    country: CountryCode
    # Chilean administrative subdivision (*comuna*) — a genuine component of a
    # postal address in Chile.
    # Optional so addresses outside Chile remain valid.
    comuna: StrName | None = None

    # -------------------------
    # Factory
    # -------------------------
    @classmethod
    def create(
        cls,
        *,
        line1: StrName | str,
        city: StrName | str,
        country: CountryCode | str,
        postal_code: StrName | str | None = None,
        line2: StrName | str | None = None,
        state: StrName | str | None = None,
        comuna: StrName | str | None = None,
    ) -> Address:
        obj = cls(
            line1=StrName.create(line1) if not isinstance(line1, StrName) else line1,
            line2=StrName.create(line2)
            if line2 is not None and not isinstance(line2, StrName)
            else line2,
            city=StrName.create(city) if not isinstance(city, StrName) else city,
            state=StrName.create(state)
            if state is not None and not isinstance(state, StrName)
            else state,
            postal_code=StrName.create(postal_code)
            if postal_code is not None and not isinstance(postal_code, StrName)
            else postal_code,
            country=CountryCode.create(country),
            comuna=StrName.create(comuna)
            if comuna is not None and not isinstance(comuna, StrName)
            else comuna,
        )

        obj._enforce_types()
        return obj

    # -------------------------
    # Invariants
    # -------------------------
    def _enforce_types(self) -> None:
        if not isinstance(self.line1, StrName):
            raise TypeError("line1 must be StrName")

        if self.line2 is not None:
            if not isinstance(self.line2, StrName):
                raise TypeError("line2 must be StrName or None")

        if not isinstance(self.city, StrName):
            raise TypeError("city must be StrName")

        if self.state is not None:
            if not isinstance(self.state, StrName):
                raise TypeError("state must be StrName or None")

        if self.postal_code is not None and not isinstance(self.postal_code, StrName):
            raise TypeError("postal_code must be StrName or None")

        if not isinstance(self.country, CountryCode):
            raise TypeError("country must be CountryCode")

        if self.comuna is not None:
            if not isinstance(self.comuna, StrName):
                raise TypeError("comuna must be StrName or None")

    def to_plain(self) -> dict[str, object]:
        return {
            "line1": self.line1.value,
            "line2": self.line2.value if self.line2 is not None else None,
            "city": self.city.value,
            "state": self.state.value if self.state is not None else None,
            "postal_code": self.postal_code.value if self.postal_code is not None else None,
            "country": self.country.to_plain(),
            "comuna": self.comuna.value if self.comuna is not None else None,
        }

    @classmethod
    def from_plain(cls, payload: object) -> Address:
        if not isinstance(payload, Mapping) or any(not isinstance(key, str) for key in payload):
            raise ValueError("Address payload must be a JSON object")
        expected = {"line1", "line2", "city", "state", "postal_code", "country", "comuna"}
        if set(payload) != expected:
            raise ValueError("Address payload keys invalid")
        return cls.create(
            line1=require_plain_str(payload["line1"], field="Address.line1"),
            line2=_optional_string(payload["line2"], field="Address.line2"),
            city=require_plain_str(payload["city"], field="Address.city"),
            state=_optional_string(payload["state"], field="Address.state"),
            postal_code=_optional_string(payload["postal_code"], field="Address.postal_code"),
            country=CountryCode.from_plain(payload["country"]),
            comuna=_optional_string(payload["comuna"], field="Address.comuna"),
        )


def _optional_string(payload: object, *, field: str) -> str | None:
    if payload is None:
        return None
    return require_plain_str(payload, field=field)
