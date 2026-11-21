"""``ILLMProvider`` over the official Anthropic SDK (Messages API).

Binding stack (CLAUDE.md, ADR 0006): Claude Haiku 5.5 at effort ``low``, with
structured output and prompt caching on a frozen system prompt. It backs the
warm-path ports (``AudienceTagger``, ``TopicExtractor``, ``CopyWriter``); the hot
path never awaits it.

- **Structured output** uses ``output_config.format`` (constrained decoding), so
  the JSON always matches the schema ``structured_output_for`` built.
- **Caching**: the system prompt goes first with ``cache_control``. Keep it
  frozen (no timestamps, ids or per-moment data) or every call misses the cache.
  Prompts shorter than the model's minimum cacheable prefix simply don't cache.
- **No sampling knobs**: current Claude models reject ``temperature``/``top_p``;
  effort is the only depth control.
- **Errors**: SDK exceptions become the typed ``ports.errors`` by exception
  class, never by message text. The SDK already retries 408/409/429/5xx and
  connection errors (``max_retries``); this adapter does not add its own loop.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from types import TracebackType
from typing import Any

import anthropic

from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import InferenceParams, ProviderMessage, ProviderResponse
from app.modules.ai.domain.model_capabilities import DEFAULT_EFFORT, Effort, ModelProfile
from app.modules.ai.ports.errors import (
    ProviderCreditExhausted,
    ProviderOutputIncomplete,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from app.modules.ai.ports.provider import ILLMProvider

DEFAULT_MODEL = "claude-haiku-5-5"
# Thinking tokens count against max_tokens; tags and short copy fit easily.
DEFAULT_MAX_TOKENS = 4096
# Warm results are only useful while the moment lasts.
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 2

# The account cannot pay: a 402, a spend-cap 429, or an organization on hold.
_CREDIT_EXHAUSTED: tuple[type[anthropic.APIStatusError], ...] = (
    anthropic.PaymentRequiredError,
    anthropic.EnforcedSpendLimitReachedError,
    anthropic.ContractSpendLimitReachedError,
    anthropic.OrganizationOnHoldError,
)


class AnthropicProvider(ILLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        effort: Effort = DEFAULT_EFFORT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        # ``profile.describe()`` is the model_version recorded with each result.
        self.profile = ModelProfile(model, effort)
        self.client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )
        self.default_params = InferenceParams.create(max_tokens=DEFAULT_MAX_TOKENS)

    async def aclose(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> AnthropicProvider:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def complete(
        self,
        messages: Sequence[ProviderMessage],
        params: InferenceParams | None = None,
    ) -> ProviderResponse:
        request = self.build_request(messages, params or self.default_params)
        try:
            response = await self.client.messages.create(**request)
        except anthropic.APIError as exc:
            raise classify_provider_error(exc) from None

        # Check why generation stopped before reading content: a truncated or
        # refused answer must never be parsed as if it were complete.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason in {"max_tokens", "refusal"}:
            raise ProviderOutputIncomplete(str(stop_reason))
        text = "".join(
            block.text
            for block in getattr(response, "content", None) or ()
            if getattr(block, "type", None) == "text"
        )
        if not text and request["output_config"].get("format") is not None:
            raise ProviderOutputIncomplete("missing_output")
        return ProviderResponse.create(output=text)

    def build_request(
        self,
        messages: Sequence[ProviderMessage],
        params: InferenceParams,
    ) -> dict[str, Any]:
        """The ``messages.create`` keyword arguments for one call."""
        system_text = "\n\n".join(m.content for m in messages if m.role is ProviderRole.SYSTEM)
        output_config: dict[str, Any] = {"effort": self.profile.effort}
        if params.structured_output is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": json.loads(params.structured_output.schema_json),
            }
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": params.max_tokens,
            "messages": [
                {"role": m.role.value, "content": m.content}
                for m in messages
                if m.role is not ProviderRole.SYSTEM
            ],
            "output_config": output_config,
        }
        if system_text:
            request["system"] = [
                {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
            ]
        return request


def classify_provider_error(exc: anthropic.APIError) -> Exception:
    """The typed failure an SDK exception stands for. Never copies provider text."""
    if isinstance(exc, _CREDIT_EXHAUSTED):
        return ProviderCreditExhausted(f"Provider refused on account grounds ({exc.status_code})")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderRateLimited(f"Provider throttled the request ({exc.status_code})")
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return ProviderUnavailable(f"Provider transport failure: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIStatusError):
        # InternalServerError covers 5xx, including 529 OverloadedError.
        if isinstance(exc, anthropic.InternalServerError | anthropic.RequestTimeoutError):
            return ProviderUnavailable(f"Provider answered {exc.status_code}")
        return ProviderRequestRejected(exc.status_code)
    return ProviderUnavailable(f"Provider failure: {type(exc).__name__}")


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "AnthropicProvider",
    "classify_provider_error",
]
