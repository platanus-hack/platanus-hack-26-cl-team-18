"""Event-handler transient-conflict retry semantics for the MessageBus.

A transient Postgres conflict (SerializationFailure 40001 / DeadlockDetected
40P01) on an event handler's commit means *nothing committed* — re-running the
handler against a fresh UoW is always safe and correct. The bus wires
``_run_with_serialization_retry`` around event dispatch precisely to do this.

The bug these tests pin: retrying a transient conflict must NOT depend on the
``best_effort_events`` policy. That policy governs only what happens to a *real*
or retry-exhausted failure (swallow vs. raise). Conflating the two made the
default (``best_effort_events=True``) silently drop retryable conflicts before
the retry net could act — a silent loss of an event reaction (e.g. a charge).
"""

# MessageBus wrapped event dispatch in a serialization-retry net, but the per-
# handler `except Exception` under best_effort_events=True swallowed 40001/40P01 before the
# net could act, so transient conflicts were dropped instead of retried.

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

import psycopg2.errors

from app.modules.common.application.command import Command
from app.modules.common.application.message_bus import MessageBus
from app.modules.common.domain.primitives.events import (
    Actor,
    ActorType,
    DomainEvent,
    RequestContext,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _RetryEvent(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "test.retry.event"
    AGGREGATE_TYPE: ClassVar[str] = "TestAggregate"
    SCHEMA_VERSION: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class _RetryCommand(Command):
    """Minimal idempotent command for MessageBus retry coverage."""

    command_id: UUID


def _make_event() -> _RetryEvent:
    return _RetryEvent(
        **_RetryEvent.create_base(
            event_id=uuid4(),
            organization_id=uuid4(),
            aggregate_id=uuid4(),
            aggregate_version=1,
            actor=Actor.create(type=ActorType.SYSTEM, id=uuid4()),
            occurred_at=datetime.now(UTC),
            ctx=RequestContext.create(),
        )
    )


def _make_command() -> _RetryCommand:
    return _RetryCommand(command_id=uuid4())


def _make_uow_factory(commit_outcomes: list[BaseException | None]):
    """Build a fake-UoW factory whose successive commits follow ``commit_outcomes``.

    A non-None entry is raised by ``commit()``; ``None`` commits cleanly. The
    counters dict records how many handler calls and commit attempts occurred,
    so a test can prove a retry actually happened.
    """

    counters = {"handler_calls": 0, "commit_attempts": 0}
    outcomes = iter(commit_outcomes)

    class _FakeUow:
        def __init__(self) -> None:
            self._committed = False
            self._has_writes = False
            self._post: list = []

        def __enter__(self) -> _FakeUow:
            self._committed = False
            self._has_writes = False
            self._post = []
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        @property
        def committed(self) -> bool:
            return self._committed

        @property
        def has_writes(self) -> bool:
            return self._has_writes

        def mark_write(self) -> None:
            self._has_writes = True

        def commit(self):
            counters["commit_attempts"] += 1
            outcome = next(outcomes)
            if outcome is not None:
                raise outcome
            self._committed = True
            return ()

        def rollback(self) -> None:
            return None

        def run_post_commit_hooks(self) -> None:
            for hook in list(self._post):
                hook()
            self._post.clear()

        def register_post_commit_hook(self, hook) -> None:
            self._post.append(hook)

    return _FakeUow, counters


def _writing_handler(counters):
    def handler(evt, uow):
        counters["handler_calls"] += 1
        uow.mark_write()

    return handler


def test_default_config_retries_transient_serialization_conflict() -> None:
    """REGRESSION: default best_effort_events=True must still retry 40001."""
    factory, counters = _make_uow_factory(
        [psycopg2.errors.SerializationFailure("simulated 40001"), None]
    )
    bus = MessageBus(
        uow_factory=factory,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [_writing_handler(counters)]},
        # default best_effort_events=True
        serialization_retry_min_seconds=0.0,
        serialization_retry_max_seconds=0.0,
    )

    bus.handle(_make_event())

    assert counters["commit_attempts"] == 2, (
        "transient SerializationFailure on the event handler was not retried under "
        "best_effort_events=True: best-effort swallowed the retryable error before "
        "the retry net could re-run the handler (silent loss of an event reaction)"
    )
    assert counters["handler_calls"] == 2


def test_deadlock_is_also_retried_by_default() -> None:
    factory, counters = _make_uow_factory(
        [psycopg2.errors.DeadlockDetected("simulated 40P01"), None]
    )
    bus = MessageBus(
        uow_factory=factory,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [_writing_handler(counters)]},
        serialization_retry_min_seconds=0.0,
        serialization_retry_max_seconds=0.0,
    )

    bus.handle(_make_event())

    assert counters["commit_attempts"] == 2
    assert counters["handler_calls"] == 2


