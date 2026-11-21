# Closed transaction caches release snapshots while preserving explicit committed hook inputs.
"""Completed transaction snapshots are released without requiring cyclic GC."""

from __future__ import annotations

import gc
import weakref
from contextlib import contextmanager

import pytest

from app.modules.common.application.uow import AbstractUnitOfWork


class Snapshot:
    def __init__(self):
        self.data = b"x" * (256 * 1024)
        self.events = ["committed-event"]

    def pull_events(self):
        events, self.events = tuple(self.events), []
        return events


class Repo:
    def __init__(self, uow, snapshot):
        self.uow = uow
        self.seen = [snapshot]


class Uow(AbstractUnitOfWork):
    def __init__(self, *, fail_commit=False, fail_rollback=False):
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.published = ()
        self.rollbacks = 0

    def _commit(self, events):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.published = events

    def rollback(self):
        self.rollbacks += 1
        if self.fail_rollback:
            raise RuntimeError("rollback failed")


@contextmanager
def delayed_gc():
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        gc.collect()
        if was_enabled:
            gc.enable()


def register_snapshot(uow):
    snapshot = Snapshot()
    ref = weakref.ref(snapshot)
    uow.register_repository("snapshot", Repo(uow, snapshot))
    return ref


@pytest.mark.parametrize(
    "outcome", ["committed", "uncommitted", "raised", "commit_failed", "rollback_failed"]
)
def test_closed_uow_releases_snapshots_even_when_gc_is_delayed(outcome):
    with delayed_gc():
        uow = Uow(
            fail_commit=outcome == "commit_failed", fail_rollback=outcome == "rollback_failed"
        )
        try:
            with uow:
                ref = register_snapshot(uow)
                if outcome == "raised":
                    raise RuntimeError("handler failed")
                if outcome in {"committed", "commit_failed"}:
                    uow.commit()
        except RuntimeError:
            assert outcome in {"raised", "commit_failed", "rollback_failed"}
        assert ref() is None
        assert list(uow._iter_repositories()) == []
        if outcome == "committed":
            assert uow.published == ("committed-event",)
            assert uow.rollbacks == 0


def test_after_exit_post_commit_hooks_still_run_once_and_keep_explicit_dependencies():
    called = []
    with Uow() as uow:
        snapshot = Snapshot()
        repo = Repo(uow, snapshot)
        uow.register_repository("snapshot", repo)
        # Post-commit owners may run hooks after returning the DB connection.
        uow.register_post_commit_hook(lambda: called.append(repo.seen[0].data[:1]))
        assert uow.commit() == ("committed-event",)
    assert called == []
    uow.run_post_commit_hooks()
    uow.run_post_commit_hooks()
    assert called == [b"x"]


def test_rollback_discards_hooks_without_running_them():
    called = []
    with Uow() as uow:
        register_snapshot(uow)
        uow.register_post_commit_hook(lambda: called.append(True))
    uow.run_post_commit_hooks()
    assert called == []
