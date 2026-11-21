"""Aggregate tracking for Unit-of-Work event draining.

`AbstractUnitOfWork.collect_new_events()` drains domain events by walking every
aggregate a repository recorded in its ``seen`` collection. Which container a
repository uses for ``seen`` is therefore a correctness concern, not a style
choice, and a plain ``set`` is the wrong one.

``Entity.__eq__``/``__hash__`` compare by ``(type, id)``, so every version of an
aggregate is "the same element" to a set. In the canonical immutable flow:

    workflow = repo.get_for_update(...)      # rehydrated, no pending events
    repo.save(workflow.mark_prepared(...))   # NEW instance carrying the event

the ``set.add`` in ``save`` is a silent no-op: the rehydrated, event-less
instance stays in the set, the evolved one is discarded, and the event it
recorded is never published. State commits without its event — exactly what the
transactional outbox exists to prevent.

`SeenAggregates` is the single sanctioned container for ``repo.seen``. It never
drops an instance that still holds pending events, while collapsing already
drained duplicates of the same aggregate so read-heavy paths do not accumulate
one entry per decoded row.

See `tests/unit/architecture/test_uow_seen_tracking.py`, which
rejects any repository that tracks ``seen`` in a bare ``set``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


def _has_pending_events(aggregate: object) -> bool:
    pending = getattr(aggregate, "pending_events", None)
    if pending is None:
        # Not an AggregateRoot (or a test double). Keep it: dropping something
        # we cannot inspect is the failure mode this class exists to prevent.
        return True
    try:
        return bool(pending())
    except AttributeError:
        # A partially constructed instance (``object.__new__`` in a test double,
        # or an aggregate mid-rehydration) cannot answer. Same rule as above:
        # when in doubt, keep it, so no event can be dropped.
        return True


class SeenAggregates(Generic[T]):
    """Aggregates a repository touched in this UoW, tracked by object identity.

    Set-like on purpose: repositories and their tests already speak ``add``,
    ``discard``, ``update``, ``in``, iteration and ``len``. The difference is
    what happens when two *different instances* of the same aggregate are
    tracked — a set keeps the first, this keeps the ones that still carry
    unpublished events.
    """

    __slots__ = ("_buckets",)

    def __init__(self, initial: Iterable[T] | None = None) -> None:
        # (type, aggregate id) -> instances of that aggregate, in tracking order.
        self._buckets: dict[tuple[type, object], list[T]] = {}
        if initial is not None:
            self.update(initial)

    # -- tracking -----------------------------------------------------------

    def add(self, aggregate: T) -> None:
        """Track ``aggregate``, keeping any sibling instance that still has events."""
        bucket = self._buckets.setdefault(_key(aggregate), [])
        if any(tracked is aggregate for tracked in bucket):
            return
        # Siblings whose events were already drained (or that never had any)
        # carry no information the UoW still needs, so they can be collapsed.
        bucket[:] = [tracked for tracked in bucket if _has_pending_events(tracked)]
        bucket.append(aggregate)

    def update(self, aggregates: Iterable[T]) -> None:
        for aggregate in aggregates:
            self.add(aggregate)

    def discard(self, aggregate: T) -> None:
        """Stop tracking every instance of ``aggregate``.

        Matches by aggregate identity, mirroring ``set.discard`` under
        ``Entity.__eq__``. Callers use this to untrack an aggregate entirely
        (e.g. after a delete), so pending events are intentionally dropped.
        """
        self._buckets.pop(_key(aggregate), None)

    def remove(self, aggregate: T) -> None:
        if _key(aggregate) not in self._buckets:
            raise KeyError(aggregate)
        self.discard(aggregate)

    def clear(self) -> None:
        self._buckets.clear()

    # -- inspection ---------------------------------------------------------

    def __iter__(self) -> Iterator[T]:
        for bucket in list(self._buckets.values()):
            yield from bucket

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._buckets.values())

    def __contains__(self, aggregate: object) -> bool:
        try:
            key = _key(aggregate)
        except AttributeError:
            return False
        return key in self._buckets

    def __bool__(self) -> bool:
        return bool(self._buckets)

    def __repr__(self) -> str:
        return f"SeenAggregates({list(self)!r})"


def _key(aggregate: object) -> tuple[type, object]:
    """Identity of the *aggregate*, shared by all of its immutable versions.

    Mirrors ``Entity.__hash__``. Anything without an ``id`` is not an aggregate
    and is keyed by object identity, so it is never collapsed into a sibling.
    """
    aggregate_id = getattr(aggregate, "id", None)
    if aggregate_id is None:
        return (type(aggregate), id(aggregate))
    return (type(aggregate), aggregate_id)
