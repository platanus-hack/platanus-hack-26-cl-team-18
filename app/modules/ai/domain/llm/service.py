from __future__ import annotations

from collections.abc import Sequence

from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import ProviderMessage


def build_message(system_prompt: str, user_prompt: str) -> list[ProviderMessage]:
    return [
        ProviderMessage.create(role=ProviderRole.SYSTEM, content=system_prompt),
        ProviderMessage.create(role=ProviderRole.USER, content=user_prompt),
    ]


def get_last_user_prompt(messages: Sequence[ProviderMessage]) -> tuple[str | None, bool]:
    for msg in reversed(messages):
        if msg.role == ProviderRole.USER and msg.content:
            return msg.content, True
    return None, False
