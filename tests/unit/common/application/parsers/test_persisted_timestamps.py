from datetime import UTC, datetime

import pytest

from app.modules.common.application.parsers.timestamps import (
    parse_persisted_utc_datetime,
    parse_utc_datetime,
)


@pytest.mark.parametrize("offset", ["Z", "+00:00", "-03:00"])
@pytest.mark.parametrize("fraction", ["", ".123456", ".999999"])
def test_persisted_instants_keep_exact_microseconds(offset, fraction):
    raw = f"2026-10-02T23:59:59{fraction}{offset}"

    parsed = parse_persisted_utc_datetime(raw)

    assert parsed == datetime.fromisoformat(raw).astimezone(UTC)
    assert parsed.tzinfo is UTC


@pytest.mark.parametrize(
    "raw",
    ["2026-10-02", "2026-10-02T23:59:59", "2026-10-02 23:59:59Z", " 2026-10-02T23:59:59Z"],
)
def test_persisted_instants_reject_ambiguous_syntax(raw):
    with pytest.raises(ValueError, match="explicit timezone offset"):
        parse_persisted_utc_datetime(raw)


def test_persisted_instants_reject_precision_the_storage_type_cannot_represent():
    with pytest.raises(ValueError, match="microsecond precision"):
        parse_persisted_utc_datetime("2026-10-02T23:59:59.9999999Z")


def test_transport_parser_keeps_its_existing_millisecond_rounding_contract():
    raw = "2026-10-02T23:59:59.999999Z"
    assert parse_utc_datetime(raw) == datetime(2026, 10, 3, tzinfo=UTC)
    assert parse_persisted_utc_datetime(raw) == datetime(
        2026, 10, 2, 23, 59, 59, 999999, tzinfo=UTC
    )
