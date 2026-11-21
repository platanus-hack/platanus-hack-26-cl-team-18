"""Build the LLM provider from environment variables.

The composition root calls ``build_llm_provider()``; nothing else reads these
variables. Keys come from the environment only: never hardcode or log them.

- ``ANTHROPIC_API_KEY``  required
- ``ANTHROPIC_MODEL``    optional, default ``DEFAULT_MODEL`` (claude-haiku-5-5)
- ``ANTHROPIC_EFFORT``   optional, low|medium|high|xhigh|max, default ``low``
- ``ANTHROPIC_TIMEOUT``  optional seconds, default ``DEFAULT_TIMEOUT_SECONDS``
- ``ANTHROPIC_BASE_URL`` optional, read by the SDK itself
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.modules.ai.domain.model_capabilities import DEFAULT_EFFORT, parse_effort
from app.modules.ai.infrastructure.adapters.llm.anthropic import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicProvider,
)
from app.modules.ai.ports.provider import ILLMProvider


class LLMNotConfigured(RuntimeError):
    """ANTHROPIC_API_KEY is missing; the AI feature cannot run."""


def build_llm_provider(env: Mapping[str, str] | None = None) -> ILLMProvider:
    source = os.environ if env is None else env
    api_key = source.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise LLMNotConfigured("Set ANTHROPIC_API_KEY to use the AI module")
    effort = source.get("ANTHROPIC_EFFORT", "").strip()
    return AnthropicProvider(
        api_key=api_key,
        model=source.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL,
        effort=parse_effort(effort) if effort else DEFAULT_EFFORT,
        timeout=float(source.get("ANTHROPIC_TIMEOUT", "") or DEFAULT_TIMEOUT_SECONDS),
    )


__all__ = ["LLMNotConfigured", "build_llm_provider"]
