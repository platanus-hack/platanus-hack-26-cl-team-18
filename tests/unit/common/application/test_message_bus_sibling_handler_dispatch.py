"""REGRESSION: every registered event handler must run, even anonymous ones.

`MessageBus` de-dupes handlers by `module.qualname` -- once when resolving the
registry, and again through the within-call `processed_evt_handlers` guard. Bare
lambdas defined side by side in one builder all carry the qualname
`<builder>.<locals>.<lambda>`, so a two-handler registration silently collapsed
to the last one: the first subscriber never ran, with no exception, no log line
and no write. One projection silently fell behind while its sibling kept
updating, so two read models disagreed about the same event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

from app.config.logging_context import get_context
from app.modules.common.application.message_bus import MessageBus
from app.modules.common.domain.primitives.events import (
    Actor,
    ActorType,
    DomainEvent,
    RequestContext,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _SiblingEvent(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "test.sibling.event"
    AGGREGATE_TYPE: ClassVar[str] = "TestAggregate"
    SCHEMA_VERSION: ClassVar[int] = 1


def _make_event() -> _SiblingEvent:
    return _SiblingEvent(
        **_SiblingEvent.create_base(
            event_id=uuid4(),
            organization_id=uuid4(),
            aggregate_id=uuid4(),
            aggregate_version=1,
            actor=Actor.create(type=ActorType.SYSTEM, id=uuid4()),
            occurred_at=datetime.now(UTC),
            ctx=RequestContext.create(),
        )
    )


class _FakeUow:
    def __enter__(self) -> _FakeUow:
        self._has_writes = False
        self._committed = False
        self._post: list = []
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


def _build_bus_with_sibling_lambdas(calls: list[str]) -> MessageBus:
    """Mirror the real wiring: two bare lambdas born in ONE enclosing scope."""
    return MessageBus(
        uow_factory=_FakeUow,
        command_handlers={},
        event_handlers_by_type={
            _SiblingEvent.EVENT_TYPE: [
                lambda evt, uow: calls.append("projection"),
                lambda evt, uow: calls.append("collection_advance"),
            ]
        },
    )


def test_both_sibling_lambda_handlers_are_invoked() -> None:
    calls: list[str] = []
    bus = _build_bus_with_sibling_lambdas(calls)

    bus.handle(_make_event())

    assert calls == ["projection", "collection_advance"]


def test_the_same_handler_registered_twice_still_runs_once() -> None:
    """The de-dupe intent it was protecting must survive the fix."""
    calls: list[str] = []

    def handler(evt, uow) -> None:
        calls.append("once")

    bus = MessageBus(
        uow_factory=_FakeUow,
        command_handlers={},
        event_handlers_by_type={_SiblingEvent.EVENT_TYPE: [handler, handler]},
    )

    bus.handle(_make_event())

    assert calls == ["once"]


def test_event_uow_factory_sees_the_event_tenant_context() -> None:
    event = _make_event()
    factory_contexts: list[object] = []

    def uow_factory() -> _FakeUow:
        factory_contexts.append(get_context().get("organization_id"))
        return _FakeUow()

    bus = MessageBus(
        uow_factory=uow_factory,
        command_handlers={},
        event_handlers_by_type={_SiblingEvent.EVENT_TYPE: [lambda evt, uow: None]},
    )

    bus.handle(event)

    assert factory_contexts == [str(event.organization_id)]
