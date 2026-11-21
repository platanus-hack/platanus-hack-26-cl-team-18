"""A utilization ratio only means something once the pool is big enough to have slack.

Production logged 1219 ``postgres_pool_high_utilization`` WARNINGs in two days.
Every one came from a worker pool sized at ``DB_MAXCONN`` 1 or 2, where a single
in-flight checkout is already 50-100% -- the designed steady state, not a warning
sign.  The signal was pure noise, and it buried the exhaustion errors that were
real.  The warning now applies only to pools whose denominator makes the ratio a
statement about spare capacity.
"""

from __future__ import annotations

import logging
import threading

import pytest
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

from app.modules.common.infrastructure.postgres.pool_activity import PoolActivity
from app.modules.common.infrastructure.postgres.postgres import PostgresDB


class _Cursor:
    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        return None


class _Conn:
    def __init__(self) -> None:
        self.closed = 0
        self.autocommit = True

    def get_transaction_status(self) -> int:
        return TRANSACTION_STATUS_IDLE

    def rollback(self) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor()


class _Pool:
    def __init__(self, size: int) -> None:
        self._available = [_Conn() for _ in range(size)]

    def getconn(self, *, timeout=10.0) -> _Conn:
        return self._available.pop()

    def putconn(self, conn: _Conn, close: bool = False) -> None:
        if not close:
            self._available.append(conn)


def _db(size: int) -> PostgresDB:
    db = PostgresDB.__new__(PostgresDB)
    db._activity = PoolActivity(role="test")
    db.pool = _Pool(size)
    db._maxconn = size
    db._used = set()
    db._last_released_at = {}
    db._role = "delivery_ledger"
    db._held_by_thread = {}
    db._held_lock = threading.Lock()
    return db


def _warned(caplog: pytest.LogCaptureFixture) -> bool:
    return any(r.message == "postgres_pool_high_utilization" for r in caplog.records)


@pytest.mark.parametrize("size", [1, 2, 3])
def test_a_worker_sized_pool_never_warns_on_its_own_steady_state(
    size: int, caplog: pytest.LogCaptureFixture
) -> None:
    db = _db(size)
    with caplog.at_level(logging.WARNING):
        for _ in range(size):
            db.get_connection()

    assert not _warned(caplog)


def test_a_pool_large_enough_for_the_ratio_to_mean_headroom_still_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _db(5)
    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            db.get_connection()

    record = next(r for r in caplog.records if r.message == "postgres_pool_high_utilization")
    assert record.used == 4
    assert record.max == 5
    assert record.utilization_pct == 80.0
    assert record.pool_role == "delivery_ledger"
    assert record.held_by_caller == 4


def test_a_large_pool_below_the_threshold_stays_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _db(10)
    with caplog.at_level(logging.WARNING):
        for _ in range(7):
            db.get_connection()

    assert not _warned(caplog)


def test_pressure_warning_distinguishes_caller_holdings_from_total_usage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _db(5)
    borrowed = []

    def borrow_elsewhere() -> None:
        for _ in range(3):
            borrowed.append(db.get_connection())

    thread = threading.Thread(target=borrow_elsewhere)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(borrowed) == 3
    with caplog.at_level(logging.WARNING):
        db.get_connection()
    record = next(r for r in caplog.records if r.message == "postgres_pool_high_utilization")
    assert record.used == 4
    assert record.held_by_caller == 1
    assert record.pool_role == "delivery_ledger"
