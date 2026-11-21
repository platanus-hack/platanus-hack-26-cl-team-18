"""``PostgresUnitOfWork.__enter__`` survives exactly one dead pooled connection.

Prod 2026-10-10 12:06: several workers died on
``OperationalError: server closed the connection unexpectedly`` raised by the
very first statement of a UoW (``SET TRANSACTION ...; SELECT set_config(...)``).
The pool's liveness ping is skipped for connections idle < 30s, so a polling
worker is always handed its last connection unchecked. Nothing has executed in
the transaction at that point, so ``__enter__`` discards the dead connection and
begins once more on a fresh one. A second failure is an outage and propagates.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import psycopg2
import psycopg2.extensions
import pytest

from app.modules.common.infrastructure.postgres.uow import PostgresUnitOfWork

_GET_CONTEXT = "app.modules.common.infrastructure.postgres.uow.get_context"


def _conn(*, execute_raises: BaseException | None = None) -> MagicMock:
    cursor = MagicMock()
    if execute_raises is not None:
        cursor.execute.side_effect = execute_raises
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def _uow(*conns: MagicMock) -> tuple[PostgresUnitOfWork, MagicMock]:
    db = MagicMock()
    db.get_connection.side_effect = list(conns)
    uow = PostgresUnitOfWork(
        db,
        outbox_repo_factory=MagicMock(return_value=MagicMock()),
        event_mapper=MagicMock(),
    )
    return uow, db


@pytest.mark.parametrize(
    "stale_error",
    [
        psycopg2.OperationalError("server closed the connection unexpectedly"),
        psycopg2.InterfaceError("connection already closed"),
    ],
)
def test_uow_enter_retries_stale_connection(stale_error: BaseException) -> None:
    dead = _conn(execute_raises=stale_error)
    fresh = _conn()
    uow, db = _uow(dead, fresh)

    with patch(_GET_CONTEXT, return_value={}):
        entered = uow.__enter__()

    assert entered is uow
    assert uow.conn is fresh
    db.discard_connection.assert_called_once_with(dead)
    db.release_connection.assert_not_called()
    assert db.get_connection.call_count == 2


def test_uow_enter_second_stale_failure_propagates() -> None:
    first = _conn(execute_raises=psycopg2.OperationalError("server closed (1)"))
    second = _conn(execute_raises=psycopg2.OperationalError("server closed (2)"))
    uow, db = _uow(first, second, _conn())

    with patch(_GET_CONTEXT, return_value={}):
        with pytest.raises(psycopg2.OperationalError, match=r"\(2\)"):
            uow.__enter__()

    assert db.get_connection.call_count == 2  # exactly one retry
    assert [c.args[0] for c in db.discard_connection.call_args_list] == [first, second]
    assert uow.conn is None


@pytest.mark.parametrize(
    "server_answer",
    [
        psycopg2.extensions.TransactionRollbackError("could not serialize access"),
        psycopg2.extensions.QueryCanceledError("canceling statement due to statement timeout"),
    ],
)
def test_uow_enter_does_not_retry_answers_from_a_live_server(
    server_answer: BaseException,
) -> None:
    """Serialization/timeout subclass OperationalError but are not dead sockets."""
    conn = _conn(execute_raises=server_answer)
    uow, db = _uow(conn, _conn())

    with patch(_GET_CONTEXT, return_value={}):
        with pytest.raises(type(server_answer)):
            uow.__enter__()

    assert db.get_connection.call_count == 1
    assert uow.conn is None


def test_uow_enter_does_not_retry_a_rejected_statement_on_a_live_connection() -> None:
    conn = _conn(execute_raises=psycopg2.ProgrammingError("permission denied"))
    uow, db = _uow(conn, _conn())

    with patch(_GET_CONTEXT, return_value={}):
        with pytest.raises(psycopg2.ProgrammingError):
            uow.__enter__()

    assert db.get_connection.call_count == 1
    db.release_connection.assert_called_once_with(conn)
    db.discard_connection.assert_not_called()


def test_uow_enter_rejects_bad_isolation_level_before_borrowing_a_connection() -> None:
    db = MagicMock()
    uow = PostgresUnitOfWork(
        db,
        isolation_level="CHAOS",
        outbox_repo_factory=MagicMock(return_value=MagicMock()),
        event_mapper=MagicMock(),
    )
    with pytest.raises(ValueError, match="isolation_level"):
        uow.__enter__()
    db.get_connection.assert_not_called()
