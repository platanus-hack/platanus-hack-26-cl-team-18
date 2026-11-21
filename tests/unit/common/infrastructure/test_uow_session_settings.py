"""Unit tests for PostgresUnitOfWork's per-transaction session settings.

Every transaction sends its settings (timeout, search path, organization and
actor context) in one batched, transaction-local ``set_config`` statement, so
nothing leaks onto the pooled session.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.modules.common.infrastructure.postgres.uow import (
    PostgresUnitOfWork,
    make_read_uow_factory,
    make_uow_factory,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _build_uow(
    *, organization_id=None, user_id=None, read_only: bool = False
) -> PostgresUnitOfWork:
    """Build a UoW wired with a mock DB; does NOT enter it."""
    db = MagicMock()
    outbox_repo_factory = MagicMock(return_value=MagicMock())
    event_mapper = MagicMock()
    return PostgresUnitOfWork(
        db,
        organization_id=organization_id,
        user_id=user_id,
        read_only=read_only,
        outbox_repo_factory=outbox_repo_factory,
        event_mapper=event_mapper,
    )


def _collect_execute_calls(uow: PostgresUnitOfWork) -> list[tuple[str, Any]]:
    """Enter the UoW and return all cur.execute call args."""
    cursor_mock = MagicMock()
    uow.db.get_connection.return_value = MagicMock(
        autocommit=False,
        cursor=MagicMock(
            return_value=MagicMock(
                __enter__=MagicMock(return_value=cursor_mock),
                __exit__=MagicMock(return_value=False),
            )
        ),
    )
    uow._outbox_repo_factory.return_value = MagicMock()

    with patch("app.modules.common.infrastructure.postgres.uow.get_context", return_value={}):
        try:
            uow.__enter__()
        except Exception:
            logger.debug(
                "uow.__enter__() raised on mocked connection; ignoring for call inspection",
                exc_info=True,
            )

    return [
        (c.args[0], c.args[1] if len(c.args) > 1 else None)
        for c in cursor_mock.execute.call_args_list
    ]


def _collect_local_settings(uow: PostgresUnitOfWork) -> tuple[str, list[tuple[str, str]]]:
    """Enter the UoW and decode its single batched setup statement.

    Returns the SQL text plus the ordered ``(setting, value)`` pairs passed to
    ``set_config(..., true)``. The UoW sends every per-tx setting in one round
    trip, so exactly one execute call is expected.
    """
    calls = _collect_execute_calls(uow)
    assert len(calls) == 1, f"Expected one batched setup statement, got: {calls}"
    sql, params = calls[0]
    assert sql.count("set_config(%s, %s, true)") * 2 == len(params)
    pairs = [(params[i], params[i + 1]) for i in range(0, len(params), 2)]
    return sql, pairs


def test_uow_sets_user_and_actor_from_user_id() -> None:
    user_id = uuid4()
    _, settings = _collect_local_settings(_build_uow(user_id=user_id))

    values = dict(settings)
    assert values["app.user_id"] == str(user_id)
    assert values["app.actor_id"] == str(user_id)


def test_uow_omits_context_settings_without_organization_or_user() -> None:
    _, settings = _collect_local_settings(_build_uow())

    names = [name for name, _ in settings]
    assert "app.current_organization" not in names
    assert "app.user_id" not in names


def test_uow_sets_rls_organization_and_transaction_local_timeout() -> None:
    organization_id = uuid4()
    sql, settings = _collect_local_settings(_build_uow(organization_id=organization_id))

    values = dict(settings)
    assert values["app.current_organization"] == str(organization_id)
    assert values["statement_timeout"] == "10s"
    assert values["search_path"] == "public, pg_catalog"
    # is_local => true on every call: nothing may leak onto the pooled session.
    assert "set_config(%s, %s, false)" not in sql


def test_uow_sets_transaction_read_only_when_configured() -> None:
    sql, _ = _collect_local_settings(_build_uow(read_only=True))

    # Transaction characteristics must lead the batch, before any ordinary query.
    assert sql.startswith("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ ONLY; SELECT ")


# ------------------------------------------------------------------ #
# Tests — make_uow_factory propagation                               #
# ------------------------------------------------------------------ #


def test_make_uow_factory_propagates_organization_and_user() -> None:
    db = MagicMock()
    organization_id, user_id = uuid4(), uuid4()
    factory = make_uow_factory(
        db,
        organization_id=organization_id,
        user_id=user_id,
        outbox_repo_factory=MagicMock(return_value=MagicMock()),
        event_mapper=MagicMock(),
    )
    uow = factory()
    assert uow.organization_id == organization_id
    assert uow.user_id == user_id
    assert uow.isolation_level == "SERIALIZABLE"


def test_make_read_uow_factory_uses_read_committed_isolation() -> None:
    db = MagicMock()
    factory = make_read_uow_factory(
        db,
        outbox_repo_factory=MagicMock(return_value=MagicMock()),
        event_mapper=MagicMock(),
    )
    uow = factory()
    assert uow.isolation_level == "READ COMMITTED"
    assert uow.read_only is True
