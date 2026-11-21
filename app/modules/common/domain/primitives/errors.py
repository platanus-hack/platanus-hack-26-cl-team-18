from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _is_jsonish(x: Any) -> bool:
    if x is None or isinstance(x, (str, int, float, bool)):
        return True
    if isinstance(x, (list, tuple)):
        return all(_is_jsonish(i) for i in x)
    if isinstance(x, dict):
        return all(isinstance(k, str) and _is_jsonish(v) for k, v in x.items())
    return False


@dataclass(slots=True)
class DomainError(Exception):
    """Base domain error (safe to map to 4xx in API).

    This class represents **business / domain failures**, not programmer errors.
    A DomainError is:
    - safe to surface to API clients (no internals, no stack traces)
    - stable and machine-readable via `code`
    - human-readable via `msg`
    - optionally enriched with structured, JSON-serializable `params`

    ---
    Basic usage (exception style)
    ------------------------------
    Domain errors are typically *raised* inside domain logic:

        raise DomainError(
            code="campaigns.campaign_name_unique",
            msg="Campaign name must be unique per brand.",
            params={"name": "Trío mañanero"},
        )

    or via a small, explicit subclass:

        class CampaignNameUnique(DomainError):
            def __init__(self, *, name: str) -> None:
                super().__init__(
                    code="campaigns.campaign_name_unique",
                    msg="Campaign name must be unique per brand.",
                    params={"name": name},
                )

        raise CampaignNameUnique(name="Trío mañanero")

    ---
    Result-style usage (future-compatible)
    ---------------------------------------
    The same DomainError object can be *returned* instead of raised,
    enabling Result/Either patterns without refactoring domain logic:

        return Err(CampaignNameUnique(name="Trío mañanero"))

    This keeps domain semantics identical whether using exceptions
    or explicit error values.

    ---
    API mapping
    -----------
    Infrastructure / API layers may safely convert this error using:

        error.to_public_dict()

    yielding a payload like:

        {
            "code": "campaigns.campaign_name_unique",
            "message": "Campaign name must be unique per brand.",
            "params": {"name": "Trío mañanero"}
        }

    ---
    Design constraints
    ------------------
    - `msg` must be a simple, user-facing string (no formatting logic).
    - `code` must be stable and suitable for programmatic handling.
    - `params` must be JSON-serializable and safe to expose.
    - DomainError MUST NOT depend on HTTP, transport, or framework concerns.
    """

    code: str
    msg: str
    params: dict[str, Any] = field(default_factory=dict)
    # is_transient=True hints to clients that a retry may succeed (e.g. lock contention,
    # temporary rate limits). is_transient=False (default) means the same request will
    # always fail until the underlying data changes.
    is_transient: bool = False
    # http_status_hint lets domain code suggest an appropriate HTTP status without
    # importing HTTP concerns. API handlers may override this. None = let API decide.
    http_status_hint: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise TypeError("DomainError.code must be a non-empty str")
        if not isinstance(self.msg, str) or not self.msg.strip():
            raise TypeError("DomainError.message must be a non-empty str")
        if not isinstance(self.params, dict):
            raise TypeError("DomainError.params must be a dict[str, Any]")
        # enforce "safe to return" constraint
        if not _is_jsonish(dict(self.params)):
            raise TypeError("DomainError.params must be JSON-serializable (str keys)")

        # Make Exception.__str__ show the message (nice logs + default handlers)
        Exception.__init__(self, self.msg)

    def to_public_dict(self) -> dict[str, Any]:
        """API-safe representation."""
        out: dict[str, Any] = {"code": self.code, "message": self.msg}
        if self.params:
            out["params"] = dict(self.params)
        if self.is_transient:
            out["is_transient"] = True
        return out
