"""Application-layer entry point for UTC timestamp parsing/formatting.

The implementation is a pure codec that lives in the domain layer
(``app.modules.common.domain.value_objects.utc_timestamp_codec``) because it has
zero application/infrastructure dependencies and is needed by domain value objects
(e.g. ``UtcTimestamp``-bearing snapshots) for their canonical serialization. This module re-exports
the codec so the established application/transport import path keeps working with
no behavioral change.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from app.modules.common.domain.value_objects.utc_timestamp_codec import (
    format_utc_datetime,
    parse_persisted_utc_datetime,
    parse_utc_datetime,
    parse_utc_only_datetime,
    parse_utc_timestamp,
)

__all__ = [
    "format_utc_datetime",
    "normalize_command_timestamp",
    "parse_persisted_utc_datetime",
    "parse_snapshot_date",
    "parse_snapshot_datetime",
    "parse_utc_datetime",
    "parse_utc_only_datetime",
    "parse_utc_timestamp",
    "reject_future_occurred_at",
]


def parse_snapshot_datetime(raw_value: str, *, field_name: str) -> datetime:
    """Parse a stored document-snapshot timestamp for display.

    Snapshots persist either a bare business date (``"2026-04-01"``) or a full
    UTC instant. A bare date becomes naive midnight of that day, because it
    names a calendar day, not an instant. A full timestamp must carry an explicit
    offset and is normalized to UTC.
    """
    if "T" not in raw_value:
        return datetime.fromisoformat(f"{raw_value}T00:00:00")
    return parse_utc_datetime(raw_value, field_name=field_name)


def parse_snapshot_date(raw_value: str, *, field_name: str) -> date:
    """Calendar date of a stored snapshot value (see :func:`parse_snapshot_datetime`)."""
    return parse_snapshot_datetime(raw_value, field_name=field_name).date()


def normalize_command_timestamp(
    value: datetime,
    *,
    field_name: str,
    on_invalid: Callable[[str], Exception],
) -> datetime:
    """Normalize a command's timestamp (``occurred_at``/``created_at``) to canonical UTC.

    Command handlers across bounded contexts all need the same thing: take the
    timestamp the application layer stamped onto a command, validate it is a
    tz-aware instant and canonicalize it to UTC, and — on failure — surface a
    *bounded-context-specific* command error rather than a raw ``TypeError`` /
    ``ValueError``.

    This centralizes that rule in one place (delegating to ``parse_utc_datetime``)
    while preserving each context's own error type via the ``on_invalid`` factory:
    it is called with the failure message and must return the exception to raise.

        return normalize_command_timestamp(
            occurred_at, field_name="occurred_at", on_invalid=UsageChargeCommandError
        )
    """
    try:
        return parse_utc_datetime(value, field_name=field_name)
    except (TypeError, ValueError) as exc:
        raise on_invalid(str(exc)) from exc


def reject_future_occurred_at(
    occurred_at: datetime,
    *,
    now_utc: datetime,
    on_invalid: Callable[[str], Exception],
) -> datetime:
    """Normalize ``occurred_at`` to canonical UTC and reject future instants.

    Command handlers across the charging bounded context all need the same
    guard: canonicalize both the command's ``occurred_at`` and the clock's
    ``now_utc`` via ``normalize_command_timestamp``, then reject an
    ``occurred_at`` that is strictly after ``now_utc``.

    Equality is accepted so server-generated command timestamps do not fail
    spuriously — millisecond normalization can collapse two consecutive clock
    reads to the same instant — while genuinely future instants remain
    invalid. Returns the normalized ``occurred_at`` on success.

        return reject_future_occurred_at(
            cmd.occurred_at, now_utc=clock.now_utc(), on_invalid=UsageChargeCommandError
        )
    """
    normalized_occurred_at = normalize_command_timestamp(
        occurred_at,
        field_name="occurred_at",
        on_invalid=on_invalid,
    )
    normalized_now = normalize_command_timestamp(
        now_utc,
        field_name="now_utc",
        on_invalid=on_invalid,
    )
    if normalized_occurred_at > normalized_now:
        raise on_invalid("occurred_at must be earlier than current UTC time")
    return normalized_occurred_at
