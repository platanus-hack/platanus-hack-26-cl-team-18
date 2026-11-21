"""A rolled-back savepoint undoes the events of the block, not just its rows.

``AbstractUnitOfWork.savepoint`` is the per-item bulkhead batch loops rely on
(settling a batch of playback reports is one): a failing item rolls back to the savepoint
and the batch carries on. If the aggregates that item created stayed tracked
with their pending events, ``commit()`` would publish them to the outbox -- a
``ChargeRecorded`` for a charge row that was rolled back.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app.modules.common.application.uow import AbstractUnitOfWork


class _Aggregate:
    def __init__(self, *events: str) -> None:
        self._events = list(events)

    def pull_events(self) -> tuple[Any, ...]:
        events, self._events = tuple(self._events), []
        return events


class _Repo:
    def __init__(self) -> None:
        self.seen: list[_Aggregate] = []


class _Cursor:
    def execute(self, sql: str) -> None:
        self.sql = sql


class _Uow(AbstractUnitOfWork):
    __slots__ = ("published", "statements")

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.published: tuple[Any, ...] = ()

    @contextmanager
    def cursor(self) -> Any:
        cur = _Cursor()
        yield cur
        self.statements.append(cur.sql)

    def _commit(self, events: tuple[Any, ...]) -> None:
        self.published = events

    def rollback(self) -> None:
        pass


def test_a_rolled_back_savepoint_discards_the_events_it_produced() -> None:
    with _Uow() as uow:
        repo = uow._get_repo("charges", _Repo)
        repo.seen.append(_Aggregate("CampaignActivated"))

        with pytest.raises(RuntimeError), uow.savepoint("per_item"):
            repo.seen.append(_Aggregate("ChargeRecorded"))
            raise RuntimeError("item needs review")

        assert uow.commit() == ("CampaignActivated",)
    assert uow.statements == ["SAVEPOINT per_item", "ROLLBACK TO SAVEPOINT per_item"]


def test_a_released_savepoint_keeps_the_events_it_produced() -> None:
    with _Uow() as uow:
        repo = uow._get_repo("charges", _Repo)

        with uow.savepoint("per_item"):
            repo.seen.append(_Aggregate("ChargeRecorded"))

        assert uow.commit() == ("ChargeRecorded",)
    assert uow.statements == ["SAVEPOINT per_item", "RELEASE SAVEPOINT per_item"]
