"""Unit tests for PostgresUnitOfWork — SERIALIZABLE conflict instrumentation.

The UoW runs every transaction at SERIALIZABLE by default (charges-ledger and budget correctness).
That makes `psycopg2.errors.SerializationFailure` (sqlstate 40001) and
`DeadlockDetected` (40P01) expected, retriable failure modes. They must surface
under a dedicated structured log event — not be swallowed into the generic
`transaction_commit_failed` bucket — so the conflict rate is measurable and
hotspot workflows are identifiable in production logs.

Covered:
  - Commit-phase conflict → `transaction_serialization_conflict` at WARNING
  - Statement-phase conflict (raised inside the `with uow:` block) → same event
  - Distinction from generic DB errors (those keep logging `transaction_commit_failed`)
  - Structured fields are present (sqlstate, conflict_phase, isolation_level,
    has_writes, organization_id, duration_ms)
  - Exceptions still propagate to the caller (no signal loss)
  - DeadlockDetected is routed to the same WARNING path
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg2
import psycopg2.errors
import pytest

from app.modules.common.infrastructure.postgres.uow import PostgresUnitOfWork

# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _build_uow(*, organization_id=None, user_id=None) -> PostgresUnitOfWork:
    db = MagicMock()
    uow = PostgresUnitOfWork(
        db,
        organization_id=organization_id,
        user_id=user_id,
        outbox_repo_factory=MagicMock(return_value=MagicMock()),
        event_mapper=MagicMock(),
    )
    # Initialize AbstractUnitOfWork state that __enter__ would normally set up.
    # Tests that bypass `with uow:` to exercise _commit() directly still need
    # _has_writes / _committed to exist.
    uow._committed = False
    uow._has_writes = False
    uow._repo_cache = {}
    uow._post_commit_hooks = []
    return uow


def _healthy_conn() -> MagicMock:
    """Mock connection whose cursor().execute() is a no-op."""
    cursor_mock = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def _make_pg_error(cls: type, *, pgcode: str | None) -> Exception:
    """Build a psycopg2 error whose `pgcode` returns the desired sqlstate.

    `pgcode` is a read-only property on the real psycopg2 error classes, so we
    subclass and shadow it. Instances must still pass isinstance() checks for
    the underlying class, which subclassing preserves.
    """
    subclass = type(
        f"Fake{cls.__name__}",
        (cls,),
        {"pgcode": pgcode},
    )
    return subclass("simulated")


def _find_record(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    for record in caplog.records:
        if record.getMessage() == event:
            return record
    pytest.fail(
        f"no log record with message {event!r} found; got: {[r.getMessage() for r in caplog.records]}"
    )


# ------------------------------------------------------------------ #
# Commit-phase conflicts                                              #
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("worker_name", ["warm_worker", None])
def test_commit_conflict_is_logged_once_with_runtime_attribution(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, worker_name: str | None
) -> None:
    # commit and context exit observe the same failure once.
    monkeypatch.setenv("RENDER_SERVICE_NAME", "app-prod")
    if worker_name:
        monkeypatch.setenv("WORKER_NAME", worker_name)
    else:
        monkeypatch.delenv("WORKER_NAME", raising=False)
    uow = _build_uow()
    conn = _healthy_conn()
    conn.commit.side_effect = _make_pg_error(psycopg2.errors.SerializationFailure, pgcode="40001")
    uow.db.get_connection.return_value = conn

    with caplog.at_level(logging.WARNING, logger=uow.logger.name):
        with pytest.raises(psycopg2.errors.SerializationFailure):
            with uow:
                uow.commit()

    records = [r for r in caplog.records if r.getMessage() == "transaction_serialization_conflict"]
    assert len(records) == 1
    assert records[0].conflict_phase == "commit"
    assert records[0].pool_role == (worker_name or "app-prod")
    assert uow.conn is None
    uow.db.release_connection.assert_called_once_with(conn)


def test_commit_serialization_failure_logs_dedicated_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """conn.commit() raising 40001 must emit `transaction_serialization_conflict`
    at WARNING — not `transaction_commit_failed` at ERROR."""
    org_id = uuid4()
    uow = _build_uow(organization_id=org_id)
    conn = _healthy_conn()
    conn.commit.side_effect = _make_pg_error(psycopg2.errors.SerializationFailure, pgcode="40001")
    uow.conn = conn
    uow._outbox_repo = MagicMock()
    uow.started_at = time.monotonic()

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with pytest.raises(psycopg2.errors.SerializationFailure):
        uow._commit(events=())

    rec = _find_record(caplog, "transaction_serialization_conflict")
    assert rec.levelno == logging.WARNING
    assert rec.sqlstate == "40001"
    assert rec.conflict_phase == "commit"
    assert rec.isolation_level == "SERIALIZABLE"
    assert rec.organization_id == org_id
    assert isinstance(rec.duration_ms, int)
    # Must NOT also emit the generic failure log — that would double-count.
    for record in caplog.records:
        assert record.getMessage() != "transaction_commit_failed"


def test_commit_deadlock_detected_routes_to_same_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DeadlockDetected (40P01) is retriable by the same contract as 40001 and
    must take the same logging path."""
    uow = _build_uow()
    conn = _healthy_conn()
    conn.commit.side_effect = _make_pg_error(psycopg2.errors.DeadlockDetected, pgcode="40P01")
    uow.conn = conn
    uow._outbox_repo = MagicMock()
    uow.started_at = time.monotonic()

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with pytest.raises(psycopg2.errors.DeadlockDetected):
        uow._commit(events=())

    rec = _find_record(caplog, "transaction_serialization_conflict")
    assert rec.levelno == logging.WARNING
    assert rec.sqlstate == "40P01"
    assert rec.conflict_phase == "commit"


