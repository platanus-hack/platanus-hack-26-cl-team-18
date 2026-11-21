from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Self, TypeVar

from app.modules.common.domain.primitives.value_object import ValueObject

T = TypeVar("T", bound="StrName")


@dataclass(frozen=True, slots=True)
class StrName(ValueObject):
    """Base Value Object for human-readable string names.

    Construction MUST go through create().
    """

    value: str

    MIN_LENGTH: ClassVar[int] = 0
    MAX_LENGTH: ClassVar[int] = 255

    @classmethod
    def create(cls, value: str) -> Self:
        if value is None:
            raise TypeError(f"{cls.__name__} cannot be None")

        v = value.strip()
        length = len(v)

        if length < cls.MIN_LENGTH:
            raise ValueError(f"{cls.__name__} must be at least {cls.MIN_LENGTH} characters")

        if length > cls.MAX_LENGTH:
            raise ValueError(f"{cls.__name__} must be at most {cls.MAX_LENGTH} characters")

        obj = cls(value=v)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(f"{self.__class__.__name__}.value must be str")

    def __str__(self) -> str:
        return self.value
