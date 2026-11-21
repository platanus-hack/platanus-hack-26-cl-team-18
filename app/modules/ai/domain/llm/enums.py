from __future__ import annotations

from enum import StrEnum


class ProviderRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
