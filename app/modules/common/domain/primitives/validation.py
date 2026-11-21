"""Shared domain validation predicates.

Centralizes the *rules* that every aggregate would otherwise repeat — "a domain
timestamp must be a :class:`UtcTimestamp` instance", "an identifier must be a
:class:`UUID`" — while letting each bounded context raise its own domain error
through an injected factory.

This keeps the rule itself in one place (change it once — e.g. to also reject
naive datetimes — and every aggregate follows) without leaking one context's
error type into another. The ``on_invalid`` callable is only invoked on the
failure path, so building a context-specific error is free in the happy case.

Pure domain code: no IO, no logging.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp


def ensure_utc_timestamp(
    value: UtcTimestamp,
    *,
    on_invalid: Callable[[], Exception],
) -> UtcTimestamp:
    """Return ``value`` when it is a ``UtcTimestamp``; otherwise raise ``on_invalid()``."""
    if not isinstance(value, UtcTimestamp):
        raise on_invalid()
    return value


def ensure_optional_utc_timestamp(
    value: UtcTimestamp | None,
    *,
    on_invalid: Callable[[], Exception],
) -> UtcTimestamp | None:
    """Like :func:`ensure_utc_timestamp`, but passes ``None`` through unchanged."""
    if value is None:
        return None
    if not isinstance(value, UtcTimestamp):
        raise on_invalid()
    return value


def ensure_uuid(value: UUID, *, on_invalid: Callable[[], Exception]) -> UUID:
    """Return ``value`` when it is a ``UUID``; otherwise raise ``on_invalid()``."""
    if not isinstance(value, UUID):
        raise on_invalid()
    return value
