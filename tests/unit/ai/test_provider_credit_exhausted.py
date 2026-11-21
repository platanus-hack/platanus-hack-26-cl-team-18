"""Each Anthropic SDK failure maps to the typed error the warm worker branches on.

"The account can't pay" must never look like "this request was bad": a bad
request is that one job's fault and is dropped, while an unfunded account fails
every job until a human tops it up, so warm work falls back (deterministic
tags, stock copy) and the failure is surfaced instead of retried blindly.
Classification is by SDK exception class, never by message text.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import pytest

from app.modules.ai.domain.llm.enums import ProviderRole
from app.modules.ai.domain.llm.provider import ProviderMessage
from app.modules.ai.infrastructure.adapters.llm.anthropic import (
    AnthropicProvider,
    classify_provider_error,
)
from app.modules.ai.ports.errors import (
    ProviderCreditExhausted,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from tests.unit.ai.async_runner import run_async
from tests.unit.ai.infrastructure.test_parsers_and_provider import FakeClient

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
_SECRET_PROSE = "the prompt said: brand brief for Café Andino"


def sdk_error(cls: type[anthropic.APIStatusError], status: int, error_type: str) -> Any:
    body = {"type": "error", "error": {"type": error_type, "message": _SECRET_PROSE}}
    response = httpx2.Response(status, request=_REQUEST, json=body)
    return cls(_SECRET_PROSE, response=response, body=body)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (sdk_error(anthropic.PaymentRequiredError, 402, "billing_error"), ProviderCreditExhausted),
        (
            sdk_error(anthropic.EnforcedSpendLimitReachedError, 429, "rate_limit_error"),
            ProviderCreditExhausted,
        ),
        (
            sdk_error(anthropic.OrganizationOnHoldError, 400, "invalid_request_error"),
            ProviderCreditExhausted,
        ),
        (sdk_error(anthropic.RateLimitError, 429, "rate_limit_error"), ProviderRateLimited),
        (sdk_error(anthropic.OverloadedError, 529, "overloaded_error"), ProviderUnavailable),
        (sdk_error(anthropic.InternalServerError, 500, "api_error"), ProviderUnavailable),
        (
            sdk_error(anthropic.BadRequestError, 400, "invalid_request_error"),
            ProviderRequestRejected,
        ),
        (
            sdk_error(anthropic.AuthenticationError, 401, "authentication_error"),
            ProviderRequestRejected,
        ),
        (sdk_error(anthropic.NotFoundError, 404, "not_found_error"), ProviderRequestRejected),
        (anthropic.APITimeoutError(request=_REQUEST), ProviderUnavailable),
        (anthropic.APIConnectionError(request=_REQUEST), ProviderUnavailable),
    ],
)
def test_sdk_failures_map_to_typed_errors(error: Exception, expected: type[Exception]) -> None:
    classified = classify_provider_error(error)  # type: ignore[arg-type]

    assert type(classified) is expected
    assert "Café" not in str(classified)


def test_a_spend_cap_429_is_not_mistaken_for_throttling() -> None:
    """Spend-cap 429s subclass RateLimitError; billing must win, since retries can't fix it."""
    error = sdk_error(anthropic.EnforcedSpendLimitReachedError, 429, "rate_limit_error")

    assert isinstance(error, anthropic.RateLimitError)
    assert isinstance(classify_provider_error(error), ProviderCreditExhausted)


def test_provider_raises_the_typed_error_without_chaining_provider_text() -> None:
    client = FakeClient(error=sdk_error(anthropic.PaymentRequiredError, 402, "billing_error"))
    provider = AnthropicProvider(client=client)  # type: ignore[arg-type]

    with pytest.raises(ProviderCreditExhausted) as raised:
        run_async(
            provider.complete([ProviderMessage.create(role=ProviderRole.USER, content="tags")])
        )

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert len(client.messages.calls) == 1
