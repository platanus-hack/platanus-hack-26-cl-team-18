"""Why a provider call failed, as types the layers above can branch on.

Warm jobs (moment tagging, copy writing) need to know which failure they hit
without reading provider prose:

- a throttled or unreachable provider clears on its own: retry later;
- a rejected request fails identically every time: drop it, don't retry;
- an account that cannot pay fails *every* job until a human tops it up: stop
  spending retries and fall back (deterministic tags, stock copy) loudly;
- a cut-off or refused answer is not a shorter answer: never parse its prefix.

The adapter translates SDK exceptions into these at the infrastructure edge, so
nothing above ``app/modules/ai/infrastructure`` imports a provider SDK.

Messages name *why* a call failed, never *what* the provider said: at most a
status code or a machine-readable reason. Provider responses can echo the
prompt (moment tags, a brand's copy brief), so an exception message is never a
place for them. ``error_code`` is what the warm worker records and what the
console shows; it equals the class name on purpose.
"""

from __future__ import annotations


class ProviderCreditExhausted(Exception):
    """The AI provider account cannot fund any call until it is topped up.

    Deliberately not a subclass of a "retryable" or "transient" base: it clears,
    but only because a human acts. Nothing the system does on its own makes the
    next attempt likelier to succeed, so callers say so out loud instead of
    burning a retry budget against it.
    """

    failure_kind = "provider_credit_exhausted"
    error_code = "ProviderCreditExhausted"


class ProviderOutputIncomplete(Exception):
    """The provider stopped before finishing its answer, or declined to answer.

    ``reason`` is the provider's machine-readable stop reason (``max_tokens``,
    ``refusal``) or ``missing_output``, never output text. Retrying the
    identical request hits the same cap or the same refusal, so this is raised
    instead of being retried silently.
    """

    error_code = "ProviderOutputIncomplete"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Provider output incomplete: {reason}")
        self.reason = reason


class ProviderRateLimited(Exception):
    """The provider throttled the account and kept doing so across retries.

    Clears on its own; the caller should try again later, not now.
    """

    error_code = "ProviderRateLimited"


class ProviderUnavailable(Exception):
    """The provider timed out, refused the connection, was overloaded or answered 5xx.

    Nothing about the request is wrong; the same request may succeed later.
    """

    error_code = "ProviderUnavailable"


class ProviderRequestRejected(Exception):
    """The provider refused this request (4xx other than throttling or billing).

    A bad key, a missing model or a malformed payload: repeating the identical
    request fails identically, so it is raised on the first attempt.
    """

    error_code = "ProviderRequestRejected"

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Provider rejected the request with status {status_code}")
        self.status_code = status_code


__all__ = [
    "ProviderCreditExhausted",
    "ProviderOutputIncomplete",
    "ProviderRateLimited",
    "ProviderRequestRejected",
    "ProviderUnavailable",
]
