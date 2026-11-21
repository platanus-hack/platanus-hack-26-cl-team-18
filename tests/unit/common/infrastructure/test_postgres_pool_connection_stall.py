"""Concurrency regressions through the real PostgresDB/pool boundary.

Only the wire connection is faked: replacing the pool would hide the lock that
made a failed handshake stall unrelated requests in production.

Stalled connection attempts never own the bookkeeping lock,
and their reservations outlive timed-out requests.
"""

import socket
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import psycopg2
import pytest
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

from app.modules.common.infrastructure.postgres import postgres
from app.modules.common.infrastructure.postgres.postgres import PostgresDB


class Connection:
    def __init__(self):
        self.closed = False
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=TRANSACTION_STATUS_IDLE)
        self.statements = []
        self.closed_event = Event()

    def get_transaction_status(self):
        return self.info.transaction_status

    def rollback(self):
        self.info.transaction_status = TRANSACTION_STATUS_IDLE

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=None):
        self.statements.append(query)

    def close(self):
        self.closed = True
        self.closed_event.set()


def build_db(**kwargs):
    return PostgresDB(
        dbname="test",
        user="test",
        password="not-a-secret",
        host="localhost",
        connect_retries=1,
        **kwargs,
    )


def test_stalled_connect_does_not_block_return_or_reuse_of_healthy_connection(monkeypatch):
    entered, unblock = Event(), Event()
    healthy = Connection()
    calls = 0

    def connect(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return healthy
        entered.set()
        assert unblock.wait(5), "test did not release the simulated network stall"
        raise psycopg2.OperationalError("simulated handshake timeout")

    monkeypatch.setattr(psycopg2, "connect", connect)
    db = build_db(minconn=1, maxconn=2)
    borrowed = db.get_connection()
    with ThreadPoolExecutor(max_workers=2) as threads:
        waiting = threads.submit(db.get_connection)
        try:
            assert entered.wait(1)
            released = threads.submit(db.release_connection, borrowed)
            released.result(timeout=1)
            # The waiting borrower must be able to use this healthy connection
            # even while the unrelated handshake is still blocked.
            assert waiting.result(timeout=1) is healthy
            assert not unblock.is_set()
        finally:
            unblock.set()
            db.close_all()


def test_checkout_deadline_does_not_wait_for_libpq_and_late_connection_is_closed(monkeypatch):
    entered, unblock = Event(), Event()
    late = Connection()

    def connect(**kwargs):
        entered.set()
        assert unblock.wait(5)
        return late

    monkeypatch.setattr(psycopg2, "connect", connect)
    monkeypatch.setattr(postgres, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.1)
    db = build_db(minconn=0, maxconn=1)
    with ThreadPoolExecutor(max_workers=1) as threads:
        waiting = threads.submit(db.get_connection)
        try:
            assert entered.wait(1)
            with pytest.raises(psycopg2.pool.PoolError, match="timed out"):
                waiting.result(timeout=1)
            assert not unblock.is_set()
            db.close_all()
        finally:
            unblock.set()
            db.close_all()
        assert late.closed_event.wait(1), "shutdown leaked the late handshake result"


def test_timed_out_waiters_do_not_free_inflight_connection_slots(monkeypatch):
    entered = [Event(), Event()]
    unblock = Event()
    calls = []
    lock = Lock()

    def connect(**kwargs):
        with lock:
            index = len(calls)
            conn = Connection()
            calls.append(conn)
        assert index < 2, "pending handshakes exceeded maxconn"
        entered[index].set()
        assert unblock.wait(5)
        return conn

    monkeypatch.setattr(psycopg2, "connect", connect)
    monkeypatch.setattr(postgres, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.1)
    db = build_db(minconn=0, maxconn=2)
    try:
        with ThreadPoolExecutor(max_workers=8) as threads:
            waiting = [threads.submit(db.get_connection) for _ in range(8)]
            assert all(event.wait(1) for event in entered)
            for future in waiting:
                with pytest.raises(psycopg2.pool.PoolError):
                    future.result(timeout=1)
        # Another wave must not replace still-running handshakes.
        with pytest.raises(psycopg2.pool.PoolError):
            db.get_connection()
        assert len(calls) == 2
        unblock.set()
        conn = db.get_connection()
        assert conn in calls
        db.release_connection(conn)
    finally:
        db.close_all()
        unblock.set()
    assert all(conn.closed_event.wait(1) for conn in calls)


def test_failed_handshake_releases_reservation_and_records_failure(monkeypatch, caplog):
    attempts = 0
    healthy = Connection()

    def connect(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise psycopg2.OperationalError("secret DSN must not appear in pool telemetry")
        return healthy

    monkeypatch.setattr(psycopg2, "connect", connect)
    db = build_db(minconn=0, maxconn=1)
    observations = []
    monkeypatch.setattr(
        db._activity, "record_call", lambda operation, **kw: observations.append((operation, kw))
    )
    try:
        with pytest.raises(psycopg2.OperationalError, match="establishment failed"):
            db.get_connection()
        assert attempts == 1  # no hidden retry storm
        for operation in ("connect", "pool_acquire", "pool_wait"):
            assert any(op == operation and values["failed"] for op, values in observations)
        assert "secret DSN" not in caplog.text
        record = next(r for r in caplog.records if r.message == "postgres_pool_connection_failed")
        assert record.pool_role == db.role
        assert record.error_type == "OperationalError"
        assert db.get_connection() is healthy
        assert attempts == 2
    finally:
        db.close_all()


def test_partial_startup_failure_closes_connections_before_retry(monkeypatch):
    healthy = Connection()
    attempts = 0

    def connect(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return healthy
        raise psycopg2.OperationalError("startup handshake failed")

    monkeypatch.setattr(psycopg2, "connect", connect)
    with pytest.raises(psycopg2.OperationalError, match="startup handshake"):
        build_db(minconn=2, maxconn=2)
    assert healthy.closed


def test_shutdown_wakes_capacity_waiters_without_retrying(monkeypatch):
    monkeypatch.setattr(psycopg2, "connect", lambda **kw: Connection())
    db = build_db(minconn=1, maxconn=1)
    db.get_connection()
    with ThreadPoolExecutor(max_workers=1) as threads:
        waiting = threads.submit(db.get_connection)
        db.close_all()
        with pytest.raises(psycopg2.pool.PoolError, match="closed"):
            waiting.result(timeout=1)


def test_parallel_borrowers_never_share_connections_or_exceed_capacity(monkeypatch):
    created = []
    lock = Lock()
    active = set()
    barrier = Barrier(4)

    def connect(**kwargs):
        conn = Connection()
        with lock:
            created.append(conn)
        return conn

    monkeypatch.setattr(psycopg2, "connect", connect)
    db = build_db(minconn=0, maxconn=4)

    def borrow():
        for _ in range(10):
            with db.connection() as conn:
                with lock:
                    assert id(conn) not in active
                    active.add(id(conn))
                barrier.wait(timeout=2)
                with lock:
                    active.remove(id(conn))

    try:
        with ThreadPoolExecutor(max_workers=4) as threads:
            futures = [threads.submit(borrow) for _ in range(4)]
            for future in futures:
                future.result(timeout=5)
        assert len(created) == 4
        assert db._held_by_thread == {}
        assert not active
    finally:
        db.close_all()
    assert all(conn.closed for conn in created)


def test_real_libpq_handshake_cannot_hold_the_request_past_its_wait_budget(monkeypatch):
    """An accepting TCP peer that never speaks PostgreSQL reproduces the wire stall."""
    accepted, unblock = Event(), Event()
    monkeypatch.setattr(postgres, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.1)
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=2) as threads:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)

        def silent_peer():
            conn, _ = listener.accept()
            with conn:
                accepted.set()
                assert unblock.wait(3)

        server = threads.submit(silent_peer)
        db = PostgresDB(
            dbname="test",
            user="test",
            password="test",
            host="127.0.0.1",
            port=listener.getsockname()[1],
            minconn=0,
            maxconn=1,
            connect_retries=1,
            connect_timeout=2,
        )
        waiting = threads.submit(db.get_connection)
        try:
            assert accepted.wait(1)
            with pytest.raises(psycopg2.pool.PoolError, match="timed out"):
                waiting.result(timeout=1)
            assert not unblock.is_set()
        finally:
            db.close_all()
            unblock.set()
            server.result(timeout=1)


def test_connector_does_not_inherit_the_borrowers_tenant_context(monkeypatch):
    tenant = ContextVar("pool_test_tenant", default=None)
    observed = []

    def connect(**kwargs):
        observed.append(tenant.get())
        return Connection()

    monkeypatch.setattr(psycopg2, "connect", connect)
    db = build_db(minconn=0, maxconn=1)
    token = tenant.set("tenant-a")
    try:
        with db.connection():
            assert observed == [None]
            assert tenant.get() == "tenant-a"
    finally:
        tenant.reset(token)
        db.close_all()


def test_discard_retains_capacity_until_the_socket_is_closed(monkeypatch):
    closing, unblock = Event(), Event()
    first = Connection()
    second = Connection()
    connections = iter([first, second])
    monkeypatch.setattr(psycopg2, "connect", lambda **kw: next(connections))
    monkeypatch.setattr(postgres, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.1)
    db = build_db(minconn=1, maxconn=1)
    borrowed = db.get_connection()
    original_close = first.close

    def slow_close():
        closing.set()
        assert unblock.wait(3)
        original_close()

    monkeypatch.setattr(first, "close", slow_close)
    with ThreadPoolExecutor(max_workers=2) as threads:
        discarded = threads.submit(db.discard_connection, borrowed)
        try:
            assert closing.wait(1)
            waiting = threads.submit(db.get_connection)
            with pytest.raises(psycopg2.pool.PoolError, match="timed out"):
                waiting.result(timeout=1)
        finally:
            unblock.set()
            discarded.result(timeout=1)
        # The slot is usable again only after the old socket actually closes.
        acquired = threads.submit(db.get_connection).result(timeout=1)
        assert acquired is second
        db.close_all()
