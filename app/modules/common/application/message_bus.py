from __future__ import annotations

import functools
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg2.errors

from app.config.logging_context import bind_context
from app.modules.common.application.command import Command
from app.modules.common.application.serialization_retry import (
    DEFAULT_BACKOFF_MAX_SECONDS,
    DEFAULT_BACKOFF_MIN_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
)
from app.modules.common.application.uow import AbstractUnitOfWork
from app.modules.common.domain.primitives.events import DomainEvent

Message = Command | DomainEvent

CommandHandler = Callable[[Any, AbstractUnitOfWork], object | None]
EventHandler = Callable[[DomainEvent, AbstractUnitOfWork], None]


# Postgres concurrency errors that are safely retryable at the message level.
# Each command / event handler runs in its own UoW; if commit fails with one
# of these, nothing was persisted and re-running the handler is correct.
#   - SerializationFailure (40001): SERIALIZABLE pivot abort or read/write cycle
#   - DeadlockDetected     (40P01): deadlock victim
_RETRYABLE_DB_ERRORS: tuple[type[BaseException], ...] = (
    psycopg2.errors.SerializationFailure,
    psycopg2.errors.DeadlockDetected,
)

# A price-save burst can contain several independent, idempotent commands.  A
# 10–50 ms retry window merely repeats the collision before the winning
# transaction commits, so retain the same safe fresh-UoW retry but give it time
# to disperse contending requests.
_DEFAULT_RETRY_ATTEMPTS = DEFAULT_RETRY_ATTEMPTS
_DEFAULT_BACKOFF_MIN_SECONDS = DEFAULT_BACKOFF_MIN_SECONDS
_DEFAULT_BACKOFF_MAX_SECONDS = DEFAULT_BACKOFF_MAX_SECONDS


def _ctx_get(obj: object, name: str) -> object | None:
    ctx = getattr(obj, "ctx", None)
    return getattr(ctx, name, None) if ctx is not None else None


