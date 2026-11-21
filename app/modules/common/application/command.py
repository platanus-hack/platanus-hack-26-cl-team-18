from __future__ import annotations

import abc
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from app.modules.common.domain.primitives.events import RequestContext  # if you reuse it


class Command(abc.ABC):
    """Base class for Application Commands (intent).

    A Command represents a **request to perform a business action**.
    It expresses *intent*, not a fact. Commands are handled by the
    application layer and may result in:
      - domain state changes
      - one or more DomainEvents
      - or a controlled failure (DomainError)

    ----------------------------------------------------------------------
    What a Command IS
    ----------------------------------------------------------------------

    - An explicit request to change system state
      (e.g. "RegisterScreen", "ActivateCampaign").
    - Created in the application or interface layer (API, job, import).
    - Dispatched through the MessageBus to exactly one handler.
    - Identified by a stable `command_id` for tracing and idempotency.

    ----------------------------------------------------------------------
    What a Command is NOT
    ----------------------------------------------------------------------

    - NOT a domain fact (those are DomainEvents).
    - NOT guaranteed to succeed.
    - NOT emitted by aggregates.
    - NOT persisted as part of domain state (except for idempotency tracking).

    ----------------------------------------------------------------------
    Required contract
    ----------------------------------------------------------------------

    All commands MUST expose:
    - `command_id`: UUID
        Used for:
          * idempotency
          * retries
          * correlation with DomainEvents
          * logging and audit

    Commands SHOULD expose:
    - `occurred_at`: tz-aware datetime supplied by the application layer
    - `ctx`: RequestContext (correlation / causation / idempotency metadata)
    - `COMMAND_TYPE` + `SCHEMA_VERSION` for stable routing and evolution

    ----------------------------------------------------------------------
    How to implement a Command
    ----------------------------------------------------------------------

    Commands are typically immutable dataclasses that inherit from this base:

        @dataclass(frozen=True, slots=True)
        class CreateCampaign(Command):
            COMMAND_TYPE = "campaigns.campaign.create"

            command_id: UUID
            occurred_at: datetime
            ctx: RequestContext

            organization_id: UUID
            brand_id: UUID
            name: str
            category: str

    The application layer is responsible for:
    - generating `command_id`
    - validating transport-level input
    - constructing Value Objects
    - dispatching the command via the MessageBus

    ----------------------------------------------------------------------
    Command handling flow
    ----------------------------------------------------------------------

    1) Interface layer creates a Command
    2) MessageBus routes it to its handler
    3) Handler:
        - loads aggregates via repositories
        - calls aggregate methods
        - receives updated aggregates with DomainEvents
    4) Unit of Work commits:
        - aggregate state
        - DomainEvents (Transactional Outbox)
    5) Events are published asynchronously

    ----------------------------------------------------------------------
    Relationship to Domain Events
    ----------------------------------------------------------------------

    - Commands express intent: "Please do X"
    - DomainEvents express facts: "X happened"

    One Command may result in:
    - zero DomainEvents (no-op, validation failure)
    - one DomainEvent
    - multiple DomainEvents

    ----------------------------------------------------------------------
    Error handling
    ----------------------------------------------------------------------

    - Business failures should raise DomainError.
    - Infrastructure failures should be handled outside the domain.
    - Command handlers should be idempotent with respect to `command_id`.

    ----------------------------------------------------------------------
    Design constraints
    ----------------------------------------------------------------------

    - Commands MUST NOT mutate domain state directly.
    - Commands MUST NOT contain domain logic.
    - Commands MUST NOT be published as events.
    - Commands MUST be safe to retry.

    ----------------------------------------------------------------------
    Summary
    ----------------------------------------------------------------------

    Commands are the **entry point of change**.
    They initiate work; aggregates decide what actually happens.
    """

    __slots__ = ()

    COMMAND_TYPE: ClassVar[str] = "command"
    SCHEMA_VERSION: ClassVar[int] = 1
    # The envelope, plus the optimistic-concurrency token.
    #
    # ``expected_version`` answers "which version did I read?"; the fingerprint
    # must answer "what am I asking you to do?". Hashing it makes a command's
    # identity depend on a value the caller provably cannot hold stable across a
    # retry: the first attempt bumps the row, so every replay of the *same*
    # intent under a frozen key re-reads a higher version, mismatches its own
    # stored fingerprint, and 409s forever (REL-155).
    #
    # Excluding it does not weaken anything. A genuinely different edit still
    # differs in its own fields and still conflicts, and the precondition itself
    # is untouched: the handler keeps asserting it under lock and still answers
    # 412 to a stale caller. Concurrency is enforced where it belongs, not by
    # smuggling the token into the request identity.
    IDEMPOTENCY_EXCLUDED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "command_id",
            "idempotency_key",
            "occurred_at",
            "correlation_id",
            "causation_id",
            "ctx",
            "expected_version",
        },
    )

    # Concrete commands are frozen dataclasses and provide this required field.
    # An abstract property conflicts with dataclass field generation under
    # static type checking, while this annotation preserves the contract.
    command_id: UUID

    # Optional but useful if you want parity with DomainEvent:
    @property
    def occurred_at(self) -> datetime | None:  # application supplies it
        return None

    @property
    def ctx(self) -> RequestContext | None:
        return None

    def idempotency_semantic_payload(self) -> dict[str, Any]:
        """Default semantic payload for idempotent write commands.

        Commands can override this when semantic normalization differs from the
        raw dataclass field set. The default excludes trace/idempotency envelope
        fields and fingerprints the remaining command payload.
        """
        if not is_dataclass(self):
            raise NotImplementedError(
                f"{type(self).__name__} must override idempotency_semantic_payload()",
            )

        payload: dict[str, Any] = {}
        for field in fields(self):
            if field.name in self.IDEMPOTENCY_EXCLUDED_FIELDS:
                continue
            payload[field.name] = getattr(self, field.name)
        return payload
