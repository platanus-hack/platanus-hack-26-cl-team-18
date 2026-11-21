from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardrailResponse:
    is_valid: bool
    reason: str | None = None
