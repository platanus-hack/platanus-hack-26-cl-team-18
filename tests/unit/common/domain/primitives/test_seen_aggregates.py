"""`SeenAggregates` keeps every version of an aggregate that still owes events.

These tests encode a real failure: a repository that tracked `seen` in a
plain `set` silently dropped the evolved instance carrying a pending event,
because `Entity.__eq__`/`__hash__`
are by `(type, id)` and the rehydrated instance was already in the set. State
committed without its event.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

from app.modules.common.domain.primitives.aggregate_root import AggregateRoot
from app.modules.common.domain.primitives.events import Actor, ActorType, DomainEvent
from app.modules.common.domain.primitives.seen_aggregates import SeenAggregates


@dataclass(frozen=True, slots=True, kw_only=True)
class _Renamed(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "test.thing.renamed"
    AGGREGATE_TYPE: ClassVar[str] = "_Thing"
    SCHEMA_VERSION: ClassVar[int] = 1

    name: str


@dataclass(frozen=True, slots=True, eq=False)
class _Thing(AggregateRoot):
    name: str = ""

    def rename(self, *, new_name: str) -> _Thing:
        new_version = self.version + 1
        evolved = replace(self, name=new_name)
        evolved._set_version(new_version)
        evolved.record_event(
            _Renamed(
                **_Renamed.create_base(
                    event_id=uuid4(),
                    organization_id=uuid4(),
                    aggregate_id=evolved.id,
                    aggregate_version=evolved.version,
                    actor=Actor.create(type=ActorType.SYSTEM, id=str(uuid4())),
                    occurred_at=datetime.now(UTC),
                ),
                name=new_name,
            )
        )
        return evolved


def _thing(thing_id: UUID | None = None, *, name: str = "before") -> _Thing:
    return _Thing(id=thing_id or uuid4(), name=name)


def test_the_evolved_instance_survives_a_load_then_save_cycle() -> None:
    """The production defect, in miniature: `get_for_update()` then `save()`."""
    seen: SeenAggregates[_Thing] = SeenAggregates()
    rehydrated = _thing()

    seen.add(rehydrated)  # repository.get_for_update()
    evolved = rehydrated.rename(new_name="after")
    seen.add(evolved)  # repository.save()

    tracked = list(seen)
    assert tracked == [evolved]
    assert tracked[0] is evolved
    assert [event.EVENT_TYPE for event in tracked[0].pending_events()] == ["test.thing.renamed"]


def test_two_versions_that_both_owe_events_are_both_kept() -> None:
    """A command that saves twice must not lose the first batch of events."""
    seen: SeenAggregates[_Thing] = SeenAggregates()
    first = _thing().rename(new_name="one")
    second = first.rename(new_name="two")

    seen.add(first)
    seen.add(second)

    assert [instance is first for instance in seen] == [True, False]
    assert list(seen) == [first, second]


def test_drained_duplicates_collapse_so_seen_does_not_grow_unboundedly() -> None:
    """Re-decoding the same row repeatedly must not leak instances."""
    seen: SeenAggregates[_Thing] = SeenAggregates()
    thing_id = uuid4()

    for _ in range(50):
        seen.add(_thing(thing_id))

    assert len(seen) == 1


def test_an_instance_whose_events_were_pulled_is_evicted_by_the_next_version() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates()
    first = _thing().rename(new_name="one")
    seen.add(first)
    first.pull_events()  # the UoW drained it

    second = first.rename(new_name="two")
    seen.add(second)

    assert list(seen) == [second]


def test_adding_the_same_instance_twice_is_a_no_op() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates()
    evolved = _thing().rename(new_name="after")

    seen.add(evolved)
    seen.add(evolved)

    assert len(seen) == 1


def test_different_aggregates_are_tracked_independently() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates()
    left = _thing(name="left")
    right = _thing(name="right")

    seen.update([left, right])

    assert len(seen) == 2
    assert set(seen) == {left, right}


def test_discard_untracks_every_version_of_the_aggregate() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates()
    rehydrated = _thing()
    evolved = rehydrated.rename(new_name="after")
    seen.update([rehydrated, evolved])

    seen.discard(rehydrated)

    assert list(seen) == []
    seen.discard(rehydrated)  # idempotent


def test_remove_raises_for_an_untracked_aggregate() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates()
    tracked = _thing()
    seen.add(tracked)

    seen.remove(tracked)
    assert list(seen) == []

    try:
        seen.remove(tracked)
    except KeyError:
        pass
    else:  # pragma: no cover - the assertion below reports the failure
        raise AssertionError("remove() must raise KeyError for an untracked aggregate")


def test_membership_is_by_aggregate_identity_not_by_instance() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates()
    rehydrated = _thing()
    seen.add(rehydrated)

    assert rehydrated in seen
    assert rehydrated.rename(new_name="after") in seen
    assert _thing() not in seen
    assert "not an aggregate" not in seen


def test_clear_and_bool_and_len_and_repr() -> None:
    seen: SeenAggregates[_Thing] = SeenAggregates([_thing(), _thing()])

    assert bool(seen) is True
    assert len(seen) == 2
    assert "SeenAggregates" in repr(seen)

    seen.clear()

    assert bool(seen) is False
    assert len(seen) == 0
    assert list(seen) == []


def test_constructor_accepts_an_initial_iterable() -> None:
    rehydrated = _thing()
    evolved = rehydrated.rename(new_name="after")

    seen: SeenAggregates[_Thing] = SeenAggregates([rehydrated, evolved])

    assert list(seen) == [evolved]


def test_objects_without_an_id_are_keyed_by_object_identity() -> None:
    """Some repositories track non-aggregates (`SeenAggregates[object]`).

    They have no `id` to collapse on, and dropping something we cannot inspect
    is exactly the failure mode this class exists to prevent, so each instance
    is kept.
    """
    seen: SeenAggregates[object] = SeenAggregates()
    first = object()
    second = object()

    seen.update([first, second, first])

    assert len(seen) == 2
    assert first in seen
    assert second in seen
