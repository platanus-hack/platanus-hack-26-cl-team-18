from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Self, TypeVar

from app.modules.common.domain.primitives.entity import Entity
from app.modules.common.domain.primitives.events import DomainEvent

_AggregateRootT = TypeVar("_AggregateRootT", bound="AggregateRoot")


@dataclass(frozen=True, slots=True, eq=False)
class AggregateRoot(Entity):
    """Base class for Aggregate Roots in the domain model.

    An Aggregate Root is the *only* entry point for modifying a consistency
    boundary. All cross-field invariants are enforced here, and all DomainEvents
    are emitted by aggregates (never by Entities or Value Objects).

    This base class is designed to work cleanly with:
    - Commands/Handlers (application layer)
    - MessageBus (dispatch + publish orchestration)
    - Unit of Work (transaction boundary)
    - Transactional Outbox (atomic persistence of events + state)

    ----------------------------------------------------------------------
    Responsibilities
    ----------------------------------------------------------------------

    - Own a stable business identifier (`id`) via Entity.
    - Track a monotonic `version` (>= 1) used for:
        * optimistic concurrency control (OCC)
        * deterministic ordering of changes per aggregate
        * idempotent downstream processing / replay
    - Buffer DomainEvents produced by state changes so they can be persisted
      and published reliably via the UoW + Outbox pipeline.

    ----------------------------------------------------------------------
    How this integrates with Commands, UoW, Bus, and Outbox
    ----------------------------------------------------------------------

    Typical command handling flow:

      1) CommandHandler receives a Command (intent)
      2) Handler opens a UnitOfWork
      3) Handler loads aggregate(s) via repositories
      4) Handler calls aggregate methods (enforce invariants, return new aggregate)
      5) Aggregate records DomainEvent(s) via record_event(...)
      6) Handler saves aggregates via repositories
      7) UoW commits:
           - persists aggregate state
           - drains events from aggregates via pull_events()
           - stores drained events in the Transactional Outbox (same DB tx)
      8) After commit, an outbox dispatcher publishes events to the MessageBus /
         broker asynchronously and reliably

    Key property:
    - The domain never publishes directly.
    - Events are published only if the database transaction commits.

    ----------------------------------------------------------------------
    What an Aggregate Root MUST do
    ----------------------------------------------------------------------

    - Enforce all invariants before emitting events.
    - Emit DomainEvents only *after* state has been validated.
    - Record events via record_event() (append-only buffer).
    - Ensure each recorded event:
        * refers to this aggregate (`aggregate_id`)
        * declares the correct aggregate type (`aggregate_type`)
        * uses the current aggregate version (`aggregate_version`)

    ----------------------------------------------------------------------
    What an Aggregate Root MUST NOT do
    ----------------------------------------------------------------------

    - Generate IDs.
    - Accept raw primitives when a Value Object exists.
    - Perform parsing, normalization, or coercion of Value Objects (create()).
    - Call clocks or generate timestamps.
    - Publish events directly to a message bus / broker.
    - Perform I/O, persistence, or infrastructure concerns.
    - Emit events from entities or value objects.
    - Mutate recorded events after creation.

    ----------------------------------------------------------------------
    Immutability & versioning model
    ----------------------------------------------------------------------

    This base class supports *immutable aggregates*.

    Typical update flow:
      1) Compute `new_version = self._bump_version()`
      2) Create a new aggregate instance with `version=new_version`
      3) Create DomainEvent(s) with `aggregate_version=new_version`
      4) Record events on the new aggregate
      5) Return the new aggregate instance

    This guarantees:
    - aggregate.version == event.aggregate_version
    - strict ordering of events per aggregate
    - reproducible audit logs

    ----------------------------------------------------------------------
    Event buffering and draining: pending_events() vs pull_events()
    ----------------------------------------------------------------------

    - pending_events():
        Read-only view of the currently buffered events.
        Useful for debugging/tests, but MUST NOT be used by the UoW/outbox.

    - pull_events():
        REQUIRED for the UoW/outbox pipeline.
        It drains buffered events and clears the buffer in one step.

        Why this matters:
        - prevents double-publishing within the same request
        - prevents duplicate publish on retries
        - prevents accidental re-publication of rehydrated aggregates
        - supports at-most-once draining per successful commit

    UnitOfWork MUST call pull_events() (not pending_events()) when collecting
    events for the transactional outbox.

    ----------------------------------------------------------------------
    Rehydration rule
    ----------------------------------------------------------------------

    Aggregates reconstructed from persistence MUST NOT contain pending events.
    Repositories should call `_assert_no_pending_events()` after rehydration.
    Any event publication should occur only from events created during the
    current command handling flow.

    ----------------------------------------------------------------------
    Invariants
    ----------------------------------------------------------------------

    - `version` must be an integer >= 1.
    - Recorded events must satisfy:
        * event.aggregate_id == self.id
        * event.aggregate_type == self.aggregate_type
        * event.aggregate_version == self.version
    """

    version: int = field(default=1, init=False)
    # The version as last loaded from / written to persistence — the anchor a
    # repository's optimistic-concurrency ``WHERE version = ...`` clause must
    # match. It stays correct when a single command applies *multiple* version
    # bumps before one ``save()`` (e.g. ``edit()`` then ``assign_to_members()``),
    # where the older ``version - 1`` heuristic points past the row's real DB
    # version and raises a spurious OCC conflict.
    #
    # ``None`` means "not yet persisted / unknown": a freshly ``create()``d
    # aggregate has no DB row until ``add()`` writes one. Reading
    # ``persisted_version`` in that state raises, so a repository that forgets to
    # call ``_mark_persisted`` on rehydration fails loudly instead of silently
    # comparing against a wrong default.
    _persisted_version: int | None = field(default=None, init=False, repr=False)
    _events: tuple[DomainEvent, ...] = field(default_factory=tuple, init=False, repr=False)

    AGGREGATE_TYPE: ClassVar[str | None] = None  # optional override if you want stable name

    def __post_init__(self) -> None:
        Entity.__post_init__(self)
        self._validate_version(self.version)

    @staticmethod
    def _validate_version(value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("AggregateRoot.version must be int")
        if value < 1:
            raise ValueError("AggregateRoot.version must be >= 1")

    def _set_version(self, value: int) -> None:
        self._validate_version(value)
        object.__setattr__(self, "version", value)

    @property
    def has_persisted_version(self) -> bool:
        """Whether this aggregate's DB version is known (rehydrated or written)."""
        return self._persisted_version is not None

    @property
    def persisted_version(self) -> int:
        """The version currently stored in persistence (the OCC anchor).

        Repositories MUST match their optimistic-concurrency ``WHERE version =``
        clause against this value, not ``version - 1``: a command may bump the
        live ``version`` more than once before a single ``save()``.

        Raises if the aggregate has never been persisted or rehydrated — that is
        a programming error (e.g. a repository's rehydration path that forgot to
        call ``_mark_persisted``), and failing loudly is safer than silently
        comparing OCC against a wrong value.
        """
        if self._persisted_version is None:
            raise RuntimeError(
                f"{self.aggregate_type}.persisted_version read before the aggregate "
                "was persisted or rehydrated; rehydration must call _mark_persisted()",
            )
        return self._persisted_version

    def _mark_persisted(self, value: int) -> None:
        """Record the version that exists in persistence.

        Called by repositories after rehydration (with the row's version) and
        after a successful insert/update (with the just-written version).
        """
        self._validate_version(value)
        object.__setattr__(self, "_persisted_version", value)

    @property
    def aggregate_type(self) -> str:
        # Prefer explicit contract name if you ever rename classes.
        return self.AGGREGATE_TYPE or type(self).__name__

    def pending_events(self) -> tuple[DomainEvent, ...]:
        return self._events

    def _assert_no_pending_events(self) -> None:
        if self._events:
            raise RuntimeError("Rehydrated aggregates must not have pending events")

    def record_event(self, event: DomainEvent) -> None:
        """Record a domain event (append-only buffer).

        The outbox/uow drains these after a successful commit.
        """
        if not isinstance(event, DomainEvent):
            raise TypeError("event must be DomainEvent")

        if event.aggregate_id != self.id:
            raise ValueError("event.aggregate_id must match aggregate id")

        if event.aggregate_type != self.aggregate_type:
            raise ValueError("event.aggregate_type must match aggregate type")

        # Core: event ordering + idempotency anchor
        if event.aggregate_version != self.version:
            raise ValueError(
                "event.aggregate_version must match aggregate version "
                f"(expected {self.version}, got {event.aggregate_version})",
            )

        object.__setattr__(self, "_events", self._events + (event,))

    def pull_events(self) -> tuple[DomainEvent, ...]:
        """Drain pending events and clear the buffer.

        Used by UnitOfWork/Outbox to ensure events are published at-most-once
        per successful commit.
        """
        evts = self._events
        object.__setattr__(self, "_events", ())
        return evts

    def _bump_version(self) -> int:
        """Return the next version (does not mutate automatically).
        Use this when creating an updated copy of an immutable aggregate.
        """
        return self.version + 1

    def _evolve(self, **changes: Any) -> Self:
        """Return a copy of this aggregate with the given fields replaced,
        preserving the pending-event buffer.

        Why this exists:
            ``dataclasses.replace`` resets fields declared with ``init=False``
            (``version`` and ``_events``) to their defaults on the new
            instance. Intent methods that chain within a single command/event
            handler — e.g. ``case.update_amount_paid(...).mark_paid(...)`` —
            therefore lose every event recorded prior to the final call.

            ``_evolve`` is the intent-method-safe alternative: it copies via
            ``replace`` and then re-attaches the previous instance's pending
            events on the copy. The caller still bumps the version explicitly
            via ``_set_version(new_version)`` and records the new event
            afterwards, so each step in a chain accumulates events with
            strictly monotonic ``aggregate_version`` values.

        Use this in domain intent methods instead of calling
        ``dataclasses.replace(self, ...)`` directly.
        """
        new = replace(self, **changes)
        object.__setattr__(new, "_events", self._events)
        # ``replace`` resets init=False fields to their defaults; carry the OCC
        # anchor forward so chained intent methods don't lose the DB version.
        object.__setattr__(new, "_persisted_version", self._persisted_version)
        return new
