"""An exhausted pool must say *whose* pool it was, and whether waiting could help.

Prod logged 142 ``postgres_pool_exhausted`` in six hours carrying two fields --
``max: 2`` and ``timeout_seconds: 10``.  Eight workers run at ``DB_MAXCONN=2``, so
that line named none of them, and attributing the failures meant correlating
``command_id`` across neighbouring log entries by hand.  Worse, it did not
distinguish the two utterly different causes behind the same PoolError:

* many tasks holding one connection each -- contention, which a bigger pool or
  less concurrency fixes, and which waiting can genuinely resolve;
* one task holding every connection while it waits for one more -- a nested unit
  of work whose floor exceeds the pool, which no wait and no traffic level ever
  resolves, and which is a bug in the sizing, not in the load.

The second is the case pinned below (``outbox_relay``, floor 3, sized 2).
It now fails instantly under its own name.
"""

from __future__ import annotations

import logging
import threading

import pytest
from psycopg2 import pool as pg_pool
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

from app.modules.common.infrastructure.postgres import postgres
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
        self.transaction_status = TRANSACTION_STATUS_IDLE

    def get_transaction_status(self) -> int:
        return self.transaction_status

    def rollback(self) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor()


class _ExhaustiblePool:
    """Hands out a fixed number of connections, then raises like psycopg2 does."""

    def __init__(self, size: int) -> None:
        self._available = [_Conn() for _ in range(size)]

    def getconn(self, *, timeout=10.0) -> _Conn:
        if not self._available:
            raise pg_pool.PoolError("connection pool exhausted")
        return self._available.pop()

    def putconn(self, conn: _Conn, close: bool = False) -> None:
        if not close:
            self._available.append(conn)


def _db(size: int, *, role: str = "outbox_relay") -> PostgresDB:
    db = PostgresDB.__new__(PostgresDB)
    db._activity = postgres.PoolActivity(role="test")
    db.pool = _ExhaustiblePool(size)
    db._maxconn = size
    db._used = set()
    db._last_released_at = {}
    db._role = role
    db._held_by_thread = {}
    db._held_lock = threading.Lock()
    return db


def test_a_task_that_holds_the_whole_pool_fails_immediately_and_by_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The outbox_relay shape: three nested phases against a pool of two."""
    db = _db(2)
    db.get_connection()
    db.get_connection()

    with caplog.at_level(logging.ERROR), pytest.raises(pg_pool.PoolError):
        db.get_connection()

    record = next(r for r in caplog.records if r.message == "postgres_pool_self_starved")
    assert record.role == "outbox_relay"
    assert record.held_by_caller == 2
    assert record.max == 2


def test_self_starvation_does_not_burn_the_acquire_timeout(monkeypatch) -> None:
    """Waiting for a connection only this caller could release is pure latency."""
    slept: list[float] = []
    monkeypatch.setattr(postgres.time, "sleep", lambda seconds: slept.append(seconds))
    db = _db(1)
    db.get_connection()

    with pytest.raises(pg_pool.PoolError):
        db.get_connection()

    assert slept == []


def test_ordinary_contention_still_waits_and_reports_the_pool_holder(
    caplog: pytest.LogCaptureFixture, monkeypatch
) -> None:
    """Another thread holds the connections, so waiting is the right thing to do."""
    db = _db(2, role="delivery_ledger")
    holder = threading.Thread(target=lambda: (db.get_connection(), db.get_connection()))
    holder.start()
    holder.join()

    monkeypatch.setattr(postgres.time, "sleep", lambda _seconds: None)
    with caplog.at_level(logging.ERROR), pytest.raises(pg_pool.PoolError):
        db.get_connection()

    record = next(r for r in caplog.records if r.message == "postgres_pool_exhausted")
    assert record.role == "delivery_ledger"
    assert record.used == 2
    assert record.held_by_caller == 0
    assert not any(r.message == "postgres_pool_self_starved" for r in caplog.records)


def test_releasing_a_connection_lowers_the_callers_holding() -> None:
    """Otherwise every worker would eventually look self-starved and fail fast."""
    db = _db(2)
    first = db.get_connection()
    db.get_connection()
    db.release_connection(first)

    assert db._held_by_caller() == 1
    assert db.get_connection() is not None


def test_release_of_an_unheld_connection_does_not_underflow_the_count() -> None:
    """A double release must not drive the holding negative and mask a real starve."""
    db = _db(2)
    conn = db.get_connection()
    db.release_connection(conn)
    db.release_connection(conn)

    assert db._held_by_caller() == 0
    assert db._held_by_thread == {}


def test_the_role_falls_back_to_the_worker_name_in_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_NAME", "outbox_dispatcher")
    assert postgres._pool_role() == "outbox_dispatcher"
    monkeypatch.delenv("WORKER_NAME")
    monkeypatch.setenv("RENDER_SERVICE_NAME", "app-api")
    assert postgres._pool_role() == "app-api"
    monkeypatch.delenv("RENDER_SERVICE_NAME")
    assert postgres._pool_role() == "unknown"
