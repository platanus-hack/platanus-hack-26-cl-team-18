"""Postgres connection pool wrapper.

Architectural intent:
- Provide a thin, reusable pool for infrastructure repositories and UoWs.
- Keep pooling configuration centralized and deterministic.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

from psycopg2 import OperationalError, ProgrammingError, pool
from psycopg2.extensions import (
    TRANSACTION_STATUS_IDLE,
    connection as PGConnection,
    cursor as PGCursor,
)
from psycopg2.extras import register_uuid

from app.modules.common.infrastructure.postgres.base import IPostgresDB
from app.modules.common.infrastructure.postgres.connection_pool import (
    ConnectionPool,
    ConnectionPoolClosed,
)
from app.modules.common.infrastructure.postgres.observed_connection import ObservedConnection
from app.modules.common.infrastructure.postgres.pool_activity import PoolActivity
from app.modules.common.infrastructure.request_performance import RequestStage, timed_stage

logger = logging.getLogger(__name__)

# Maximum seconds to wait for a connection to become available in the pool
# before raising PoolError (prevents indefinite hangs under load).
_POOL_ACQUIRE_TIMEOUT_SECONDS = 10.0

# Log a warning when pool utilization exceeds this fraction (e.g. 0.8 = 80%).
_POOL_UTILIZATION_WARN_THRESHOLD = 0.8
# Utilization is a ratio, and a ratio carries no information about headroom once the
# denominator is tiny. Worker pools run at maxconn 1-2, where a single in-flight
# checkout is already 50-100% -- their designed steady state. Warning there produced
# a continuous stream of WARNINGs that said nothing and buried the failures that did
# (pool exhaustion). Only pools large enough for the ratio to mean spare capacity are
# eligible for the utilization warning.
_POOL_UTILIZATION_WARN_MIN_MAXCONN = 4

# Environment variables that name the process holding the pool, in priority
# order.  ``WORKER_NAME`` names a background worker; ``RENDER_SERVICE_NAME`` is
# Render's own name for the web service.  Without it, ``postgres_pool_exhausted``
# says only ``max: 2`` -- true of every process -- and attributing an exhaustion
# means correlating ``command_id`` across neighbouring log lines by hand.
_POOL_ROLE_ENV_VARS = ("WORKER_NAME", "RENDER_SERVICE_NAME")


def _pool_role() -> str:
    for name in _POOL_ROLE_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return "unknown"


# Pooled connections can be killed by the database or network while idle.  Check
# connections that have been sitting in the pool before handing them to business
# queries, so long-running workers do not surface avoidable first-query failures.
_POOL_IDLE_HEALTH_CHECK_SECONDS = 30.0

# Every query here is tenant-scoped and finishes in milliseconds, so LLVM JIT
# never pays for its compile time. It still fires whenever a misestimated plan
# crosses jit_above_cost: a generate_series bucket join or a correlated subplan
# is enough, and aggregate queries that execute in ~100 ms can spend ~1 s compiling.
# A startup option is the
# session default, so the per-request RESET ALL keeps it off.
_SESSION_STARTUP_OPTIONS = "-c jit=off"

# TCP keepalives on every pooled socket. The idle-ping above is skipped for
# connections released less than ``_POOL_IDLE_HEALTH_CHECK_SECONDS`` ago, so a
# worker polling every few seconds never pings at all; without keepalives a peer
# that vanished silently (NAT/proxy flow dropped, managed-database failover) is only
# discovered by the next real query, and an in-flight read can block until the
# kernel's default (~2h) gives up. With these, the kernel probes after 30s of
# silence and declares the socket dead after ~30 + 3*10 = 60s, which surfaces as
# an ``OperationalError`` the shared worker-loop guard and the UoW's
# retry-once-on-a-fresh-connection already handle. libpq ignores keepalive
# settings on Unix-domain sockets (local dev), so they are harmless there.
_DEFAULT_KEEPALIVES_IDLE_SECONDS = 30
_DEFAULT_KEEPALIVES_INTERVAL_SECONDS = 10
_DEFAULT_KEEPALIVES_COUNT = 3


class PostgresDB(IPostgresDB):
    """Concrete Postgres pool implementation for infrastructure.

    Best practices:
    - Keep pooling logic here (no business queries).
    - Use UoW for transactional writes.
    - Use connection() for ad-hoc read-only work when needed.
    """

    def __init__(
        self,
        dbname: str,
        user: str,
        password: str,
        host: str,
        port: int = 5432,
        sslmode: str | None = None,
        minconn: int = 1,
        maxconn: int = 10,
        connect_retries: int = 10,
        connect_retry_delay: float = 0.5,
        connect_retry_max_delay: float = 5.0,
        # Seconds to wait for a TCP connection to the Postgres server.
        # Prevents indefinite hangs when the database host is unreachable.
        connect_timeout: int = 10,
        keepalives_idle: int = _DEFAULT_KEEPALIVES_IDLE_SECONDS,
        keepalives_interval: int = _DEFAULT_KEEPALIVES_INTERVAL_SECONDS,
        keepalives_count: int = _DEFAULT_KEEPALIVES_COUNT,
    ):
        # Ensure uuid columns are decoded as uuid.UUID (not plain strings).
        register_uuid()
        self._maxconn = maxconn
        self._role = _pool_role()
        self._activity = PoolActivity(role=self._role)
        self._used: set = set()
        self._last_released_at: dict[int, float] = {}
        # How many pooled connections each thread currently holds *at once*.
        #
        # This is the one number that separates the two failure modes hiding
        # behind an identical PoolError.  Many threads holding one connection
        # each is contention: real load, tune the pool.  One thread holding
        # ``maxconn`` of them is a *deterministic* starve -- a single task whose
        # nested units of work need more connections than the pool can ever
        # hand out, so it deadlocks against itself and no amount of waiting
        # helps.  ``PostgresUnitOfWork.__enter__`` refuses to re-enter a live
        # connection, so every nesting level costs one more connection and the
        # floor is a property of the code path, not of the traffic.
        self._held_by_thread: dict[int, int] = {}
        self._held_lock = threading.Lock()
        last_error: OperationalError | None = None
        for attempt in range(connect_retries):
            try:
                # FastAPI resolves some dependencies in worker threads. The pool
                # therefore must be thread-safe or concurrent requests can borrow
                # the same psycopg connection and trip "set_session cannot be
                # used inside a transaction" on the next UoW.
                self.pool = ConnectionPool(
                    minconn,
                    maxconn,
                    activity=self._activity,
                    role=self._role,
                    connection_factory=partial(ObservedConnection, activity=self._activity),
                    dbname=dbname,
                    user=user,
                    password=password,
                    host=host,
                    port=port,
                    sslmode=sslmode,
                    connect_timeout=connect_timeout,
                    keepalives=1,
                    keepalives_idle=keepalives_idle,
                    keepalives_interval=keepalives_interval,
                    keepalives_count=keepalives_count,
                    options=_SESSION_STARTUP_OPTIONS,
                    application_name=f"app:{self._role}"[:63],
                )
                return
            except OperationalError as exc:
                last_error = exc
                if attempt == connect_retries - 1:
                    break
                delay = min(connect_retry_delay * (2**attempt), connect_retry_max_delay)
                time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise OperationalError(
            f"Failed to establish a database connection pool: connect_retries={connect_retries} "
            "exhausted no attempts (connect_retries must be >= 1)."
        )

    @timed_stage(RequestStage.DB_CHECKOUT)
    def get_connection(self) -> PGConnection:
        """Acquire a connection from the pool.

        Waiting for capacity or a new handshake is bounded by one deadline.
        Connection validation still uses synchronous driver I/O; its duration
        is observed separately and is not a hard socket-read deadline.
        Emits a WARNING when pool utilization exceeds the configured threshold.
        """
        acquisition_started = time.monotonic()
        try:
            return self._acquire_connection(acquisition_started)
        except Exception:
            self._activity.record_call(
                "pool_wait", elapsed=time.monotonic() - acquisition_started, failed=True
            )
            raise

    def _acquire_connection(self, acquisition_started: float) -> PGConnection:
        deadline = acquisition_started + _POOL_ACQUIRE_TIMEOUT_SECONDS

        while True:
            try:
                # A caller holding the whole pool cannot be helped by waiting.
                timeout = (
                    0.0
                    if self._held_by_caller() >= self._maxconn
                    else max(0.0, deadline - time.monotonic())
                )
                conn = self.pool.getconn(timeout=timeout)
                if not self._prepare_connection_for_checkout(
                    conn
                ) or not self._connection_is_usable(conn):
                    self._discard_connection(conn)
                    # Recovery from broken sockets shares the same checkout
                    # budget as contention. Repeated discards previously
                    # continued forever without reaching the deadline check.
                    # Validation is synchronous; a single driver operation
                    # can still exceed the wait budget.
                    if time.monotonic() >= deadline:
                        raise pool.PoolError(
                            "No healthy database connection within checkout budget"
                        )
                    continue
                self._used.add(id(conn))
                self._record_checkout()
                self._activity.record("checkout")
                self._activity.acquired(
                    id(conn), waited_seconds=time.monotonic() - acquisition_started
                )
                utilization = len(self._used) / self._maxconn
                if (
                    self._maxconn >= _POOL_UTILIZATION_WARN_MIN_MAXCONN
                    and utilization >= _POOL_UTILIZATION_WARN_THRESHOLD
                ):
                    logger.warning(
                        "postgres_pool_high_utilization",
                        extra={
                            "pool_role": self._role,
                            "held_by_caller": self._held_by_caller(),
                            "used": len(self._used),
                            "max": self._maxconn,
                            "utilization_pct": round(utilization * 100, 1),
                        },
                    )
                return conn
            except ConnectionPoolClosed:
                raise
            except pool.PoolError:
                held_by_caller = self._held_by_caller()
                if held_by_caller >= self._maxconn:
                    # Waiting cannot help: the caller is holding every
                    # connection it is waiting for.  Fail immediately and say
                    # so, rather than burning the full acquire timeout and then
                    # reporting the same exhaustion an overloaded pool reports.
                    logger.error(
                        "postgres_pool_self_starved",
                        extra={
                            "role": self._role,
                            "max": self._maxconn,
                            "held_by_caller": held_by_caller,
                            "used": len(self._used),
                        },
                    )
                    raise
                # The pool already waited on its condition for the remaining
                # budget. Do not start another polling/retry budget here.
                logger.error(
                    "postgres_pool_exhausted",
                    extra={
                        "role": self._role,
                        "max": self._maxconn,
                        "used": len(self._used),
                        "held_by_caller": held_by_caller,
                        "timeout_seconds": _POOL_ACQUIRE_TIMEOUT_SECONDS,
                    },
                )
                raise

    def _record_checkout(self) -> None:
        key = threading.get_ident()
        with self._held_lock:
            self._held_by_thread[key] = self._held_by_thread.get(key, 0) + 1

    def _record_release(self) -> None:
        key = threading.get_ident()
        with self._held_lock:
            remaining = self._held_by_thread.get(key, 0) - 1
            if remaining > 0:
                self._held_by_thread[key] = remaining
            else:
                # Never let the map grow one entry per thread that ever ran.
                self._held_by_thread.pop(key, None)

    def _held_by_caller(self) -> int:
        with self._held_lock:
            return self._held_by_thread.get(threading.get_ident(), 0)

    @property
    def role(self) -> str:
        """The process this pool belongs to, as it appears in pool log lines."""
        return self._role

    @timed_stage(RequestStage.DB_RELEASE)
    def release_connection(self, conn: PGConnection) -> None:
        if id(conn) in self._used:
            self._record_release()
        self._used.discard(id(conn))
        try:
            # Always hand a clean connection back to the pool. A connection
            # whose server-side transaction status is IDLE has nothing to
            # undo, so the rollback is skipped; any other status (INTRANS,
            # INERROR, UNKNOWN, or a status we cannot read) is rolled back so a
            # pooled connection never carries transaction state into the next
            # request.
            try:
                tx_status_getter = getattr(conn, "get_transaction_status", None)
                tx_status = tx_status_getter() if callable(tx_status_getter) else None
                if tx_status != TRANSACTION_STATUS_IDLE:
                    self._activity.record("cleanup_rollback")
                    conn.rollback()
            except OperationalError, ProgrammingError:
                self._discard_connection(conn)
                return

            try:
                conn.autocommit = True
            except OperationalError, ProgrammingError:
                self._discard_connection(conn)
                return

            # Drop any session-scoped GUC this holder set, so the next caller
            # cannot inherit it. A worker that raises an `app.*` setting with
            # SET SESSION and lowers it again in a `finally` that merely logs
            # when the RESET fails would otherwise hand that state -- and
            # possibly cross-org visibility -- to whichever request picked the
            # connection up next.
            try:
                with conn.cursor() as cur:
                    self._activity.record("reset")
                    cur.execute("RESET ALL")
            except OperationalError, ProgrammingError:
                self._discard_connection(conn)
                return

            self._last_released_at[id(conn)] = time.monotonic()
            self._activity.returned(id(conn))
            self.pool.putconn(conn)
        except Exception:
            self._discard_connection(conn)

    def discard_connection(self, conn: PGConnection) -> None:
        """Close a connection the caller knows is broken instead of pooling it.

        ``release_connection`` would try to roll back and ``RESET ALL`` first;
        for a socket the server already closed that is wasted round trips at
        best. Callers that just saw ``OperationalError``/``InterfaceError`` on
        ``conn`` use this so the next checkout opens a fresh connection.
        """
        self._discard_connection(conn)

    def _discard_connection(self, conn: PGConnection) -> None:
        """Return a broken connection to the pool closed, without reusing it."""
        self._activity.record("discard")
        self._activity.returned(id(conn))
        if id(conn) in self._used:
            self._record_release()
        self._used.discard(id(conn))
        self._last_released_at.pop(id(conn), None)
        try:
            self.pool.putconn(conn, close=True)
        except Exception:
            logger.warning("postgres.pool.discard_putconn_failed", exc_info=True)

    def _prepare_connection_for_checkout(self, conn: PGConnection) -> bool:
        """Normalize pooled connection state before handing it to callers.

        A previous caller may have leaked an open transaction or left autocommit
        disabled. Reset that state here so the next UoW can safely configure
        the connection without tripping psycopg's `set_session` guard.
        """
        try:
            tx_status_getter = getattr(conn, "get_transaction_status", None)
            tx_status = tx_status_getter() if callable(tx_status_getter) else None
            should_rollback = tx_status not in (None, TRANSACTION_STATUS_IDLE)
            if should_rollback or not getattr(conn, "autocommit", False):
                self._activity.record("cleanup_rollback")
                conn.rollback()
            conn.autocommit = True
            # Backstop for the GUC sanitation that release_connection performs.
            # A connection with no clean-release record never went through that
            # path -- it leaked, or its holder crashed -- so it may still carry
            # session state, such as an `app.*` setting a worker raised and
            # never lowered. Sanitize it here before anyone can read through
            # it. Connections that were released cleanly are already clean, so
            # this costs no extra round trip on the hot path (see
            # test_get_connection_does_not_ping_recently_released_connection).
            if id(conn) not in self._last_released_at:
                with conn.cursor() as cur:
                    self._activity.record("reset")
                    cur.execute("RESET ALL")
            return True
        except OperationalError, ProgrammingError:
            logger.warning("postgres_pool_discarded_dirty_connection")
            return False

    def _connection_is_usable(self, conn: PGConnection) -> bool:
        """Return whether a pooled connection is safe to hand to callers."""
        if getattr(conn, "closed", False):
            return False

        last_released_at = self._last_released_at.get(id(conn))
        if last_released_at is None:
            return True

        idle_seconds = time.monotonic() - last_released_at
        if idle_seconds < _POOL_IDLE_HEALTH_CHECK_SECONDS:
            return True

        try:
            with conn.cursor() as cur:
                self._activity.record("health_check")
                cur.execute("SELECT 1")
        except OperationalError:
            logger.warning(
                "postgres_pool_discarded_stale_connection",
                extra={"idle_seconds": round(idle_seconds, 1)},
            )
            return False
        return True

    def close_all(self) -> None:
        self.pool.closeall()

    def execute(
        self,
        query: str,
        params: Any = None,
        *,
        fetch: bool = False,
        fetchone: bool = False,
        fetchall: bool = False,
    ) -> Any:
        conn = self.get_connection()
        broken = False
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(query, params)
                if fetchone:
                    return cur.fetchone()
                if fetch or fetchall:
                    return cur.fetchall()
                return None
        except OperationalError:
            broken = True
            raise
        finally:
            if broken:
                # Discard the broken connection so the pool creates a fresh one
                # on the next acquire instead of serving stale dead connections.
                self._discard_connection(conn)
            else:
                self.release_connection(conn)

    @contextmanager
    def cursor(self) -> Iterator[PGCursor]:
        """Yield a cursor from a pooled connection for short-lived work.

        Used by integrations repositories that operate outside the UoW pattern
        (e.g. ``ApiTokenRepositoryPostgres``).
        """
        conn = self.get_connection()
        broken = False
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                yield cur
        except OperationalError:
            broken = True
            raise
        finally:
            if broken:
                self._discard_connection(conn)
            else:
                self.release_connection(conn)

    @contextmanager
    def connection(self) -> Iterator[PGConnection]:
        """Yield a pooled connection for short-lived, read-only work.

        The handle is in **autocommit**: every statement commits as it executes
        and ``conn.rollback()`` undoes nothing. Callers that write must use
        :meth:`transaction` instead — a write here is committed the moment it
        runs, whatever the caller intends afterwards.
        """
        conn = self.get_connection()
        broken = False
        try:
            conn.autocommit = True
            yield conn
        except OperationalError:
            broken = True
            raise
        finally:
            if broken:
                self._discard_connection(conn)
            else:
                self.release_connection(conn)

    @contextmanager
    def transaction(self) -> Iterator[PGConnection]:
        """Yield a pooled connection in a real transaction, for write work.

        Commits on clean exit, rolls back on any exception, and restores
        autocommit before the connection returns to the pool so the next
        borrower sees the pool's normal contract.

        This exists because :meth:`connection` yields an autocommit handle:
        code that wrote through it and then called ``rollback()`` to undo the
        writes — the natural shape of a script's dry run — silently persisted
        them instead. Rather than have each caller remember to toggle
        ``autocommit`` by hand, atomicity is a property of the handle you ask
        for.
        """
        conn = self.get_connection()
        broken = False
        try:
            conn.autocommit = False
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.autocommit = True
        except OperationalError:
            broken = True
            raise
        finally:
            if broken:
                self._discard_connection(conn)
            else:
                self.release_connection(conn)
