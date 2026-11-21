# `common` — shared kernel

The generic, self-contained building blocks every bounded context uses.
Everything here imports only other files in this folder (plus
`app/config/logging_context.py`), and `tests/unit/common/**` covers it (no DB
needed).

`common/` is a **leaf**: it never imports a bounded context. Bounded contexts
import from it.

## What's here

| Need | Use | File |
|---|---|---|
| Aggregate root with version + event recording | `AggregateRoot` | `domain/primitives/aggregate_root.py` |
| Entity / value object base classes | `Entity`, `ValueObject` | `domain/primitives/entity.py`, `value_object.py` |
| Typed ids (stop mixing up a `DeviceId` and a `UserId`) | `TypedId` subclass | `domain/primitives/typed_id.py` |
| Domain events + request context | `DomainEvent`, `RequestContext` | `domain/primitives/events.py` |
| Domain errors with stable codes | `DomainError` and subclasses | `domain/primitives/errors.py` |
| Business rules as objects | `Policy`, `Specification` | `domain/primitives/policy.py`, `specification.py` |
| UUIDv7 generation (injected) | `IdGenerator`, `DomainIdGenerator` | `application/id_generator.py`, `domain/id_generator.py` |
| Time (injected, never `datetime.now()` in domain) | `Clock` port, `SystemClock` | `application/clock.py`, `infrastructure/clock/` |
| Commands + dispatch | `Command`, `MessageBus` | `application/command.py`, `message_bus.py` |
| Transactions | `AbstractUnitOfWork`, read-only guard | `application/uow.py` |
| Postgres transactions + outbox in same tx | `PostgresDB`, `make_uow_factory` | `infrastructure/postgres/` |
| Integration events / outbox writer | `IntegrationEvent`, `OutboxRepositoryPostgres` | `application/integration_event.py`, `infrastructure/outbox/` |
| Idempotency fingerprints | `compute_request_hash`, … | `application/idempotency.py` |
| Retry on serialization conflict | `serialization_retry` | `application/serialization_retry.py` |
| Timestamp parsing at boundaries | `parse_utc_datetime`, … | `application/parsers/timestamps.py` |
| Money (Decimal + currency, no floats) | `Money`, `CurrencyCode`, `RoundingPolicy` | `domain/value_objects/money.py` etc. |
| Other value objects | `Quantity`, `Percentage`, `UtcTimestamp`, `DateRange`, `UtcDateRange`, `Name`, `Url`, `Address`, `Timezone`, `Metadata`, `IdempotencyKey`, `DedupeKey`, `OrganizationId` | `domain/value_objects/` |
| Country / currency catalogs | enums + catalogs | `domain/country/`, `domain/currency/` |

The outbox table the Postgres UoW writes to is in `db/migrations/0001_outbox.sql`.

## Not here yet

An outbox *dispatcher*, an idempotency Postgres repository, queue-health
monitors, a worker supervisor and an HTTP API kernel. Write them when you
actually need one; follow `docs/common_patterns.md` for the shape.

## Minimal wiring

```python
from app.modules.common.application.message_bus import MessageBus
from app.modules.common.infrastructure.postgres.postgres import PostgresDB
from app.modules.common.infrastructure.postgres.uow import make_uow_factory

db = PostgresDB(dbname=..., user=..., password=..., host=...)
uow_factory = make_uow_factory(db, organization_id=org_id, user_id=user_id)
bus = MessageBus(
    uow_factory=uow_factory,
    command_handlers={RegisterDevice: handle_register_device},
    event_handlers_by_type={"devices.device.registered": [notify_operator]},
)
bus.handle(RegisterDevice(...))
```

Handler signatures: `CommandHandler = (cmd, uow) -> result`,
`EventHandler = (event, uow) -> None`. The bus opens the UoW, binds request
context, calls the handler, commits, writes integration events to the outbox
in the same transaction, then dispatches the domain events in-process.
Event handlers are keyed by the event's stable `event_type` string.
