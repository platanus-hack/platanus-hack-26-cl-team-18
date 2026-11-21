"""Context-local logging fields.

Use bind_context(...) within request/command handling to enrich logs with
correlation_id, organization_id, user_id, etc.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

CONTEXT_KEYS = (
    "correlation_id",
    "request_id",
    "causation_id",
    "organization_id",
    "user_id",
    "actor_type",
    "actor_id",
    "command_id",
    "event_id",
    "event_type",
    "schema_version",
    "aggregate_id",
    "aggregate_type",
    "aggregate_version",
    "idempotency_key",
    "source",
)

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_causation_id: ContextVar[str | None] = ContextVar("causation_id", default=None)
_organization_id: ContextVar[str | None] = ContextVar("organization_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_actor_type: ContextVar[str | None] = ContextVar("actor_type", default=None)
_actor_id: ContextVar[str | None] = ContextVar("actor_id", default=None)
_command_id: ContextVar[str | None] = ContextVar("command_id", default=None)
_event_id: ContextVar[str | None] = ContextVar("event_id", default=None)
_event_type: ContextVar[str | None] = ContextVar("event_type", default=None)
_schema_version: ContextVar[str | None] = ContextVar("schema_version", default=None)
_aggregate_id: ContextVar[str | None] = ContextVar("aggregate_id", default=None)
_aggregate_type: ContextVar[str | None] = ContextVar("aggregate_type", default=None)
_aggregate_version: ContextVar[str | None] = ContextVar("aggregate_version", default=None)
_idempotency_key: ContextVar[str | None] = ContextVar("idempotency_key", default=None)
_source: ContextVar[str | None] = ContextVar("source", default=None)

_VAR_MAP: dict[str, ContextVar[str | None]] = {
    "correlation_id": _correlation_id,
    "request_id": _request_id,
    "causation_id": _causation_id,
    "organization_id": _organization_id,
    "user_id": _user_id,
    "actor_type": _actor_type,
    "actor_id": _actor_id,
    "command_id": _command_id,
    "event_id": _event_id,
    "event_type": _event_type,
    "schema_version": _schema_version,
    "aggregate_id": _aggregate_id,
    "aggregate_type": _aggregate_type,
    "aggregate_version": _aggregate_version,
    "idempotency_key": _idempotency_key,
    "source": _source,
}


def set_context(**values: Any) -> None:
    """Set one or more context values for the current execution flow."""
    for key, value in values.items():
        if key in _VAR_MAP and value is not None:
            _VAR_MAP[key].set(str(value))


def clear_context() -> None:
    """Clear all context values for the current execution flow."""
    for var in _VAR_MAP.values():
        var.set(None)


def get_context() -> dict[str, str]:
    """Return current context values without None entries."""
    ctx: dict[str, str] = {}
    for key, var in _VAR_MAP.items():
        value = var.get()
        if value is not None:
            ctx[key] = value
    return ctx


@contextmanager
def bind_context(**values: Any) -> Iterator[None]:
    """Temporarily bind context values, restoring previous values on exit."""
    tokens = {}
    for key, value in values.items():
        if key in _VAR_MAP and value is not None:
            tokens[key] = _VAR_MAP[key].set(str(value))
    try:
        yield
    finally:
        for key, token in tokens.items():
            _VAR_MAP[key].reset(token)
