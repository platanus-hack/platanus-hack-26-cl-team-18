"""`AggregateRoot._evolve` vs bare `dataclasses.replace`.

`replace` resets every field declared `init=False`, which on an aggregate means
`version`, `_events` and `_persisted_version`. Two consequences, both silent:

- the pending-event buffer is emptied, so a command that chains two intent
  methods commits the second one's event and loses the first one's — state is
  written, its event never reaches the outbox;
- the optimistic-concurrency anchor is discarded, so the repository has nothing
  to write `WHERE version = :expected_version` against.

`_evolve` carries all three across the copy. These tests pin that behaviour on
the primitive itself; `tests/unit/architecture/test_domain_pattern_guardrails.py`
keeps the 36 aggregates using it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

import pytest

from app.modules.common.domain.primitives.aggregate_root import AggregateRoot
from app.modules.common.domain.primitives.events import (
    Actor,
    ActorType,
    DomainEvent,
    RequestContext,
)

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
_ACTOR = Actor.create(type=ActorType.SYSTEM, id="test")


@dataclass(frozen=True, slots=True, kw_only=True)
class _CounterChanged(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "test.counter.changed"
    AGGREGATE_TYPE: ClassVar[str] = "Counter"
    SCHEMA_VERSION: ClassVar[int] = 1

    label: str

    @classmethod
    def create(cls, *, aggregate: _Counter, label: str) -> _CounterChanged:
        return cls(
            **cls.create_base(
                event_id=uuid4(),
                organization_id=aggregate.organization_id,
                aggregate_id=aggregate.id,
                aggregate_version=aggregate.version,
                actor=_ACTOR,
                occurred_at=_NOW,
                ctx=RequestContext(),
            ),
            label=label,
        )


@dataclass(frozen=True, slots=True, eq=False)
class _Counter(AggregateRoot):
    """A minimal aggregate whose intent methods are safe to chain."""

    AGGREGATE_TYPE: ClassVar[str | None] = "Counter"

    organization_id: UUID
    count: int

    @classmethod
    def create(cls) -> _Counter:
        return cls(id=uuid4(), organization_id=uuid4(), count=0)

    def _step(self, label: str, *, use_evolve: bool) -> _Counter:
        new_version = self._bump_version()
        updated = (
            self._evolve(count=self.count + 1)
            if use_evolve
            else replace(self, count=self.count + 1)
        )
        updated._set_version(new_version)
        updated.record_event(_CounterChanged.create(aggregate=updated, label=label))
        return updated

    def increment(self, label: str) -> _Counter:
        return self._step(label, use_evolve=True)

    def increment_with_bare_replace(self, label: str) -> _Counter:
        return self._step(label, use_evolve=False)


def _rehydrated(*, count: int = 0, version: int = 7) -> _Counter:
    """A counter as a repository hands it back: version set, anchor marked."""
    counter = _Counter(id=uuid4(), organization_id=uuid4(), count=count)
    counter._set_version(version)
    counter._mark_persisted(version)
    return counter


class TestEventPreservation:
    def test_chained_intent_methods_accumulate_every_event(self) -> None:
        final = _rehydrated().increment("first").increment("second")

        assert [event.label for event in final.pending_events()] == ["first", "second"]

    def test_events_carry_strictly_monotonic_aggregate_versions(self) -> None:
        final = _rehydrated(version=7).increment("first").increment("second")

        assert [event.aggregate_version for event in final.pending_events()] == [8, 9]
        assert final.version == 9

    def test_bare_replace_silently_drops_the_earlier_event(self) -> None:
        """The defect `_evolve` exists to prevent, pinned so it cannot return."""
        final = (
            _rehydrated().increment_with_bare_replace("first").increment_with_bare_replace("second")
        )

        # "first" was recorded, then discarded by the second copy. The state
        # change survives; its event never reaches the outbox.
        assert [event.label for event in final.pending_events()] == ["second"]
        assert final.count == 2

    def test_evolve_does_not_mutate_the_original(self) -> None:
        original = _rehydrated()
        original.increment("first")

        assert original.pending_events() == ()
        assert original.count == 0


class TestOccAnchorPreservation:
    def test_evolve_carries_the_persisted_version_across_the_copy(self) -> None:
        updated = _rehydrated(version=7).increment("first")

        # The row in the database is still at 7 even though the live aggregate
        # has moved to 8 — that 7 is what the repository must match on.
        assert updated.persisted_version == 7
        assert updated.version == 8

    def test_anchor_survives_multiple_bumps_before_a_single_save(self) -> None:
        updated = _rehydrated(version=7).increment("first").increment("second")

        assert updated.persisted_version == 7
        assert updated.version == 9
        # The `version - 1` heuristic would say 8 here and raise a spurious
        # conflict against a row that is still at 7.
        assert updated.persisted_version != updated.version - 1

    def test_bare_replace_discards_the_anchor(self) -> None:
        updated = _rehydrated(version=7).increment_with_bare_replace("first")

        assert not updated.has_persisted_version
        with pytest.raises(RuntimeError, match="persisted_version read before"):
            _ = updated.persisted_version

    def test_a_never_persisted_aggregate_reports_no_anchor(self) -> None:
        fresh = _Counter.create()

        assert not fresh.has_persisted_version
        with pytest.raises(RuntimeError, match="must call _mark_persisted"):
            _ = fresh.persisted_version
