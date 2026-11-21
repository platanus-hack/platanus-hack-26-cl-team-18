from __future__ import annotations

import logging
import re

from .model import GuardrailResponse


class GuardrailService:
    """Provisional guardrail service: always returns valid.
    Replace with real prompt validation later.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        max_prompt_chars: int = 12_000,
        blocked_patterns: tuple[str, ...] = (),
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.max_prompt_chars = max_prompt_chars
        self.blocked_patterns = blocked_patterns

    async def validate_prompt(self, prompt: str) -> GuardrailResponse:
        if not isinstance(prompt, str) or not prompt.strip():
            return GuardrailResponse(is_valid=False, reason="prompt must be a non-empty string")

        if len(prompt) > self.max_prompt_chars:
            return GuardrailResponse(
                is_valid=False,
                reason=f"prompt exceeds max length of {self.max_prompt_chars} chars",
            )

        for pattern in self.blocked_patterns:
            if re.search(pattern, prompt, flags=re.IGNORECASE):
                return GuardrailResponse(
                    is_valid=False,
                    reason=f"prompt blocked by guardrail pattern: {pattern}",
                )

        self.logger.debug(
            "guardrail_check_passed",
            extra={"prompt_length": len(prompt)},
        )
        return GuardrailResponse(is_valid=True)
