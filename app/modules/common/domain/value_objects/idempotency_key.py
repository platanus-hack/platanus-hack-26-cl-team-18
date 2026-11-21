from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from app.modules.common.domain.primitives.value_object import ValueObject


class IdempotencyKeyInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IdempotencyKey(ValueObject):
    _value: str

    _PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]+")
    MIN_LENGTH: ClassVar[int] = 8
    MAX_LENGTH: ClassVar[int] = 128

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(cls, raw_key: str) -> IdempotencyKey:
        if not isinstance(raw_key, str):
            raise IdempotencyKeyInvalid("Idempotency key must be a string.")

        raw_key = raw_key.strip()

        return cls(_value=raw_key)

    def _enforce_types(self) -> None:
        if not isinstance(self._value, str):
            raise IdempotencyKeyInvalid("Idempotency key must be a string.")

        if not self._value:
            raise IdempotencyKeyInvalid("Idempotency key cannot be blank.")

        length = len(self._value)
        if not (self.MIN_LENGTH <= length <= self.MAX_LENGTH):
            raise IdempotencyKeyInvalid(
                f"Idempotency key length must be between {self.MIN_LENGTH} and "
                f"{self.MAX_LENGTH} characters.",
            )

        if not self._PATTERN.fullmatch(self._value):
            raise IdempotencyKeyInvalid(
                "Idempotency key contains invalid characters. "
                "Only alphanumeric, '.', '_', and '-' are allowed.",
            )

    @property
    def value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"IdempotencyKey({self._value!r})"
