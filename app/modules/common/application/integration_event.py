from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    """Stable, serialized event published outside the bounded context.

    This is the ONLY thing that leaves the service via the outbox.
    """

    # identity / routing
    event_id: UUID
    event_type: str
    schema_version: int

    # time / tenant
    occurred_at: datetime  # UTC (tz-aware)
    organization_id: UUID | None

    # aggregate
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int

    # data contract
    payload: dict[str, Any]  # JSON-safe, schema-versioned
    headers: dict[str, Any] = field(default_factory=dict)  # JSON-safe extension point

    # tracing / audit
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    request_id: UUID | None = None
    idempotency_key: str | None = None
    source: str | None = None

    actor_type: str | None = None
    actor_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("IntegrationEvent.occurred_at must be datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("IntegrationEvent.occurred_at must be timezone-aware (UTC)")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))

        _validate_jsonish_dict("payload", self.payload)
        _validate_jsonish_dict("headers", self.headers)


def _is_jsonish(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_jsonish(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_jsonish(v) for k, v in value.items())
    return False


def _validate_jsonish_dict(field_name: str, value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"IntegrationEvent.{field_name} must be a dict[str, Any]")
    if not _is_jsonish(dict(value)):
        raise TypeError(f"IntegrationEvent.{field_name} must be JSON-serializable (str keys)")
