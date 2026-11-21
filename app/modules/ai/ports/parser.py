from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class LLMOutputParser(Protocol):
    def __call__(
        self,
        s: str,
        schema: type[BaseModel] | None = None,
        force_return: bool = False,
    ) -> Any: ...
