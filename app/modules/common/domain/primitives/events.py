from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from app.modules.common.domain.value_objects.metadata import Metadata

# ---------------------------
# Actor
# ---------------------------


class ActorType(StrEnum):
    USER = "user"
    API_TOKEN = "api_token"
    SYSTEM = "system"
    AI = "ai"
    ML = "ml"


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: UUID | str | None = None
    name: str | None = None
    version: str | None = None
    metadata: Metadata = field(default_factory=Metadata.empty)

    @classmethod
    def create(
        cls,
        *,
        type: ActorType,
        id: UUID | str | None = None,
        name: str | None = None,
        version: str | None = None,
        metadata: Metadata | None = None,
    ) -> Actor:
        md = Metadata.empty() if metadata is None else metadata
        if not isinstance(md, Metadata):
            raise TypeError("actor.metadata must be Metadata or None")

        cls._validate_actor_type(type)
        cls._validate_actor_id(type=type, id=id)
        cls._validate_optional_text("name", name)
        cls._validate_optional_text("version", version)

        return cls(type=type, id=id, name=name, version=version, metadata=md)

    @staticmethod
    def _validate_actor_type(type: ActorType) -> None:
        if not isinstance(type, ActorType):
            raise TypeError("actor.type must be ActorType")

    @staticmethod
    def _validate_actor_id(*, type: ActorType, id: UUID | str | None) -> None:
        if type in {ActorType.USER, ActorType.SYSTEM, ActorType.API_TOKEN}:
            if id is None:
                raise ValueError(f"{type} actor requires an id")
            if isinstance(id, str) and not id.strip():
                raise ValueError("actor.id cannot be blank")
        elif isinstance(id, str) and not id.strip():
            raise ValueError("actor.id cannot be blank")

    @staticmethod
    def _validate_optional_text(field_name: str, value: str | None) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise TypeError(f"actor.{field_name} must be str or None")
        if not value.strip():
            raise ValueError(f"actor.{field_name} cannot be blank")


# ---------------------------
# Request Context
# ---------------------------


@dataclass(frozen=True, slots=True)
class RequestContext:
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    request_id: UUID | None = None
    idempotency_key: str | None = None
    source: str | None = None  # "api", "job", "import", etc.
    metadata: Metadata = field(default_factory=Metadata.empty)  # JSON-safe extension point

    @classmethod
    def create(
        cls,
        *,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        request_id: UUID | None = None,
        idempotency_key: str | None = None,
        source: str | None = None,
        metadata: Metadata | None = None,
    ) -> RequestContext:
        cls._validate_optional_uuid("correlation_id", correlation_id)
        cls._validate_optional_uuid("causation_id", causation_id)
        cls._validate_optional_uuid("request_id", request_id)
        cls._validate_optional_nonblank("idempotency_key", idempotency_key)
        cls._validate_optional_nonblank("source", source)

        md = Metadata.empty() if metadata is None else metadata
        if not isinstance(md, Metadata):
            raise TypeError("ctx.metadata must be Metadata or None")

        if correlation_id is None:  # default to request_id
            correlation_id = request_id

        return cls(
            correlation_id=correlation_id,
            causation_id=causation_id,
            request_id=request_id,
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            source=source.strip() if isinstance(source, str) else None,
            metadata=md,
        )

    @staticmethod
    def _validate_optional_uuid(field_name: str, value: UUID | None) -> None:
        if value is None:
            return
        if not isinstance(value, UUID):
            raise TypeError(f"ctx.{field_name} must be UUID or None")

    @staticmethod
    def _validate_optional_nonblank(field_name: str, value: str | None) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise TypeError(f"ctx.{field_name} must be str or None")
        if not value.strip():
            raise ValueError(f"ctx.{field_name} cannot be blank")


