from __future__ import annotations

import abc
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import Any, TypeVar, cast

from app.modules.common.domain.primitives.events import DomainEvent

TRepo = TypeVar("TRepo")


PostCommitHook = Callable[[], None]


class AbstractUnitOfWork(abc.ABC):
    """Unit of Work + Transactional Outbox pattern (how to use it end-to-end)

    This class is the *application-layer transaction boundary* and the single
    place where domain events are drained for reliable publishing.

    The pattern has 4 cooperating layers:

    ----------------------------------------------------------------------
    1) Domain layer (Aggregates) — emits facts, never publishes
    ----------------------------------------------------------------------
    - Aggregates enforce invariants and record DomainEvents *in-memory*:

        event = SomethingHappened.create(...)
        aggregate.record_event(event)

    - Aggregates MUST provide:
        pull_events() -> tuple[DomainEvent, ...]
      which returns buffered events and clears the buffer.

    - Aggregates must never call:
        - uow.commit()
        - repositories
        - outbox
        - message bus

    Domain rule:
        "Domain emits facts; infrastructure delivers them."

    ----------------------------------------------------------------------
    2) Repository layer — tracks touched aggregates & write intent
    ----------------------------------------------------------------------
    Repositories participating in event draining must:
    - maintain `self.seen: SeenAggregates[AggregateRoot]`
      containing all aggregates loaded/saved in this UoW. A plain `set` is
      *not* an acceptable substitute: `Entity.__eq__`/`__hash__` are by
      `(type, id)`, so a set silently collapses the evolved instance that
      carries the pending events into the rehydrated one that does not, and
      the events never reach the outbox. See `SeenAggregates` and
      `tests/unit/architecture/test_uow_seen_tracking.py`.
    - add aggregates to `seen` on:
        - get/load
        - add/save
        - update
    - call `uow.mark_write()` on any write (INSERT/UPDATE/DELETE)

    Example (repository method):

        def save(self, agg: Campaign) -> None:
            self.seen.add(agg)
            with self.uow.cursor() as cur:
                cur.execute("UPDATE ...")
            self.uow.mark_write()

    Why mark_write matters:
    - MessageBus may choose to commit only when writes occurred for event handlers.
      If repos forget to call mark_write(), your event reactions may not commit.

    ----------------------------------------------------------------------
    3) Application handlers — mutate domain, never publish
    ----------------------------------------------------------------------
    Handlers should:
    - be deterministic and side-effect free outside the DB transaction
    - load aggregates via repos
    - call domain methods that record events
    - save aggregates via repos
    - return DTOs/results (optional)

    Handlers MUST NOT:
    - call uow.commit() or uow.rollback()
    - drain events (no collect_new_events())
    - write to outbox directly
    - publish events directly

    Correct handler shape:

        def handle_pause_campaign(cmd: PauseCampaign, uow: AbstractUnitOfWork) -> Result:
            campaign = uow.campaigns.get(cmd.campaign_id)   # repo adds to seen
            campaign = campaign.pause(...)                 # domain records event
            uow.campaigns.save(campaign)                   # repo mark_write()
            return Result(...)

    ----------------------------------------------------------------------
    4) MessageBus — owns transaction boundaries + in-process cascading
    ----------------------------------------------------------------------
    The MessageBus is the orchestrator for *in-process* execution only.

    For each handled message (command or event), the bus must:
    - open a fresh UoW
    - call the handler
    - call uow.commit() exactly once
    - enqueue returned DomainEvents for further in-process handling

    Important:
    - The bus enqueues DomainEvents returned by commit() for cascading.
    - External delivery is handled later by the outbox dispatcher worker.

    Correct bus behavior:

        with uow:
            handler(...)
            new_events = uow.commit()      # drains once + persists outbox
        queue.extend(new_events)

    ----------------------------------------------------------------------
    Transactional Outbox — where it lives and how it works
    ----------------------------------------------------------------------
    The outbox is an infrastructure concern implemented inside the *concrete*
    UoW _commit(events):

    ConcreteUoW._commit(events) MUST:
    1) Map DomainEvents -> IntegrationEvents (stable serialized contract)
    2) Insert IntegrationEvents into outbox table (idempotent on event_id)
    3) Commit the DB transaction (state + outbox together)

    Pseudocode:

        def _commit(self, events):
            integration_events = [mapper.map(e) for e in events]
            outbox_repo.add_all(integration_events)  # same tx, ON CONFLICT DO NOTHING
            conn.commit()

    ----------------------------------------------------------------------
    Outbox Dispatcher Worker (separate process/thread)
    ----------------------------------------------------------------------
    After commit, a separate worker process:
    - leases pending outbox rows (FOR UPDATE SKIP LOCKED)
    - publishes to broker/webhooks/etc
    - marks sent / retries / dead-letters

    The worker is the ONLY place that performs external delivery.

    ----------------------------------------------------------------------
    MessageBus instantiation — composition root only
    ----------------------------------------------------------------------
    When you have many bounded contexts, do NOT build the bus inside a BC.
    Build it at the application boundary (composition root), e.g.:

        app/bootstrap/bootstrap.py

    Recommended wiring structure:
    - each BC exports handler maps in `modules/<bc>/application/registry.py`
    - bootstrap merges registries and constructs the bus:

        def bootstrap() -> MessageBus:
            return MessageBus(
                uow_factory=make_uow_factory(...),
                command_handlers=build_command_handlers(),
                event_handlers_by_type=build_event_handlers_by_type(),
            )

    This keeps BCs isolated and prevents import cycles.

    ----------------------------------------------------------------------
    Common failure modes (and how this design prevents them)
    ----------------------------------------------------------------------
    - Double-draining events:
        Prevented by: UoW.commit() being the only drain point.
    - Publishing before commit:
        Prevented by: outbox write happens inside commit(), delivery is async later.
    - Silent event-loss on retries/crash:
        Prevented by: outbox persistence in same tx + idempotent event_id.
    - Infinite event loops:
        Prevented by: bus max_messages + per-(event_id, handler) dedupe.
    - Handlers committing:
        Prevented by: MessageBus forbid_handler_commit guard.

    ----------------------------------------------------------------------
    Summary
    ----------------------------------------------------------------------
    - Aggregates record DomainEvents (facts)
    - Repositories track seen aggregates + call uow.mark_write() on writes
    - Handlers mutate domain, never commit/publish
    - MessageBus owns UoW lifecycle and enqueues events returned by commit()
    - Concrete UoW persists IntegrationEvents to outbox in the same transaction
    - A separate outbox dispatcher delivers to external systems

    """

    __slots__ = ("_committed", "_has_writes", "_post_commit_hooks", "_repo_cache")

    def __enter__(self) -> AbstractUnitOfWork:
        self._committed = False
        self._has_writes = False
        self._repo_cache: dict[str, object] = {}
        self._post_commit_hooks: list[PostCommitHook] = []
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self._post_commit_hooks.clear()
                self.rollback()
        finally:
            # Repositories belong to this transaction, not the UoW object's
            # eventual GC lifetime. Each can point back here and retain large
            # tracked snapshots in a cycle, including on a failed rollback.
            # Keep successful post-commit hooks: some owners run them after
            # returning the connection; explicitly captured inputs stay alive.
            self._repo_cache.clear()

    def commit(self) -> tuple[DomainEvent, ...]:
        """Commit the current transaction.

        Contract:
        - Drain domain events exactly once via `collect_new_events()` (uses pull_events()).
        - Persist state + integration outbox entries atomically in `_commit(events)`.
        - Return drained DomainEvents for in-process cascading (MessageBus).
        """
        events = self.collect_new_events()  # <-- single drain point
        try:
            self._commit(events)
        except Exception:
            self.rollback()
            raise
        else:
            self._committed = True
            return events

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def has_writes(self) -> bool:
        return self._has_writes

    def mark_write(self) -> None:
        """Mark the UoW as having performed a write."""
        self._has_writes = True

    def cursor(self) -> Any:
        """Return a driver cursor context manager for infrastructure repositories."""
        raise NotImplementedError

    def dict_cursor(self) -> Any:
        """Return a dict-row driver cursor context manager for query-heavy repositories."""
        raise NotImplementedError

    @contextmanager
    def savepoint(self, name: str) -> Iterator[None]:
        """Isolate a sub-block of this UoW's transaction behind a SAVEPOINT.

        Use around a best-effort or per-item write that shares this UoW's
        connection with other work (e.g. one record in a batch-processing loop)
        so that a failure there rolls back only that sub-block instead of
        poisoning the whole transaction with InFailedSqlTransaction, which
        would otherwise abort every statement that follows until the outer
        transaction rolls back entirely.

        The failure still propagates to the caller — this only bounds its
        blast radius on the shared connection, it does not swallow the error.

        Rolling back the rows is only half of undoing the block: aggregates
        created or evolved inside it are still tracked in the repositories'
        ``seen`` collections with their pending events, and ``commit()`` would
        publish those events (e.g. a ``ChargeRecorded`` for a charge row that
        no longer exists) to the outbox. On rollback the pending events of
        every aggregate *instance* first tracked inside the block are therefore
        discarded too. Instances tracked before the block keep theirs:
        aggregates are immutable, so a transition inside the block produces a
        new instance rather than adding events to an old one.
        """
        tracked_before = self._tracked_aggregate_ids()
        with self.cursor() as cur:
            cur.execute(f"SAVEPOINT {name}")
        try:
            yield
        except Exception:
            with self.cursor() as cur:
                cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._discard_events_tracked_since(tracked_before)
            raise
        else:
            with self.cursor() as cur:
                cur.execute(f"RELEASE SAVEPOINT {name}")

    def _tracked_aggregate_ids(self) -> frozenset[int]:
        """Object identities of every aggregate instance tracked by a repository."""
        tracked: set[int] = set()
        for repo in self._iter_repositories():
            seen = getattr(repo, "seen", None)
            if seen:
                tracked.update(id(agg) for agg in list(seen))
        return frozenset(tracked)

    def _discard_events_tracked_since(self, tracked_before: frozenset[int]) -> None:
        """Drop the pending events of aggregate instances first tracked after a savepoint."""
        for repo in self._iter_repositories():
            seen = getattr(repo, "seen", None)
            if not seen:
                continue
            for agg in list(seen):
                if id(agg) in tracked_before:
                    continue
                pull = getattr(agg, "pull_events", None)
                if pull is not None:
                    pull()

    def register_post_commit_hook(self, hook: PostCommitHook) -> None:
        """Register a callback to run only after this UoW commits successfully."""
        self._post_commit_hooks.append(hook)

    def run_post_commit_hooks(self) -> None:
        hooks = list(self._post_commit_hooks)
        self._post_commit_hooks.clear()
        for hook in hooks:
            hook()

    # -------------------------
    # Repository cache
    # -------------------------

    def register_repository(self, key: str, repo: object) -> None:
        """Register a repository for event collection."""
        self._repo_cache[key] = repo

    def _get_repo(self, key: str, factory: Callable[[], TRepo]) -> TRepo:
        """Cache a repository for this transaction; context exit releases the cache."""
        repo = self._repo_cache.get(key)
        if repo is None:
            repo = factory()
            self._repo_cache[key] = repo
        return cast("TRepo", repo)

    def _iter_repositories(self) -> Iterable[object]:
        return self._repo_cache.values()

    # -------------------------
    # Event collection
    # -------------------------

    def collect_new_events(self) -> tuple[DomainEvent, ...]:
        """Drain all newly recorded domain events from aggregates seen by repositories.

        Contract:
        - Repositories maintain a `.seen` container (a `SeenAggregates`, or a
          `list`) of every aggregate *instance* loaded or saved in this UoW.
          Every instance matters, not every aggregate: an immutable aggregate
          records its event on the new instance returned by `_evolve()`, so a
          container that keeps only one instance per aggregate id drops events.
        - Aggregates expose `pull_events()`, returning the buffered events and
          clearing the buffer (required to avoid duplicate publishing).
        """
        events: list[DomainEvent] = []
        drained: set[int] = set()  # id(agg)

        for repo in self._iter_repositories():
            seen = getattr(repo, "seen", None)
            if not seen:
                continue

            for agg in list(seen):
                obj_id = id(agg)
                if obj_id in drained:
                    continue
                drained.add(obj_id)

                pull = getattr(agg, "pull_events", None)
                if pull is None:
                    raise RuntimeError(
                        f"Aggregate {type(agg).__name__} must implement pull_events()",
                    )
                pulled = list(pull())
                events.extend(pulled)

        return tuple(events)

    # -------------------------
    # Infrastructure hooks
    # -------------------------

    @abc.abstractmethod
    def _commit(self, events: tuple[DomainEvent, ...]) -> None:
        """Infrastructure commit hook (persist state + outbox within same tx)."""
        raise NotImplementedError

    @abc.abstractmethod
    def rollback(self) -> None:
        raise NotImplementedError