def test_default_command_retry_window_survives_a_price_save_burst(
    monkeypatch,
) -> None:
    """A 40001 can outlive three short retries while sibling price saves commit."""
    monkeypatch.setattr("app.modules.common.application.message_bus.time.sleep", lambda _s: None)
    factory, counters = _make_uow_factory(
        [psycopg2.errors.SerializationFailure("simulated 40001")] * 4 + [None]
    )
    bus = MessageBus(
        uow_factory=factory,
        command_handlers={_RetryCommand: _writing_handler(counters)},
        event_handlers_by_type={},
    )

    assert bus.handle(_make_command()) is None
    assert counters["handler_calls"] == 5
    assert counters["commit_attempts"] == 5


def test_best_effort_swallows_exhausted_transient_conflict() -> None:
    """best_effort_events=True must never fail the cascade — even when retries are
    exhausted. The retries still happen; the final unresolved conflict is dropped."""
    attempts = 3
    factory, counters = _make_uow_factory(
        [psycopg2.errors.SerializationFailure("persistent 40001")] * attempts
    )
    bus = MessageBus(
        uow_factory=factory,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [_writing_handler(counters)]},
        best_effort_events=True,
        serialization_retry_attempts=attempts,
        serialization_retry_min_seconds=0.0,
        serialization_retry_max_seconds=0.0,
    )

    # Does not raise: best-effort swallows the exhausted conflict.
    assert bus.handle(_make_event()) is None
    assert counters["commit_attempts"] == attempts


def test_strict_config_raises_after_exhausting_retries() -> None:
    """best_effort_events=False must surface a retry-exhausted conflict after
    attempting the full retry budget."""
    attempts = 3
    factory, counters = _make_uow_factory(
        [psycopg2.errors.SerializationFailure("persistent 40001")] * attempts
    )
    bus = MessageBus(
        uow_factory=factory,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [_writing_handler(counters)]},
        best_effort_events=False,
        serialization_retry_attempts=attempts,
        serialization_retry_min_seconds=0.0,
        serialization_retry_max_seconds=0.0,
    )

    try:
        bus.handle(_make_event())
    except psycopg2.errors.SerializationFailure:
        pass
    else:  # pragma: no cover
        raise AssertionError("strict bus must re-raise an exhausted serialization conflict")

    assert counters["commit_attempts"] == attempts


def test_non_retryable_error_respects_best_effort_policy() -> None:
    """A non-retryable handler error must NOT be retried, and best_effort decides
    whether it is swallowed (True) or raised (False)."""

    def boom(evt, uow):
        uow.mark_write()
        raise ValueError("non-retryable")

    factory_a, counters_a = _make_uow_factory([None])
    swallow_bus = MessageBus(
        uow_factory=factory_a,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [boom]},
        best_effort_events=True,
        serialization_retry_min_seconds=0.0,
        serialization_retry_max_seconds=0.0,
    )
    assert swallow_bus.handle(_make_event()) is None
    # ValueError is raised by the handler before commit; commit never runs and the
    # error is not retried.
    assert counters_a["commit_attempts"] == 0

    factory_b, _ = _make_uow_factory([None])
    strict_bus = MessageBus(
        uow_factory=factory_b,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [boom]},
        best_effort_events=False,
        serialization_retry_min_seconds=0.0,
        serialization_retry_max_seconds=0.0,
    )
    try:
        strict_bus.handle(_make_event())
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("strict bus must re-raise a non-retryable handler error")


def test_callable_object_event_handler_is_dispatched_and_deduplicated() -> None:
    """REGRESSION: production subscribers are callable dataclass instances, not functions."""
    factory, counters = _make_uow_factory([None])

    class Handler:
        def __call__(self, _event, uow) -> None:
            counters["handler_calls"] += 1
            uow.mark_write()

    handler = Handler()
    bus = MessageBus(
        uow_factory=factory,
        command_handlers={},
        event_handlers_by_type={_RetryEvent.EVENT_TYPE: [handler, handler]},
    )

    bus.handle(_make_event())

    assert counters["handler_calls"] == 1
    assert counters["commit_attempts"] == 1
