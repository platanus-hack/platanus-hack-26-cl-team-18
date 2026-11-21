from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.modules.common.domain.primitives.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class HttpUrl(ValueObject):
    value: str

    @classmethod
    def create(cls, raw: str | HttpUrl) -> HttpUrl:
        if isinstance(raw, HttpUrl):
            return raw
        if not isinstance(raw, str):
            raise TypeError("HttpUrl input must be str or HttpUrl")

        normalized = raw.strip()
        obj = cls(value=normalized)
        obj._enforce_types()
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("HttpUrl.value must be str")
        if not self.value.strip():
            raise ValueError("HttpUrl cannot be blank")

        parsed = urlparse(self.value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("HttpUrl must be a valid absolute HTTP(S) URL")

    def __str__(self) -> str:
        return self.value
