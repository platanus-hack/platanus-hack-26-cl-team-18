from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

import psycopg2
import psycopg2.extensions
import psycopg2.extras

from app.config.logging_context import get_context
from app.modules.common.application.integration_event import IntegrationEvent
from app.modules.common.application.ports.outbox import IOutboxRepository
from app.modules.common.application.uow import AbstractUnitOfWork
from app.modules.common.domain.primitives.events import DomainEvent
from app.modules.common.infrastructure.outbox.event_mapper import (
    DomainEventToIntegrationEventMapper,
)
from app.modules.common.infrastructure.outbox.repository_write import OutboxRepositoryPostgres
from app.modules.common.infrastructure.postgres.base import IPostgresDB

"""
Postgres Unit of Work implementation (with Transactional Outbox).

Key changes implemented:
- Matches AbstractUnitOfWork contract: _commit(self, events)
- Persists integration events to the outbox *within the same DB transaction*
  as aggregate writes, before COMMIT.
- Adds hygiene + safety around isolation/timeout configuration.
- Keeps repo cache + event draining in AbstractUnitOfWork.
- Leaves publishing to a separate outbox dispatcher/worker.
"""


class PostgresUnitOfWork(AbstractUnitOfWork):
    """Concrete UoW over a psycopg2 connection + Transactional Outbox.

    Responsibilities:
    - Open a pooled connection
    - Configure per-tx settings with SET LOCAL (timeout, RLS vars)
    - Drain domain events once (done by AbstractUnitOfWork.commit())
    - Map domain events -> integration events
    - Insert outbox rows idempotently in same transaction
    - Commit/rollback and return connection to pool
    """

    def __init__(
        self,
        db: IPostgresDB,
        *,
        # Context vars (used for RLS / auditing)
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
        # Outbox integration
        outbox_repo_factory: Callable[[Any], IOutboxRepository],
        event_mapper: DomainEventToIntegrationEventMapper,
        # Tx settings
        statement_timeout: str = "10s",
        isolation_level: str = "SERIALIZABLE",  # Charges ledger and budgets: prioritize correctness over performance
        read_only: bool = False,
        # Logging
        logger: logging.Logger | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id

        self._outbox_repo_factory = outbox_repo_factory
        self._event_mapper = event_mapper
        self._outbox_repo: IOutboxRepository | None = None

        self.statement_timeout = statement_timeout
        self.isolation_level = isolation_level
        self.read_only = read_only
        self.logger = logger or logging.getLogger(__name__)

        self.conn = None
        self.started_at: float | None = None
        # Set when a rollback found the connection already gone.  It carries that
        # discovery from `rollback()` to `__exit__`, which owns the decision to
        # discard the connection rather than hand it back to the pool.
        self._connection_lost = False
        self._committed = False
        self._has_writes = False
        self._repo_cache = {}
        self._post_commit_hooks = []
        self._logged_commit_conflict: BaseException | None = None

    def __enter__(self) -> PostgresUnitOfWork:
        if self.conn is not None:
            raise RuntimeError(
                "PostgresUnitOfWork cannot be entered while it already has an active "
                "connection; create a fresh UnitOfWork for nested transaction phases.",
            )
        super().__enter__()
        self._logged_commit_conflict = None
        self.started_at = time.monotonic()
        iso = self._validated_isolation_level()
        # A pooled connection can be dead without the pool knowing: its idle
        # ping is skipped for connections released < 30s ago, which is every
        # connection a polling worker ever uses. The first statement on such a
        # socket fails with "server closed the connection unexpectedly". Nothing
        # has run in the transaction yet, so the failure is safe to absorb once:
        # discard the dead connection and begin again on a fresh one. A second
        # failure is a real outage and propagates.
        for attempt in (1, 2):
            acquisition_started_at = time.monotonic()
            conn = cast(Any, self.db.get_connection())
            pool_acquisition_ms = int((time.monotonic() - acquisition_started_at) * 1000)
            self.conn = conn
            self._connection_lost = False
            try:
                # Pooled connections arrive with autocommit=True; the per-tx
                # set_config(..., true) calls below are no-ops without this.
                conn.autocommit = False
                self._begin_transaction(conn, iso)
                # Build per-tx outbox repo (uses this connection/tx)
                self._outbox_repo = self._outbox_repo_factory(self.conn)
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                self._rollback_quietly(conn)
                self._discard_broken_connection(conn)
                self.conn = None
                if attempt == 1 and _is_stale_connection_error(exc):
                    self.logger.warning(
                        "uow.begin_retrying_on_fresh_connection",
                        extra={
                            "organization_id": self.organization_id,
                            "error": repr(exc),
                        },
                    )
                    continue
                raise
            except psycopg2.DatabaseError:
                # A live connection that refused the statement: hand it back
                # clean (release rolls back and resets it) rather than closing.
                self._rollback_quietly(conn)
                self.db.release_connection(conn)
                self.conn = None
                raise

            self.logger.debug(
                "transaction_started",
                extra={
                    "organization_id": self.organization_id,
                    "user_id": self.user_id,
                    "pool_acquisition_ms": pool_acquisition_ms,
                    "isolation_level": iso,
                    "read_only": self.read_only,
                    "begin_attempt": attempt,
                },
            )
            return self
        raise AssertionError("unreachable: the begin loop returns or raises")

    def _validated_isolation_level(self) -> str:
        allowed = {"READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"}
        iso = (self.isolation_level or "").upper().strip()
        if iso not in allowed:
            raise ValueError(
                f"Invalid isolation_level={self.isolation_level!r}. Allowed: {sorted(allowed)}",
            )
        return iso

    def _begin_transaction(self, conn: Any, iso: str) -> None:
        """Apply every per-tx setting in one round trip (autocommit already off)."""
        with conn.cursor() as cur:
            # Transaction characteristics must be set before any ordinary query.
            read_mode = "READ ONLY" if self.read_only else "READ WRITE"

            # Every per-tx setting travels in ONE round trip: the transaction
            # characteristics first (they must precede any ordinary query),
            # then a single SELECT of set_config(..., is_local => true), which
            # is exactly SET LOCAL. Issued one statement at a time these were
            # 7-9 round trips on every transaction the app opens.
            #
            # statement_timeout is parameterized; search_path is pinned to
            # prevent contamination from pooled connections reused across
            # requests; the app.* GUCs feed RLS and auditing.
            local_settings: list[tuple[str, str]] = [
                ("statement_timeout", str(self.statement_timeout)),
                ("search_path", "public, pg_catalog"),
            ]
            if self.organization_id:
                local_settings.append(("app.current_organization", str(self.organization_id)))
            if self.user_id:
                local_settings.append(("app.user_id", str(self.user_id)))
                local_settings.append(("app.actor_id", str(self.user_id)))
            request_id = get_context().get("request_id")
            if request_id:
                local_settings.append(("app.request_id", str(request_id)))

            set_config_calls = ", ".join("set_config(%s, %s, true)" for _ in local_settings)
            cur.execute(
                f"SET TRANSACTION ISOLATION LEVEL {iso}, {read_mode}; SELECT {set_config_calls}",
                tuple(value for pair in local_settings for value in pair),
            )

    @staticmethod
    def _rollback_quietly(conn: Any) -> None:
        # The connection may already be dead (InterfaceError), so swallow.
        try:
            conn.rollback()
        except psycopg2.Error:
            pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Delegate commit/rollback decision to AbstractUnitOfWork.__exit__.
        Always return connection to pool.
        """
        # Mid-transaction serialization/deadlock conflicts surface here (a SELECT
        # or UPDATE raises, propagates past the caller, and triggers __exit__).
        # Emit the conflict log before rollback so commit-phase and statement-phase
        # conflicts show up under the same greppable event name.
        if exc is not self._logged_commit_conflict and isinstance(
            exc,
            (psycopg2.errors.SerializationFailure, psycopg2.errors.DeadlockDetected),
        ):
            duration_ms = (
                int((time.monotonic() - self.started_at) * 1000) if self.started_at else None
            )
            self.logger.warning(
                "transaction_serialization_conflict",
                extra={
                    "organization_id": self.organization_id,
                    "user_id": self.user_id,
                    "duration_ms": duration_ms,
                    "has_writes": self.has_writes,
                    "sqlstate": getattr(exc, "pgcode", None),
                    "conflict_phase": "statement",
                    "isolation_level": self.isolation_level,
                    "read_only": self.read_only,
                    "pool_role": os.getenv("WORKER_NAME")
                    or os.getenv("RENDER_SERVICE_NAME", "unknown"),
                },
            )
        try:
            super().__exit__(exc_type, exc, tb)
        finally:
            # Do not retain the exception traceback and its transaction objects.
            self._logged_commit_conflict = None
            if self.conn is not None:
                # `rollback()` already ran on the uncommitted path and may have
                # found the connection gone.  That verdict is final: re-probing
                # a connection known to be dead only asks the same question
                # again.
                conn_broken = self._connection_lost
                try:
                    # Ensure connection goes back to pool in a clean state.
                    # rollback() is safe even if already committed.
                    if not self.committed and not conn_broken:
                        try:
                            self.conn.rollback()
                        except psycopg2.Error:
                            # Connection is broken; mark so we discard instead of reuse.
                            conn_broken = True
                    if not conn_broken:
                        self.conn.autocommit = True
                finally:
                    if conn_broken:
                        self._discard_broken_connection(self.conn)
                    else:
                        self.db.release_connection(self.conn)
                    self.conn = None
                    self._outbox_repo = None

    # ------------------------------------------------------------------
    # Infrastructure hook: persist outbox + commit transaction
    # ------------------------------------------------------------------

    def _commit(self, events: tuple[DomainEvent, ...]) -> None:
        if not self.conn:
            raise RuntimeError("UnitOfWork has no active connection.")
        if self._outbox_repo is None:
            raise RuntimeError("Outbox repository not initialized (did you enter the UoW?).")

        duration_ms = int((time.monotonic() - self.started_at) * 1000) if self.started_at else None

        try:
            # 1) Persist outbox rows in same transaction (idempotent on event_id)
            if events and self._event_mapper is not None:
                integration_events: list[IntegrationEvent] = [
                    self._event_mapper.map(e) for e in events
                ]
                self._outbox_repo.add_all(integration_events)

            # 2) Commit aggregate state + outbox together
            commit_started_at = time.monotonic()
            self.conn.commit()
            commit_duration_ms = round((time.monotonic() - commit_started_at) * 1000, 3)

            log_extra = {
                "organization_id": self.organization_id,
                "user_id": self.user_id,
                "duration_ms": duration_ms,
                "commit_duration_ms": commit_duration_ms,
                "read_only": self.read_only,
                "has_writes": self.has_writes,
                "pool_role": os.getenv("WORKER_NAME")
                or os.getenv("RENDER_SERVICE_NAME", "unknown"),
                "domain_event_count": len(events),
            }
            if self.has_writes or events:
                self.logger.info("transaction_committed", extra=log_extra)
            else:
                self.logger.debug("transaction_committed_read_only", extra=log_extra)

        except (
            psycopg2.errors.SerializationFailure,
            psycopg2.errors.DeadlockDetected,
        ) as e:
            # Retriable-by-contract conflicts (sqlstate 40001 / 40P01). Log at WARNING
            # with a dedicated event name so the conflict rate can be measured and
            # hotspot workflows can be identified, instead of being lost inside the
            # generic commit_failed bucket.
            # __exit__ sees this same exception when commit fails inside a UoW.
            # Count it once as a commit conflict, never again as a statement one.
            self._logged_commit_conflict = e
            self.logger.warning(
                "transaction_serialization_conflict",
                extra={
                    "organization_id": self.organization_id,
                    "user_id": self.user_id,
                    "duration_ms": duration_ms,
                    "domain_event_count": len(events),
                    "has_writes": self.has_writes,
                    "sqlstate": getattr(e, "pgcode", None),
                    "conflict_phase": "commit",
                    "isolation_level": self.isolation_level,
                    "read_only": self.read_only,
                    "pool_role": os.getenv("WORKER_NAME")
                    or os.getenv("RENDER_SERVICE_NAME", "unknown"),
                },
            )
            raise
        except psycopg2.DatabaseError, psycopg2.OperationalError:
            self.logger.exception(
                "transaction_commit_failed",
                extra={
                    "organization_id": self.organization_id,
                    "user_id": self.user_id,
                    "duration_ms": duration_ms,
                    "domain_event_count": len(events),
                },
            )
            raise

    def rollback(self) -> None:
        if not self.conn:
            return
        duration_ms = int((time.monotonic() - self.started_at) * 1000) if self.started_at else None
        try:
            self.conn.rollback()
        except psycopg2.Error:
            # The connection is gone.  In production this is reached almost
            # exclusively while unwinding the exception that killed it --
            # `OperationalError: server closed the connection unexpectedly`
            # raised by whichever statement was in flight -- and `__exit__`
            # calls us on that path, so anything raised here *replaces* that
            # exception.  A background worker (the outbox relay, a delivery
            # writer) would then report `InterfaceError: connection already
            # closed`, naming neither the failing statement nor the reason,
            # with the real cause surviving only in the chained traceback.
            #
            # There is also nothing left to undo.  The server discarded the
            # transaction when it dropped the connection, so the rollback this
            # method promises has already happened by other means; reporting
            # failure would be false as well as destructive.  `__enter__`
            # already swallows exactly this on its own teardown path -- this is
            # the same rule, applied to the same situation.
            self._connection_lost = True
            self.logger.warning(
                "transaction_rollback_connection_lost",
                extra={
                    "organization_id": self.organization_id,
                    "user_id": self.user_id,
                    "duration_ms": duration_ms,
                    "has_writes": self.has_writes,
                },
            )
        finally:
            log_extra = {
                "organization_id": self.organization_id,
                "user_id": self.user_id,
                "duration_ms": duration_ms,
                "has_writes": self.has_writes,
            }
            if self.has_writes:
                self.logger.warning("transaction_rolled_back", extra=log_extra)
            else:
                self.logger.debug("transaction_rolled_back_read_only", extra=log_extra)

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        if not self.conn:
            raise RuntimeError("UnitOfWork has no active connection.")
        cur = self.conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    @contextmanager
    def dict_cursor(self) -> Iterator[Any]:
        """Cursor that returns rows as dicts (keyed by column name)."""
        if not self.conn:
            raise RuntimeError("UnitOfWork has no active connection.")
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()

    def _discard_broken_connection(self, conn: Any) -> None:
        discard = getattr(self.db, "discard_connection", None)
        if callable(discard):
            # PostgresDB: keeps the per-thread held-connection count honest.
            discard(conn)
            return
        used = getattr(self.db, "_used", None)
        if used is not None:
            used.discard(id(conn))
        pool = getattr(self.db, "pool", None)
        if pool is not None:
            try:
                pool.putconn(conn, close=True)
                return
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def _is_stale_connection_error(exc: BaseException) -> bool:
    """A dead socket, not a statement the server rejected on a live one.

    ``TransactionRollbackError`` (serialization/deadlock) and
    ``QueryCanceledError`` (statement timeout) subclass ``OperationalError`` but
    are answers from a working server; a fresh connection would not change them.
    """
    if isinstance(
        exc,
        (psycopg2.extensions.TransactionRollbackError, psycopg2.extensions.QueryCanceledError),
    ):
        return False
    return isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))


def make_uow_factory(
    db: IPostgresDB,
    *,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    statement_timeout: str = "10s",
    isolation_level: str = "SERIALIZABLE",  # Charges ledger and budgets: prioritize correctness over performance
    read_only: bool = False,
    outbox_repo_factory: Callable[[Any], IOutboxRepository] | None = None,
    event_mapper: DomainEventToIntegrationEventMapper | None = None,
    logger: logging.Logger | None = None,
) -> Callable[[], PostgresUnitOfWork]:
    """Build a zero-arg factory for PostgresUnitOfWork with shared configuration."""
    mapper = event_mapper or DomainEventToIntegrationEventMapper()
    repo_factory = outbox_repo_factory or (lambda conn: OutboxRepositoryPostgres(conn))

    def _factory() -> PostgresUnitOfWork:
        return PostgresUnitOfWork(
            db,
            organization_id=organization_id,
            user_id=user_id,
            outbox_repo_factory=repo_factory,
            event_mapper=mapper,
            statement_timeout=statement_timeout,
            isolation_level=isolation_level,
            read_only=read_only,
            logger=logger,
        )

    return _factory


def make_idempotency_uow_factory(
    db: IPostgresDB,
    *,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    statement_timeout: str = "5s",
    logger: logging.Logger | None = None,
) -> Callable[[], PostgresUnitOfWork]:
    """Build a UoW factory for standalone idempotency-register transactions.

    The HTTP idempotency helpers (``begin/store/reset_http_idempotent_request``)
    open a transaction whose only statement is one conditional write on the
    ``idempotency`` table. Correctness there comes from the partial unique
    indexes on ``(organization_id, key)`` / ``(key)`` plus single-row
    conditional writes (``ON CONFLICT DO NOTHING``, ``WHERE status =
    'in_progress'``), none of which depend on snapshot isolation.

    Under ``SERIALIZABLE`` those same statements take SIREAD locks on btree
    leaf pages, and unrelated keys sharing a prefix share leaf pages, so concurrent writers
    abort each other as SSI pivots and exhaust their retries. READ COMMITTED
    removes predicate locking from this path without weakening any guarantee.

    A caller may commit other writes in the same transaction only when they
    are blind inserts of fresh-id rows that carry no money or budget (e.g. a
    media asset record stored together with its idempotency result). A write
    that reads state to establish a ledger or budget invariant must stay on
    the SERIALIZABLE default factory.
    """
    return make_uow_factory(
        db,
        organization_id=organization_id,
        user_id=user_id,
        statement_timeout=statement_timeout,
        isolation_level="READ COMMITTED",
        read_only=False,
        logger=logger,
    )


def make_read_uow_factory(
    db: IPostgresDB,
    *,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    statement_timeout: str = "10s",
    isolation_level: Literal["READ COMMITTED", "REPEATABLE READ"] = "READ COMMITTED",
    outbox_repo_factory: Callable[[Any], IOutboxRepository] | None = None,
    event_mapper: DomainEventToIntegrationEventMapper | None = None,
    logger: logging.Logger | None = None,
) -> Callable[[], PostgresUnitOfWork]:
    """Build a UoW factory for pure query services.

    Query endpoints do not need serializable snapshots because they do not
    establish ledger facts or enforce write invariants. Running them at
    READ COMMITTED avoids PostgreSQL SSI pivot aborts when workers concurrently
    update hot tables such as the charges ledger and campaign budgets.

    A query whose reads must agree with each other (one evaluation reading
    configuration, aggregate and evidence) may ask for REPEATABLE READ: a
    read-only snapshot takes no SIREAD locks, so it cannot abort writers either.
    SERIALIZABLE is deliberately not offered.
    """
    return make_uow_factory(
        db,
        organization_id=organization_id,
        user_id=user_id,
        statement_timeout=statement_timeout,
        isolation_level=isolation_level,
        read_only=True,
        outbox_repo_factory=outbox_repo_factory,
        event_mapper=event_mapper,
        logger=logger,
    )
