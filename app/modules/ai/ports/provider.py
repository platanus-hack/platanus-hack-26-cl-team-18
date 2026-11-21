from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage, ProviderResponse


@runtime_checkable
class ILLMProvider(Protocol):
    async def complete(
        self,
        messages: Sequence[ProviderMessage],
        params: InferenceParams | None = None,
    ) -> ProviderResponse: ...

    async def aclose(self) -> None: ...
