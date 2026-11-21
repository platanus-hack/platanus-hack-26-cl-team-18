"""Every pooled socket carries TCP keepalives.

The pool's idle ping is skipped for connections released < 30s ago, so polling
workers never ping. Keepalives are what make a silently-vanished peer surface as
an ``OperationalError`` in bounded time instead of a read blocking for the
kernel default (~2h).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.common.infrastructure.postgres import postgres


class _RecordingPool:
    calls: list[dict[str, Any]] = []

    def __init__(self, minconn: int, maxconn: int, **kwargs: Any) -> None:
        type(self).calls.append(kwargs)


@pytest.fixture(autouse=True)
def _record_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    _RecordingPool.calls = []
    monkeypatch.setattr(postgres, "ConnectionPool", _RecordingPool)
    monkeypatch.setattr(postgres, "register_uuid", lambda: None)


def _build(**overrides: Any) -> None:
    postgres.PostgresDB(dbname="app", user="app", password="x", host="db.internal", **overrides)


def test_postgres_pool_keepalives_are_enabled_by_default() -> None:
    _build()

    (kwargs,) = _RecordingPool.calls
    assert kwargs["keepalives"] == 1
    assert kwargs["keepalives_idle"] == 30
    assert kwargs["keepalives_interval"] == 10
    assert kwargs["keepalives_count"] == 3
    # Dead-peer detection (idle + interval * count) stays well under the prod
    # idle_in_transaction_session_timeout of 60s plus a poll interval.
    assert (
        kwargs["keepalives_idle"] + kwargs["keepalives_interval"] * kwargs["keepalives_count"] <= 60
    )


def test_postgres_pool_keepalives_are_overridable() -> None:
    _build(keepalives_idle=5, keepalives_interval=2, keepalives_count=4)

    (kwargs,) = _RecordingPool.calls
    assert (
        kwargs["keepalives_idle"],
        kwargs["keepalives_interval"],
        kwargs["keepalives_count"],
    ) == (
        5,
        2,
        4,
    )


def test_postgres_pool_keeps_existing_connect_kwargs() -> None:
    _build(connect_timeout=7)

    (kwargs,) = _RecordingPool.calls
    assert kwargs["connect_timeout"] == 7
    assert kwargs["options"] == "-c jit=off"


def test_postgres_connections_identify_the_worker_for_database_attribution(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_NAME", "delivery_ledger")
    _build()
    (kwargs,) = _RecordingPool.calls
    assert kwargs["application_name"] == "app:delivery_ledger"