class ReadOnlyUnitOfWorkGuard(AbstractUnitOfWork):
    """Proxy that allows reads but fails fast on any persistence-oriented operation.

    Inherits from AbstractUnitOfWork so it is a valid substitute in any handler/factory
    typed against the abstract base. All write/commit/hook entry points raise; read paths
    delegate to the underlying UoW via __getattr__.
    """

    # Note: no __slots__ here — adding instance attrs (_uow, _operation_name) alongside the
    # base class' slots would require redeclaring all of them. Drop slots; the perf cost
    # is negligible (one short-lived guard per query) and gains us clean inheritance.

    def __init__(self, uow: AbstractUnitOfWork, *, operation_name: str) -> None:
        self._uow = uow
        self._operation_name = operation_name

    def __getattr__(self, name: str) -> Any:
        # Triggered only when normal attribute lookup fails — safe to proxy everything else
        # (cursor(), dict_cursor(), repositories) to the underlying UoW.
        return getattr(self._uow, name)

    def cursor(self) -> Any:
        return self._uow.cursor()

    def dict_cursor(self) -> Any:
        return self._uow.dict_cursor()

    def execute(
        self,
        query: str,
        params: Any = None,
        *,
        fetch: bool = False,
        fetchone: bool = False,
        fetchall: bool = False,
    ) -> Any:
        with self._uow.cursor() as cur:
            cur.execute(query, params)
            if fetchone:
                return cur.fetchone()
            if fetch or fetchall:
                return cur.fetchall()
            return None

    def commit(self) -> tuple[DomainEvent, ...]:
        raise RuntimeError(f"{self._operation_name} must not commit state")

    def mark_write(self) -> None:
        raise RuntimeError(f"{self._operation_name} must not perform writes")

    def register_post_commit_hook(self, hook: PostCommitHook) -> None:
        raise RuntimeError(f"{self._operation_name} must not register post-commit hooks")

    # AbstractUnitOfWork abstract methods — guard never persists, so these raise.
    def _commit(self, events: tuple[DomainEvent, ...]) -> None:
        raise RuntimeError(f"{self._operation_name} must not commit state")

    def rollback(self) -> None:  # pragma: no cover - guard never enters write tx
        # Delegate to the underlying UoW so an outer context manager can still abort cleanly.
        self._uow.rollback()


class SelfManagedCommandUnitOfWork(AbstractUnitOfWork):
    """No-op UoW for commands that own their internal transaction boundaries."""

    def cursor(self) -> Any:
        raise RuntimeError("Self-managed command UoW has no database cursor")

    def dict_cursor(self) -> Any:
        raise RuntimeError("Self-managed command UoW has no database cursor")

    def _commit(self, events: tuple[DomainEvent, ...]) -> None:
        if events:
            raise RuntimeError("Self-managed command UoW cannot persist domain events")

    def rollback(self) -> None:
        return None