@dataclass(slots=True)
class MessageBus:
    """MessageBus — Application-layer orchestration for Commands and DomainEvents

    The MessageBus is the *only* component allowed to:
    - create UnitOfWork instances
    - call `uow.commit()`
    - trigger in-process event cascading

    It coordinates **in-process execution only**.
    External delivery (Kafka, webhooks, queues, etc.) is handled separately
    via the Transactional Outbox + dispatcher workers.

    ----------------------------------------------------------------------
    What this MessageBus IS
    ----------------------------------------------------------------------

    - An in-process dispatcher for:
        * Commands (intent: "please do X")
        * DomainEvents (facts: "X already happened")

    - A transaction boundary orchestrator:
        * One fresh UnitOfWork per handler invocation
        * Deterministic commit / rollback behavior
        * Exactly-once domain-event draining per transaction

    - A safety layer:
        * Prevents handlers from committing directly
        * Guards against infinite event loops
        * Ensures consistent tracing / logging context

    ----------------------------------------------------------------------
    What this MessageBus is NOT
    ----------------------------------------------------------------------

    - NOT an async message broker
    - NOT an event publisher to external systems
    - NOT responsible for retries across process restarts
    - NOT allowed to bypass the UnitOfWork

    If you need durability or cross-process delivery:
        → use the Transactional Outbox + worker.

    ----------------------------------------------------------------------
    How to use it (happy path)
    ----------------------------------------------------------------------

    1) Instantiate the bus at the *composition root* (bootstrap layer):

        bus = MessageBus(
            uow_factory=make_uow_factory(...),
            command_handlers={CreateCampaign: handle_create_campaign},
            event_handlers_by_type={
                "campaigns.campaign.created": [on_campaign_created],
            },
        )

    2) Entry point (API / job / CLI) calls:

        result = bus.handle(command)

    3) For a Command:
        - The bus:
            * opens a new UnitOfWork
            * binds request context (correlation, idempotency, etc.)
            * calls the command handler
            * calls uow.commit()
            * enqueues returned DomainEvents for in-process handling

    4) For each resulting DomainEvent:
        - The bus:
            * resolves handlers by stable event_type
            * runs each handler in its own UnitOfWork
            * commits only if the handler performed writes
            * enqueues any newly produced DomainEvents

    5) Processing continues until the in-memory queue is empty.

    The return value of `handle()` is:
        - the result of the *initial command handler*
        - events do not return values

    ----------------------------------------------------------------------
    Handler contracts (VERY IMPORTANT)
    ----------------------------------------------------------------------

    Command handlers MUST:
    - mutate aggregates via repositories
    - let aggregates record DomainEvents
    - return a result (optional)

    Command handlers MUST NOT:
    - call uow.commit() or uow.rollback()
    - publish events
    - drain events
    - create new UnitOfWork instances

    Event handlers MUST:
    - react to DomainEvents
    - optionally mutate aggregates
    - optionally record *new* DomainEvents

    Event handlers MUST NOT:
    - assume they run in the same transaction as the producer
    - call uow.commit() or uow.rollback()
    - publish integration events directly

    If a handler violates this, the MessageBus will fail fast.

    ----------------------------------------------------------------------
    Transaction + event semantics
    ----------------------------------------------------------------------

    - Commands are STRICT:
        * Always committed
        * If they fail, the error is raised

    - Events are BEST-EFFORT by default:
        * Exceptions are logged
        * Processing continues unless `best_effort_events=False`

    - DomainEvents are drained exactly once:
        * uow.commit() drains buffered events
        * drained events are returned and enqueued
        * handlers never call collect_new_events()

    - Event handlers commit only if writes occurred:
        * avoids empty commits
        * relies on repositories calling uow.mark_write()

    ----------------------------------------------------------------------
    Safety mechanisms built in
    ----------------------------------------------------------------------

    - max_messages:
        Prevents infinite cascades caused by event loops.

    - dedupe_event_handlers_within_call:
        Ensures the same (event_id, handler) pair is not executed twice
        in a single in-process cascade.

    - forbid_handler_commit:
        Enforces the rule that ONLY the MessageBus controls commit().

    ----------------------------------------------------------------------
    Event routing rules
    ----------------------------------------------------------------------

    - Events are routed by:
        DomainEvent.EVENT_TYPE (stable string)

    - Python class-based routing is intentionally avoided to ensure:
        * stable routing for outbox / integrations
        * schema evolution without import coupling

    ----------------------------------------------------------------------
    Where this fits in the architecture
    ----------------------------------------------------------------------

    API / Job / CLI
        ↓
    MessageBus.handle(Command)
        ↓
    CommandHandler (domain mutation)
        ↓
    UnitOfWork.commit()
        ↓
    Transactional Outbox (DB)
        ↓
    In-process DomainEvent handlers
        ↓
    (async worker later publishes IntegrationEvents externally)

    ----------------------------------------------------------------------
    Summary (rules of thumb)
    ----------------------------------------------------------------------

    - If you want to CHANGE state → use a Command
    - If something HAS HAPPENED → emit a DomainEvent
    - If you need durability → outbox, not the bus
    - If you need retries across crashes → outbox worker
    - If you see `uow.commit()` in a handler → it's a bug

    This MessageBus is the backbone of deterministic, auditable,
    event-driven behavior in a modular monolith.
    """

    uow_factory: Callable[[], AbstractUnitOfWork]

    # Commands: class-based dispatch is fine (commands are in-process).
    command_handlers: dict[type[Command], CommandHandler]

    # Events: stable routing by event_type (recommended).
    event_handlers_by_type: dict[str, list[EventHandler]]

    command_uow_factories: dict[type[Command], Callable[[], AbstractUnitOfWork]] = field(
        default_factory=dict,
    )

    # Queries: read-only dispatching without a UoW transaction.
    # Handler signature: (query) -> result
    query_handlers: dict[type, Callable[[object], object]] = field(default_factory=dict)

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    # Event handling policy
    best_effort_events: bool = True

    # Safety
    max_messages: int = 10_000
    dedupe_event_handlers_within_call: bool = True

    # Strong rule: handlers MUST NOT call uow.commit(); the bus owns commit.
    forbid_handler_commit: bool = True

    # Backstop for transient Postgres concurrency errors (SerializationFailure,
    # DeadlockDetected). Each retry runs a fresh UoW from scratch, so it is
    # safe only because nothing committed on the previous attempt. Structural
    # races (e.g. SELECT-then-INSERT) should be fixed at the source — this is
    # the safety net for everything else.
    serialization_retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS
    serialization_retry_min_seconds: float = _DEFAULT_BACKOFF_MIN_SECONDS
    serialization_retry_max_seconds: float = _DEFAULT_BACKOFF_MAX_SECONDS

    def execute(self, message: object) -> object | None:
        """Dispatch a Command, DomainEvent, or Query.

        Queries are resolved via query_handlers (no UoW transaction opened).
        Commands and DomainEvents are handled by handle() with full UoW lifecycle.
        """
        if type(message) in self.query_handlers:
            handler = self.query_handlers[type(message)]
            self.logger.debug("handling query %s", type(message).__name__)
            return handler(message)  # type: ignore[arg-type]
        return self.handle(message)  # type: ignore[arg-type]

    def handle(self, message: Message) -> object | None:
        queue: deque[Message] = deque([message])
        processed = 0
        result: object | None = None

        # Prevent accidental infinite loops within the same request cascade:
        # (event_id, handler_qualname)
        processed_evt_handlers: set[tuple[str, str]] = set()

        while queue:
            processed += 1
            if processed > self.max_messages:
                raise RuntimeError("MessageBus exceeded max_messages (possible event loop).")

            msg = queue.popleft()

            if isinstance(msg, Command):
                result = self._run_with_serialization_retry(
                    self._make_command_invocation(msg, queue),
                    label=f"command:{type(msg).__name__}",
                )
                continue

            if isinstance(msg, DomainEvent):
                # No outer retry wrapper here: _handle_event retries each handler
                # individually (a fresh UoW per attempt) so that a transient
                # conflict on one handler does not re-run handlers that already
                # committed, and so the best_effort policy can be applied per
                # handler after its own retries are exhausted.
                self._handle_event(msg, queue, processed_evt_handlers=processed_evt_handlers)
                continue

            raise TypeError(f"{msg} is not a Command or DomainEvent")

        return result

    def _run_with_serialization_retry(
        self,
        fn: Callable[[], object | None],
        *,
        label: str,
    ) -> object | None:
        """Retry `fn` on transient Postgres concurrency errors with jittered backoff.

        Safe to retry because each invocation runs `fn` from scratch with a
        fresh UoW; SerializationFailure / DeadlockDetected mean the transaction
        rolled back and nothing was persisted on the failed attempt.

        After exhausting attempts, the last exception propagates unchanged.
        """
        attempts = max(1, self.serialization_retry_attempts)
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except _RETRYABLE_DB_ERRORS as exc:
                last_exc = exc
                if attempt >= attempts:
                    self.logger.warning(
                        "messagebus.retry_exhausted label=%s attempts=%d error=%s",
                        label,
                        attempt,
                        type(exc).__name__,
                    )
                    raise
                backoff_ceiling = min(
                    self.serialization_retry_max_seconds,
                    self.serialization_retry_min_seconds * (2 ** (attempt - 1)),
                )
                backoff = random.uniform(self.serialization_retry_min_seconds, backoff_ceiling)
                self.logger.info(
                    "messagebus.retrying label=%s attempt=%d/%d error=%s backoff_s=%.4f",
                    label,
                    attempt,
                    attempts,
                    type(exc).__name__,
                    backoff,
                )
                time.sleep(backoff)
        # Unreachable: loop either returns or raises.
        if last_exc is None:
            raise RuntimeError("messagebus retry loop exited without capturing an exception")
        raise last_exc

    # -------------------------
    # Commands
    # -------------------------

    def _make_command_invocation(
        self,
        cmd: Command,
        queue: deque[Message],
    ) -> Callable[[], object | None]:
        """Build a zero-arg thunk that runs one command under the retry net.

        Binds ``cmd`` eagerly as a parameter (rather than a drain-loop variable
        closure) so each thunk captures its own command — safe to re-run per
        retry attempt.
        """

        def _invoke() -> object | None:
            return self._handle_command(cmd, queue)

        return _invoke

    def _handle_command(self, cmd: Command, queue: deque[Message]) -> object | None:
        handler = self.command_handlers.get(type(cmd))
        if handler is None:
            raise KeyError(f"No command handler registered for {type(cmd).__name__}")

        uow_factory = self.command_uow_factories.get(type(cmd), self.uow_factory)
        uow = uow_factory()
        with uow:
            with bind_context(
                command_id=getattr(cmd, "command_id", None),
                correlation_id=_ctx_get(cmd, "correlation_id"),
                causation_id=getattr(cmd, "command_id", None),
                request_id=_ctx_get(cmd, "request_id"),
                idempotency_key=_ctx_get(cmd, "idempotency_key"),
                source=_ctx_get(cmd, "source"),
            ):
                self.logger.debug(
                    "handling command %s with %s",
                    type(cmd).__name__,
                    getattr(handler, "__name__", type(handler).__name__),
                )

                res = handler(cmd, uow)

                if self.forbid_handler_commit and uow.committed:
                    raise RuntimeError(
                        f"{type(cmd).__name__} handler must NOT call uow.commit(); "
                        "MessageBus owns commit() to ensure single event drain/outbox persistence.",
                    )

                # Commands are strict: always commit (even if no writes, it establishes a clear boundary).
                new_events = uow.commit()
                self._run_post_commit_hooks(uow, message_type=type(cmd).__name__)
                queue.extend(new_events)

                return res

    # -------------------------
    # Events
    # -------------------------

    def _handle_event(
        self,
        evt: DomainEvent,
        queue: deque[Message],
        *,
        processed_evt_handlers: set[tuple[str, str]],
    ) -> None:
        handlers = self._resolve_event_handlers(evt)

        for handler in handlers:
            handler_name = _event_handler_name(handler)
            handler_key = (str(evt.event_id), handler_name)

            if self.dedupe_event_handlers_within_call and handler_key in processed_evt_handlers:
                self.logger.warning(
                    "skipping duplicate in-process handling for event_id=%s handler=%s",
                    evt.event_id,
                    handler_name,
                )
                continue

            try:
                # Transient Postgres conflicts (40001/40P01) are ALWAYS retried
                # here with a fresh UoW per attempt. Retrying is safe (a conflict
                # rolls the transaction back, so nothing committed) and must be
                # independent of best_effort_events: that policy governs only what
                # happens to a *real* or retry-exhausted failure, not whether a
                # retryable conflict is retried at all. Conflating the two made
                # the default (best_effort_events=True) swallow retryable conflicts
                # before the retry net could re-run the handler -- a silent loss of
                # an in-process event reaction.
                self._run_with_serialization_retry(
                    self._make_event_handler_invocation(
                        evt,
                        handler,
                        queue,
                        handler_key=handler_key,
                        processed_evt_handlers=processed_evt_handlers,
                    ),
                    label=f"event:{type(evt).__name__}:{handler_name}",
                )
            except Exception:
                # Reached for non-retryable handler errors and for retryable
                # conflicts that exhausted the retry budget. best_effort_events
                # decides whether to drop (default) or surface the failure.
                self.logger.exception(
                    "Exception handling event %s (%s) in %s",
                    type(evt).__name__,
                    getattr(evt, "event_type", None),
                    handler_name,
                )
                if not self.best_effort_events:
                    raise

    def _invoke_event_handler(
        self,
        evt: DomainEvent,
        handler: EventHandler,
        queue: deque[Message],
        *,
        handler_key: tuple[str, str],
        processed_evt_handlers: set[tuple[str, str]],
    ) -> None:
        """Run a single event handler in its own UoW.

        Called once per retry attempt by `_run_with_serialization_retry`, so it
        must be safe to re-run from scratch: it builds a fresh UoW and only
        records the handler as processed (and enqueues cascaded events) after a
        successful commit.
        """
        with bind_context(
            event_id=evt.event_id,
            event_type=evt.event_type,
            schema_version=evt.schema_version,
            organization_id=evt.organization_id,
            aggregate_id=evt.aggregate_id,
            aggregate_type=evt.aggregate_type,
            aggregate_version=evt.aggregate_version,
            correlation_id=evt.ctx.correlation_id,
            causation_id=evt.ctx.causation_id or evt.event_id,
            request_id=evt.ctx.request_id,
            idempotency_key=evt.ctx.idempotency_key,
            source=evt.ctx.source,
            actor_type=evt.actor.type.value,
            actor_id=str(evt.actor.id) if evt.actor.id is not None else None,
        ):
            uow = self.uow_factory()
            with uow:
                self.logger.debug(
                    "handling event %s (%s) with %s",
                    type(evt).__name__,
                    getattr(evt, "event_type", None),
                    handler_key[1],
                )

                handler(evt, uow)

                if self.forbid_handler_commit and uow.committed:
                    raise RuntimeError(
                        f"{type(evt).__name__}:{handler_key[1]} must NOT call uow.commit(); "
                        "MessageBus owns commit() to ensure single event drain/outbox persistence.",
                    )

                # For events: only commit if this handler wrote something.
                # (No empty commits; keeps event reactions cheaper.)
                if uow.has_writes:
                    new_events = uow.commit()
                    self._run_post_commit_hooks(
                        uow,
                        message_type=f"{type(evt).__name__}:{handler_key[1]}",
                    )
                    queue.extend(new_events)

                processed_evt_handlers.add(handler_key)

    def _make_event_handler_invocation(
        self,
        evt: DomainEvent,
        handler: EventHandler,
        queue: deque[Message],
        *,
        handler_key: tuple[str, str],
        processed_evt_handlers: set[tuple[str, str]],
    ) -> Callable[[], None]:
        """Build a zero-arg thunk that runs one event handler under the retry net.

        Binds ``handler``/``handler_key`` eagerly as parameters (rather than a
        loop-variable closure) so each per-handler thunk captures its own values
        — safe to call once per retry attempt.
        """

        def _invoke() -> None:
            self._invoke_event_handler(
                evt,
                handler,
                queue,
                handler_key=handler_key,
                processed_evt_handlers=processed_evt_handlers,
            )

        return _invoke

    def _resolve_event_handlers(self, evt: DomainEvent) -> list[EventHandler]:
        evt_type = evt.event_type  # guaranteed by DomainEvent contract

        handlers = self.event_handlers_by_type.get(evt_type, [])
        if not handlers:
            # Zero handlers is valid: event may exist for outbox/integration/audit only.
            self.logger.debug(
                "no in-process handlers registered for event_type=%r (skipping)",
                evt_type,
            )
            return []

        # de-dupe handlers if misregistered twice
        unique: dict[str, EventHandler] = {}
        for h in handlers:
            unique[_event_handler_name(h)] = h
        return list(unique.values())

    def _run_post_commit_hooks(self, uow: AbstractUnitOfWork, *, message_type: str) -> None:
        try:
            uow.run_post_commit_hooks()
        except Exception:
            self.logger.exception("Exception running post-commit hooks for %s", message_type)
            if not self.best_effort_events:
                raise


def _event_handler_name(handler: EventHandler) -> str:
    """Return a stable label for either a function or a callable handler object.

    The label is a de-dupe key (both for "misregistered twice" collapsing and for
    the within-call `processed_evt_handlers` guard), so it MUST distinguish two
    genuinely different handlers. Bare lambdas defined side by side in one builder
    all share the qualname `<builder>.<locals>.<lambda>`: keying on that alone
    silently collapsed sibling registrations to the last one, so a real subscriber
    never ran (no error, no log, no write). Anonymous handlers therefore carry
    their object identity in the key. Identity is stable for the process lifetime
    because the bus registry holds a reference to every registered handler.
    """
    handler_type = type(handler)
    module = getattr(handler, "__module__", handler_type.__module__)
    qualname = getattr(handler, "__qualname__", handler_type.__qualname__)
    name = f"{module}.{qualname}"
    if qualname.rpartition(".")[2] == "<lambda>" or isinstance(handler, functools.partial):
        # `functools.partial` collides the same way: every partial reports the
        # qualname "partial" regardless of the function it wraps.
        return f"{name}#{id(handler):x}"
    return name
