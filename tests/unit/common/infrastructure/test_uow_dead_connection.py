"""Unit tests for PostgresUnitOfWork — dead-connection safety.

Regression for the production Sentry error:
  OperationalError: server closed the connection unexpectedly   (line 93 __enter__)
  InterfaceError:   connection already closed                   (line 134 __enter__)

Root cause: when the DB server dropped the connection, __enter__'s except block
caught OperationalError and called rollback() on the already-dead connection.
That raised InterfaceError (not caught), which:
  1. masked the real error reported to Sentry
  2. skipped `finally`, leaking the broken connection back to the pool

Fix: add InterfaceError to the outer except; swallow errors from rollback() in cleanup.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from app.modules.common.infrastructure.postgres.uow import PostgresUnitOfWork

# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _build_uow() -> PostgresUnitOfWork:
    db = MagicMock()
    return PostgresUnitOfWork(
        db,
        outbox_repo_factory=MagicMock(return_value=MagicMock()),
        event_mapper=MagicMock(),
    )


def _make_conn(*, cursor_raises=None, rollback_raises=None) -> MagicMock:
    """Return a mock connection whose cursor().execute() and rollback() can raise."""
    cursor_mock = MagicMock()
    if cursor_raises is not None:
        cursor_mock.execute.side_effect = cursor_raises

    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    if rollback_raises is not None:
        conn.rollback.side_effect = rollback_raises

    return conn, cursor_mock


# ------------------------------------------------------------------ #
# Core regression: OperationalError → rollback raises InterfaceError  #
# ------------------------------------------------------------------ #


def test_operational_error_followed_by_interface_error_on_rollback_reraises_original() -> None:
    """The production failure: server drops the connection (OperationalError on execute),
    then rollback() raises InterfaceError because the connection is already gone.
    __enter__ must re-raise the original OperationalError, not InterfaceError."""
    uow = _build_uow()
    conn, _ = _make_conn(
        cursor_raises=psycopg2.OperationalError("server closed the connection unexpectedly"),
        rollback_raises=psycopg2.InterfaceError("connection already closed"),
    )
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.OperationalError, match="server closed the connection"):
            uow.__enter__()


def test_connection_discarded_when_operational_error_and_rollback_raises_interface_error() -> None:
    """No connection leak, and no reuse: a dead connection is discarded (closed),
    never released back into the pool, even when rollback() fails.

    ``get_connection`` keeps handing back the same dead mock, so the one
    retry-on-a-fresh-connection also fails and the error propagates.
    """
    uow = _build_uow()
    conn, _ = _make_conn(
        cursor_raises=psycopg2.OperationalError("server closed the connection unexpectedly"),
        rollback_raises=psycopg2.InterfaceError("connection already closed"),
    )
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.OperationalError):
            uow.__enter__()

    assert uow.db.discard_connection.call_count == 2
    uow.db.discard_connection.assert_called_with(conn)
    uow.db.release_connection.assert_not_called()


def test_conn_cleared_after_dead_connection_cleanup() -> None:
    """self.conn must be None after __enter__ fails — prevents accidental reuse."""
    uow = _build_uow()
    conn, _ = _make_conn(
        cursor_raises=psycopg2.OperationalError("server closed the connection unexpectedly"),
        rollback_raises=psycopg2.InterfaceError("connection already closed"),
    )
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.OperationalError):
            uow.__enter__()

    assert uow.conn is None


# ------------------------------------------------------------------ #
# Variant: OperationalError → rollback succeeds                        #
# ------------------------------------------------------------------ #


def test_operational_error_with_clean_rollback_reraises_original() -> None:
    """When rollback() succeeds after an OperationalError, the OperationalError is still raised."""
    uow = _build_uow()
    conn, _ = _make_conn(
        cursor_raises=psycopg2.OperationalError("server closed the connection unexpectedly"),
    )
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.OperationalError):
            uow.__enter__()

    # One rollback + discard per attempt (first try and the single retry).
    assert conn.rollback.call_count == 2
    assert uow.db.discard_connection.call_count == 2
    uow.db.release_connection.assert_not_called()
    assert uow.conn is None


# ------------------------------------------------------------------ #
# Variant: InterfaceError raised directly by execute                   #
# ------------------------------------------------------------------ #


def test_interface_error_on_execute_is_caught_and_reraises() -> None:
    """If execute() itself raises InterfaceError (e.g., connection gone before the cursor opens),
    __enter__ must catch it, discard the connection, retry once, and re-raise."""
    uow = _build_uow()
    conn, _ = _make_conn(
        cursor_raises=psycopg2.InterfaceError("connection already closed"),
    )
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.InterfaceError):
            uow.__enter__()

    assert uow.db.discard_connection.call_count == 2
    uow.db.release_connection.assert_not_called()
    assert uow.conn is None


# ------------------------------------------------------------------ #
# Variant: DatabaseError (generic DB failure)                          #
# ------------------------------------------------------------------ #


def test_database_error_on_execute_releases_connection() -> None:
    """Generic DatabaseError (e.g., permission denied on SET) is cleaned up correctly."""
    uow = _build_uow()
    conn, _ = _make_conn(
        cursor_raises=psycopg2.DatabaseError("permission denied"),
    )
    uow.db.get_connection.return_value = conn

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        with pytest.raises(psycopg2.DatabaseError):
            uow.__enter__()

    uow.db.release_connection.assert_called_once_with(conn)
    assert uow.conn is None


# ------------------------------------------------------------------ #
# The same rule, on the way out                                        #
# ------------------------------------------------------------------ #
#
# Everything above covers __enter__.  The rule it establishes -- a rollback on a
# connection the server already dropped must not raise, because it would replace
# the exception that dropped it -- was never applied to __exit__, and __exit__ is
# where a long-lived worker actually meets this: the connection survives entry
# and dies mid-run.
#
# The failure shape, as any long-lived worker sees it:
#
#   psycopg2.OperationalError: server closed the connection unexpectedly
#     <repository>.<write>  (cur.execute)
#   During handling of the above exception, another exception occurred:
#   psycopg2.InterfaceError: connection already closed
#     uow.py __exit__ -> AbstractUnitOfWork.__exit__ -> rollback -> conn.rollback()
#
# The InterfaceError is what the worker recorded, alerted on, and stored as the
# job's error.  It names neither the statement that failed nor why, so the class
# read as a connection-handling bug of its own rather than as the server drop it
# actually was.  And the rollback it reports as failed had already happened: the
# server discarded the transaction when it closed the connection.


@contextmanager
def _live_uow():
    """A UoW that entered cleanly, holding a connection that is about to die.

    ``__enter__`` succeeds -- the connection is healthy at that point -- so the
    entry-path guards above never run and what the body raises meets ``__exit__``
    exactly as it does in a worker.
    """
    uow = _build_uow()
    conn, _ = _make_conn()
    uow.db.get_connection.return_value = conn
    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        yield uow, conn


def test_a_connection_lost_mid_transaction_does_not_replace_the_real_failure() -> None:
    with _live_uow() as (uow, conn):
        conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")

        with pytest.raises(psycopg2.OperationalError, match="server closed the connection"):
            with uow:
                raise psycopg2.OperationalError("server closed the connection unexpectedly")


def test_a_lost_connection_is_discarded_rather_than_returned_to_the_pool() -> None:
    """A dead connection handed back to the pool is handed out again."""
    with _live_uow() as (uow, conn):
        conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")
        uow._discard_broken_connection = MagicMock()

        with pytest.raises(psycopg2.OperationalError):
            with uow:
                raise psycopg2.OperationalError("server closed the connection unexpectedly")

        uow._discard_broken_connection.assert_called_once_with(conn)
        uow.db.release_connection.assert_not_called()
        assert uow.conn is None


def test_a_connection_known_to_be_dead_is_not_rolled_back_twice() -> None:
    """`rollback()` has already asked; __exit__ must not ask the same question again."""
    with _live_uow() as (uow, conn):
        conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")

        with pytest.raises(psycopg2.OperationalError):
            with uow:
                raise psycopg2.OperationalError("server closed the connection unexpectedly")

        assert conn.rollback.call_count == 1


def test_losing_the_connection_is_reported_under_its_own_event_name(caplog) -> None:
    """The substitution is gone, so the fact itself needs somewhere to be seen."""
    with _live_uow() as (uow, conn):
        conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")

        with caplog.at_level("WARNING"):
            with pytest.raises(psycopg2.OperationalError):
                with uow:
                    raise psycopg2.OperationalError("server closed the connection unexpectedly")

    assert "transaction_rollback_connection_lost" in {record.msg for record in caplog.records}


def test_an_explicit_rollback_on_a_lost_connection_reports_success() -> None:
    """The rollback did happen -- the server performed it by dropping the socket."""
    with _live_uow() as (uow, conn):
        with uow:
            conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")
            uow.rollback()  # must not raise
            uow.commit()


def test_a_healthy_transaction_still_returns_its_connection_to_the_pool() -> None:
    """The lost-connection path must not capture the ordinary rollback."""
    with _live_uow() as (uow, conn):
        uow._discard_broken_connection = MagicMock()

        with pytest.raises(ValueError):
            with uow:
                raise ValueError("an ordinary domain failure")

        uow.db.release_connection.assert_called_once_with(conn)
        uow._discard_broken_connection.assert_not_called()
