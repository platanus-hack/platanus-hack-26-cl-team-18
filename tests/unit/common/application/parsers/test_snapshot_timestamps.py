"""Shared parsing of stored document-snapshot timestamps."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.modules.common.application.parsers.timestamps import (
    parse_snapshot_date,
    parse_snapshot_datetime,
)


def test_bare_business_date_is_naive_midnight_of_that_day() -> None:
    assert parse_snapshot_datetime("2026-04-01", field_name="period_start") == datetime(2026, 4, 1)


def test_full_timestamp_is_normalized_to_utc() -> None:
    assert parse_snapshot_datetime(
        "2026-04-01T03:00:00+03:00", field_name="period_start"
    ) == datetime(2026, 4, 1, 0, 0, tzinfo=UTC)


def test_full_timestamp_without_offset_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_snapshot_datetime("2026-04-01T00:00:00", field_name="period_start")


@pytest.mark.parametrize(
    "raw", ["2026-04-01", "2026-04-01T23:59:59+00:00", "2026-04-01T00:00:00.000Z"]
)
def test_snapshot_date_is_the_calendar_day(raw: str) -> None:
    assert parse_snapshot_date(raw, field_name="date") == date(2026, 4, 1)