def test_commit_generic_database_error_still_logs_commit_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-retriable DB errors (e.g. constraint violation) must keep their
    previous behavior — `transaction_commit_failed` at ERROR via logger.exception —
    so the new instrumentation doesn't mask unrelated failures."""
    uow = _build_uow()
    conn = _healthy_conn()
    conn.commit.side_effect = psycopg2.DatabaseError("constraint violation")
    uow.conn = conn
    uow._outbox_repo = MagicMock()
    uow.started_at = time.monotonic()

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with pytest.raises(psycopg2.DatabaseError):
        uow._commit(events=())

    _find_record(caplog, "transaction_commit_failed")
    for record in caplog.records:
        assert record.getMessage() != "transaction_serialization_conflict"


def test_commit_success_does_not_emit_conflict_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Negative control: a clean commit must never emit the conflict warning."""
    uow = _build_uow()
    conn = _healthy_conn()
    uow.conn = conn
    uow._outbox_repo = MagicMock()
    uow.started_at = time.monotonic()

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    uow._commit(events=())

    for record in caplog.records:
        assert record.getMessage() != "transaction_serialization_conflict"
    record = _find_record(caplog, "transaction_committed_read_only")
    assert record.commit_duration_ms >= 0
    assert record.has_writes is False
    assert record.read_only is False  # no writes is different from a READ ONLY transaction


# UoW lifetime is not the duration of the actual COMMIT call.
def test_commit_duration_measures_the_database_call_instead_of_uow_lifetime(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    uow = _build_uow()
    uow.conn = _healthy_conn()
    uow._outbox_repo = MagicMock()
    uow.started_at = 10.0
    times = iter((12.0, 20.0, 20.125))
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    monkeypatch.setenv("WORKER_NAME", "outbox_dispatcher")
    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    uow._commit(events=())
    record = _find_record(caplog, "transaction_committed_read_only")
    assert record.duration_ms == 2000
    assert record.commit_duration_ms == 125
    assert record.pool_role == "outbox_dispatcher"


# ------------------------------------------------------------------ #
# Statement-phase conflicts (raised inside `with uow:` block)         #
# ------------------------------------------------------------------ #


def test_mid_transaction_serialization_failure_emits_conflict_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A SELECT/UPDATE inside the UoW raising 40001 propagates up through
    __exit__. The UoW must log `transaction_serialization_conflict` with
    conflict_phase=statement before rolling back."""
    org_id = uuid4()
    uow = _build_uow(organization_id=org_id)
    conn = _healthy_conn()
    uow.db.get_connection.return_value = conn

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.errors.SerializationFailure):
            with uow:
                raise _make_pg_error(psycopg2.errors.SerializationFailure, pgcode="40001")

    rec = _find_record(caplog, "transaction_serialization_conflict")
    assert rec.levelno == logging.WARNING
    assert rec.sqlstate == "40001"
    assert rec.conflict_phase == "statement"
    assert rec.isolation_level == "SERIALIZABLE"
    assert rec.organization_id == org_id


def test_mid_transaction_deadlock_emits_conflict_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    uow = _build_uow()
    conn = _healthy_conn()
    uow.db.get_connection.return_value = conn

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.errors.DeadlockDetected):
            with uow:
                raise _make_pg_error(psycopg2.errors.DeadlockDetected, pgcode="40P01")

    rec = _find_record(caplog, "transaction_serialization_conflict")
    assert rec.conflict_phase == "statement"
    assert rec.sqlstate == "40P01"


def test_mid_transaction_generic_error_does_not_emit_conflict_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-retriable mid-tx error (e.g. ValueError from application code) must
    NOT be misclassified as a serialization conflict."""
    uow = _build_uow()
    conn = _healthy_conn()
    uow.db.get_connection.return_value = conn

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(ValueError):
            with uow:
                raise ValueError("boom")

    for record in caplog.records:
        assert record.getMessage() != "transaction_serialization_conflict"


def test_mid_transaction_conflict_still_rolls_back_and_releases_connection() -> None:
    """The conflict log is instrumentation only — it must not short-circuit
    rollback or the connection-release path."""
    uow = _build_uow()
    conn = _healthy_conn()
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.errors.SerializationFailure):
            with uow:
                raise _make_pg_error(psycopg2.errors.SerializationFailure, pgcode="40001")

    # rollback called (once in our __exit__ conflict path via super(), once in cleanup).
    assert conn.rollback.called
    uow.db.release_connection.assert_called_once_with(conn)
    assert uow.conn is None


def test_conflict_log_survives_missing_pgcode(caplog: pytest.LogCaptureFixture) -> None:
    """Defensive: if a future driver variant produces a SerializationFailure
    without a populated pgcode, instrumentation must still fire with sqlstate=None
    rather than blowing up during logging."""
    uow = _build_uow()
    conn = _healthy_conn()
    conn.commit.side_effect = _make_pg_error(psycopg2.errors.SerializationFailure, pgcode=None)
    uow.conn = conn
    uow._outbox_repo = MagicMock()
    uow.started_at = time.monotonic()

    caplog.set_level(logging.DEBUG, logger=uow.logger.name)
    with pytest.raises(psycopg2.errors.SerializationFailure):
        uow._commit(events=())

    rec = _find_record(caplog, "transaction_serialization_conflict")
    assert rec.sqlstate is None
