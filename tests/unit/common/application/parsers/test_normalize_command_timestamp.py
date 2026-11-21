from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.modules.common.application.parsers.timestamps import normalize_command_timestamp


class _BoundedContextError(ValueError):
    """Stand-in for a per-bounded-context command error."""


def _on_invalid(message: str) -> Exception:
    return _BoundedContextError(message)


class TestNormalizeCommandTimestamp:
    def test_passes_through_utc_instant(self) -> None:
        value = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

        assert (
            normalize_command_timestamp(value, field_name="occurred_at", on_invalid=_on_invalid)
            == value
        )

    def test_canonicalizes_offset_to_utc(self) -> None:
        value = datetime(2026, 1, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3)))

        normalized = normalize_command_timestamp(
            value, field_name="occurred_at", on_invalid=_on_invalid
        )

        assert normalized == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert normalized.tzinfo == UTC

    def test_wraps_naive_datetime_in_bounded_context_error(self) -> None:
        # Regression: a naive (tz-unaware) timestamp must raise the caller's own
        # error type, never leak a raw TypeError/ValueError out of the handler.
        with pytest.raises(_BoundedContextError):
            normalize_command_timestamp(
                datetime.fromisoformat("2026-01-01T12:00:00"),
                field_name="occurred_at",
                on_invalid=_on_invalid,
            )

    def test_wraps_malformed_value_in_bounded_context_error(self) -> None:
        with pytest.raises(_BoundedContextError):
            normalize_command_timestamp(
                "not-a-timestamp",
                field_name="occurred_at",
                on_invalid=_on_invalid,  # type: ignore[arg-type]
            )

    def test_error_factory_receives_the_failure_message(self) -> None:
        with pytest.raises(_BoundedContextError) as excinfo:
            normalize_command_timestamp(
                None,
                field_name="occurred_at",
                on_invalid=_on_invalid,  # type: ignore[arg-type]
            )

        # The factory is handed the underlying message, and the original cause is chained.
        assert str(excinfo.value)
        assert excinfo.value.__cause__ is not None
