from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.modules.common.infrastructure.clock.fixed_clock import FixedClock


def test_fixed_clock_moves_only_when_told() -> None:
    t0 = datetime(2026, 11, 21, 12, tzinfo=UTC)
    clock = FixedClock(t0)
    assert clock.now_utc() == t0
    assert clock.advance(timedelta(milliseconds=250)) == t0 + timedelta(milliseconds=250)
    assert clock.now_utc() == t0 + timedelta(milliseconds=250)


def test_fixed_clock_normalizes_to_utc_and_rejects_naive_or_backwards() -> None:
    clock = FixedClock(datetime(2026, 11, 21, 9, tzinfo=timezone(timedelta(hours=-3))))
    assert clock.now_utc() == datetime(2026, 11, 21, 12, tzinfo=UTC)
    assert clock.now_utc().tzinfo == UTC
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 21))
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))
