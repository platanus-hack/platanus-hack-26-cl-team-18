"""Bounded psycopg2 pool whose bookkeeping lock never covers network I/O.

This is the unkeyed pool used by PostgresDB, not a replacement for psycopg's
full keyed-pool API. PostgresDB owns transaction and session sanitation.
"""

from __future__ import annotations

import logging
import threading
import time
from contextvars import Context
from typing import Any

import psycopg2
from psycopg2.extensions import connection
from psycopg2.pool import PoolError

from app.modules.common.infrastructure.postgres.pool_activity import PoolActivity

logger = logging.getLogger(__name__)


class ConnectionPoolClosed(PoolError):
    """Shutdown is terminal, not contention to retry."""


class ConnectionPool:
    """Keep connected + connecting sockets within maxconn, including late results.

    A request waits on a condition, never on libpq's handshake. At most maxconn
    daemon threads can establish connections, and each reserves a slot until
    it finishes, even if its borrower times out. A returned healthy connection
    can satisfy the waiter immediately. There is no background retry loop.

    Idle connections are retained up to maxconn to avoid reconnecting on every
    burst. The existing PostgresDB idle validation still applies. No SQL runs
    on connector threads, and a connection is handed to only one borrower.
    """

    def __init__(
        self, minconn: int, maxconn: int, *, activity: PoolActivity, role: str, **kwargs: Any
    ) -> None:
        if not 0 <= minconn <= maxconn or maxconn < 1:
            raise ValueError("Require 0 <= minconn <= maxconn and maxconn >= 1")
        if kwargs.get("connect_timeout", 0) <= 0:
            raise ValueError("A positive connect_timeout is required")
        self._maxconn = maxconn
        self._kwargs = kwargs
        self._activity = activity
        self._role = role
        self._condition = threading.Condition()
        self._idle: list[connection] = []
        self._borrowed: dict[int, connection] = {}
        self._opening = 0
        self._closing = 0
        self._waiters = 0
        self._failure_generation = 0
        self._last_error: Exception | None = None
        self.closed = False
        # Preserve fail-fast startup and PostgresDB's bounded startup retries.
        # A partial warmup must not leak sockets when a later connect fails.
        try:
            for _ in range(minconn):
                self._idle.append(self._connect())
        except BaseException:
            self.closeall()
            raise

    def _connect(self) -> connection:
        started = time.monotonic()
        failed = True
        try:
            conn = psycopg2.connect(**self._kwargs)
            failed = False
            return conn
        except Exception as exc:
            # Never log a DSN, credentials or the provider's error text.
            logger.warning(
                "postgres_pool_connection_failed",
                extra={
                    "pool_role": self._role,
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                },
            )
            raise
        finally:
            self._activity.record_call("connect", elapsed=time.monotonic() - started, failed=failed)

    def getconn(self, *, timeout: float = 10.0) -> connection:
        started = time.monotonic()
        failed = True
        try:
            conn = self._getconn(deadline=started + timeout)
            failed = False
            return conn
        finally:
            self._activity.record_call(
                "pool_acquire", elapsed=time.monotonic() - started, failed=failed
            )

    def _getconn(self, *, deadline: float) -> connection:
        with self._condition:
            generation = self._failure_generation
            self._waiters += 1
            try:
                while True:
                    if self.closed:
                        raise ConnectionPoolClosed("connection pool is closed")
                    if self._idle:
                        conn = self._idle.pop()
                        self._borrowed[id(conn)] = conn
                        return conn
                    if self._failure_generation != generation:
                        raise psycopg2.OperationalError(
                            "Database connection establishment failed"
                        ) from self._last_error
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PoolError("Database connection checkout timed out")
                    size = len(self._borrowed) + self._opening + self._closing
                    if size < self._maxconn and self._opening < self._waiters:
                        self._opening += 1
                        try:
                            threading.Thread(
                                target=self._open_reserved_connection,
                                name="postgres-pool-connect",
                                daemon=True,
                                # A late handshake belongs to the pool, not to
                                # a request/tenant whose context may have ended.
                                context=Context(),
                            ).start()
                        except BaseException:
                            self._opening -= 1
                            self._condition.notify_all()
                            raise
                    self._condition.wait(remaining)
            finally:
                self._waiters -= 1

    def _open_reserved_connection(self) -> None:
        conn: connection | None = None
        error: Exception | None = None
        try:
            conn = self._connect()
        except Exception as exc:
            error = exc
        finally:
            with self._condition:
                self._opening -= 1
                if error is not None:
                    self._failure_generation += 1
                    self._last_error = error
                if conn is not None and not self.closed:
                    self._idle.append(conn)
                    conn = None  # pool now owns it
                self._condition.notify_all()
            # Shutdown may race with a successful handshake. Never resurrect
            # the pool or leak the late connection, and never close under lock.
            if conn is not None:
                conn.close()

    def putconn(self, conn: connection, *, close: bool = False) -> None:
        with self._condition:
            if self.closed:
                close = True
            elif self._borrowed.pop(id(conn), None) is not conn:
                raise PoolError("Cannot return a connection not borrowed from this pool")
            elif not close and not conn.closed:
                self._idle.append(conn)
            if close:
                self._closing += 1
            self._condition.notify_all()
        if close:
            try:
                conn.close()
            finally:
                with self._condition:
                    self._closing -= 1
                    self._condition.notify_all()

    def closeall(self) -> None:
        with self._condition:
            if self.closed:
                return
            self.closed = True
            connections = [*self._idle, *self._borrowed.values()]
            self._idle.clear()
            self._borrowed.clear()
            self._condition.notify_all()
        for conn in connections:
            conn.close()