# ---------------------------
# Domain Event
# ---------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Domain Events — Usage Guide

    This module defines the base abstractions for Domain Events.

    A DomainEvent represents a **fact that already happened** in the domain
    (e.g. "CampaignActivated", "AuctionCleared"). Events are immutable,
    auditable, and emitted **only by Aggregate Roots**.

    They are the backbone of:
    - Audit logs (who/what/when/why)
    - Event-driven integrations
    - Read model updates (CQRS)
    - Reliable publishing via Transactional Outbox

    ----------------------------------------------------------------------
    Core rules
    ----------------------------------------------------------------------

    1) Domain Events are FACTS, not commands
    - They describe something that has already occurred.
    - They must never be used to request work.

    2) Only Aggregate Roots emit Domain Events
    - Entities and value objects do not create or publish events.
    - The aggregate root enforces invariants, mutates state, and records events.

    3) Domain Events are immutable
    - All fields are frozen.
    - No post-construction mutation is allowed.

    4) No clocks in the domain
    - The application layer supplies `occurred_at`.
    - The domain validates and normalizes timestamps to UTC.

    5) Stable contracts
    - Every event defines:
        EVENT_TYPE        (routing + storage key)
        AGGREGATE_TYPE    (owning aggregate)
        SCHEMA_VERSION    (schema evolution)

    ----------------------------------------------------------------------
    How to implement a Domain Event
    ----------------------------------------------------------------------

    Create a dataclass that subclasses DomainEvent.
    Define the contract constants and the payload fields.
    Expose a `create(...)` factory that delegates metadata creation
    to `DomainEvent.create_base(...)`.

    Example:
        @dataclass(frozen=True, slots=True, kw_only=True)
        class CampaignCreated(DomainEvent):
            EVENT_TYPE: ClassVar[str] = "campaigns.campaign.created"
            AGGREGATE_TYPE: ClassVar[str] = "Campaign"
            SCHEMA_VERSION: ClassVar[int] = 1

            # payload
            brand_id: UUID
            name: str
            category: str

            @classmethod
            def create(
                cls,
                *,
                event_id: UUID,
                organization_id: UUID,
                aggregate_id: UUID,
                aggregate_version: int,
                occurred_at: datetime,
                actor: Actor,
                ctx: RequestContext | None,
                brand_id: UUID,
                name: str,
                category: str,
            ) -> "CampaignCreated":
                return cls(
                    **cls.create_base(
                        event_id=event_id,
                        organization_id=organization_id,
                        aggregate_id=aggregate_id,
                        aggregate_version=aggregate_version,
                        occurred_at=occurred_at,
                        actor=actor,
                        ctx=ctx,
                    ),
                    brand_id=brand_id,
                    name=name,
                    category=category,
                )

    Notes:
    - Payload should contain only what is needed for audit, replay, or projections.
    - Avoid embedding entire aggregates or sensitive data.
    - Use IDs + key descriptive fields.

    ----------------------------------------------------------------------
    How Aggregate Roots use Domain Events
    ----------------------------------------------------------------------

    Aggregate Roots follow this pattern:

    1) Enforce invariants
    2) Create a new immutable state (or mutate internally if mutable)
    3) Bump aggregate version
    4) Create a DomainEvent using the new version
    5) Record the event on the aggregate
    6) Return the updated aggregate

    Example (immutable aggregate):

        def rename(
            self,
            *,
            new_name: CampaignName,
            event_id: UUID,
            occurred_at: datetime,
            actor: Actor,
            ctx: RequestContext | None,
        ) -> "Campaign":
            new_version = self._bump_version()
            # `_evolve`, never `replace(self, ...)`: replace resets the
            # init=False fields, dropping already-recorded events and the
            # optimistic-concurrency anchor.
            updated = self._evolve(name=new_name)
            updated._set_version(new_version)

            event = CampaignRenamed.create(
                event_id=event_id,
                organization_id=updated.organization_id,
                aggregate_id=updated.id,
                aggregate_version=updated.version,
                occurred_at=occurred_at,
                actor=actor,
                ctx=ctx,
                name=str(updated.name),
            )

            updated.record_event(event)
            return updated

    Invariants enforced by AggregateRoot:
    - event.aggregate_id == aggregate.id
    - event.aggregate_type == aggregate.aggregate_type
    - event.aggregate_version == aggregate.version

    ----------------------------------------------------------------------
    How events flow through the system
    ----------------------------------------------------------------------

    1) Command handler loads aggregate via repository
    2) Domain method returns updated aggregate with recorded events
    3) Unit of Work commits:
        - Persist aggregate state
        - Persist events to Transactional Outbox (same transaction)
    4) Outbox dispatcher publishes events to message bus
    5) Consumers:
        - Update read models (CQRS)
        - Trigger workflows / integrations
        - Write audit logs

    ----------------------------------------------------------------------
    Audit & observability
    ----------------------------------------------------------------------

    Each DomainEvent carries:
    - event_id             (global uniqueness)
    - aggregate_id         (what changed)
    - aggregate_version    (ordering per aggregate)
    - occurred_at          (domain time)
    - actor                (who/what caused it)
    - ctx                  (correlation / causation / idempotency)

    This makes DomainEvent suitable for:
    - Full audit trails
    - Time-travel debugging
    - Rebuilding read models
    - AI-based analysis of system behavior

    ----------------------------------------------------------------------
    Anti-patterns to avoid
    ----------------------------------------------------------------------

    - Emitting events from application services
    - Using DomainEvent as a command
    - Calling a clock inside the domain
    - Modifying event payloads after creation
    - Publishing events before the Unit of Work commits
    - Emitting events for read-only queries

    ----------------------------------------------------------------------
    Summary
    ----------------------------------------------------------------------

    Domain Events are:
    - immutable facts
    - owned by aggregates
    - versioned and ordered
    - audit-ready
    - infrastructure-agnostic

    They are the primary integration contract in an event-driven system.

    """

    EVENT_TYPE: ClassVar[str]
    AGGREGATE_TYPE: ClassVar[str]
    SCHEMA_VERSION: ClassVar[int] = 1

    event_id: UUID
    organization_id: UUID | None
    occurred_at: datetime  # tz-aware UTC (supplied by application)
    aggregate_id: UUID
    aggregate_version: int  # monotonic per-aggregate revision (used for ordering/idempotency)

    actor: Actor
    ctx: RequestContext

    @property
    def event_type(self) -> str:
        return type(self).EVENT_TYPE

    @property
    def aggregate_type(self) -> str:
        return type(self).AGGREGATE_TYPE

    @property
    def schema_version(self) -> int:
        return type(self).SCHEMA_VERSION

    @classmethod
    def create_base(
        cls,
        *,
        event_id: UUID,
        organization_id: UUID | None,
        aggregate_id: UUID,
        aggregate_version: int,
        actor: Actor,
        occurred_at: datetime,
        ctx: RequestContext | None = None,
        allow_missing_organization_id: bool = False,
    ) -> dict[str, Any]:
        cls._validate_uuid("event_id", event_id)
        cls._validate_uuid(
            "organization_id", organization_id, required=not allow_missing_organization_id
        )
        cls._validate_uuid("aggregate_id", aggregate_id)
        cls._validate_aggregate_version(aggregate_version)

        if not isinstance(actor, Actor):
            raise TypeError("actor must be Actor")

        if ctx is None:
            ctx = RequestContext.create()

        occurred_at_utc = cls._normalize_occurred_at(occurred_at=occurred_at)
        cls._validate_contract_constants()

        return dict(
            event_id=event_id,
            organization_id=organization_id,
            occurred_at=occurred_at_utc,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            actor=actor,
            ctx=ctx,
        )

    @classmethod
    def _validate_contract_constants(cls) -> None:
        if not hasattr(cls, "EVENT_TYPE") or not isinstance(cls.EVENT_TYPE, str):
            raise TypeError(f"{cls.__name__}.EVENT_TYPE must be defined as a str")
        if not cls.EVENT_TYPE.strip():
            raise ValueError(f"{cls.__name__}.EVENT_TYPE cannot be blank")

        if not hasattr(cls, "AGGREGATE_TYPE") or not isinstance(cls.AGGREGATE_TYPE, str):
            raise TypeError(f"{cls.__name__}.AGGREGATE_TYPE must be defined as a str")
        if not cls.AGGREGATE_TYPE.strip():
            raise ValueError(f"{cls.__name__}.AGGREGATE_TYPE cannot be blank")

        v = getattr(cls, "SCHEMA_VERSION", None)
        if not isinstance(v, int) or v < 1:
            raise ValueError(f"{cls.__name__}.SCHEMA_VERSION must be an int >= 1")

    @staticmethod
    def _validate_uuid(field_name: str, value: UUID | None, *, required: bool = True) -> None:
        if value is None:
            if required:
                raise ValueError(f"{field_name} is required")
            return
        if not isinstance(value, UUID):
            raise TypeError(f"{field_name} must be UUID")

    @staticmethod
    def _validate_aggregate_version(value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("aggregate_version must be int")
        if value < 1:
            raise ValueError("aggregate_version must be >= 1")

    @staticmethod
    def _normalize_occurred_at(*, occurred_at: datetime) -> datetime:
        if occurred_at is None:
            raise ValueError("occurred_at is required (application must supply it)")
        if not isinstance(occurred_at, datetime):
            raise TypeError("occurred_at must be datetime")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return occurred_at.astimezone(UTC)
