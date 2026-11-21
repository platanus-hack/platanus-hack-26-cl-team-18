from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class DedupeKey(ValueObject):
    """Normalized key used to identify potentially duplicated records."""

    value: str

    MIN_LENGTH: ClassVar[int] = 1
    MAX_LENGTH: ClassVar[int] = 128

    @classmethod
    def create(cls, raw: str) -> DedupeKey:
        if raw is None:
            raise ValueError("DedupeKey value is required")
        if not isinstance(raw, str):
            raise ValueError("DedupeKey value must be str")

        normalized = raw.strip()
        if not normalized:
            raise ValueError("DedupeKey value cannot be blank")
        if not (cls.MIN_LENGTH <= len(normalized) <= cls.MAX_LENGTH):
            raise ValueError(
                f"DedupeKey length must be between {cls.MIN_LENGTH} and {cls.MAX_LENGTH} characters",
            )

        obj = cls(value=normalized)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.value, str):
            raise ValueError("DedupeKey.value must be str")
        if not self.value:
            raise ValueError("DedupeKey.value cannot be blank")
        if self.value != self.value.strip():
            raise ValueError("DedupeKey.value must be normalized (trimmed)")
        if not (self.MIN_LENGTH <= len(self.value) <= self.MAX_LENGTH):
            raise ValueError(
                f"DedupeKey length must be between {self.MIN_LENGTH} and {self.MAX_LENGTH} characters",
            )

    def __str__(self) -> str:
        return self.value
