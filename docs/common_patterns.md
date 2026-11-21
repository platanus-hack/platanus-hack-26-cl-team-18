# Common Module Artifacts

This document defines the **correct usage** of the shared kernel in
`app/modules/common`. The goal is to keep every bounded context (`screens`,
`audience`, `campaigns`, `auction`, `creative`, `delivery`) aligned and to make
AI agents implement changes that respect the architecture. Rules live in
[`rules.md`](rules.md); binding decisions live in [`docs/adr/`](adr/); what the
kernel contains is listed in
[`app/modules/common/README.md`](../app/modules/common/README.md). Vocabulary
comes from [`CONTEXT.md`](../CONTEXT.md).

Examples below use this project's domain. Names such as `Campaign`,
`Screen` or `AttentionCharge` illustrate the shape; check the bounded context
before assuming a class exists.

---

## 1. Domain Primitives (Aggregate, Entity, VO, Events, Errors)

### 1.1 Value Objects
File: `app/modules/common/domain/primitives/value_object.py`

Rules:
- Every VO is immutable (`@dataclass(frozen=True, slots=True)`).
- **Construction goes through `create(...)` only.**
- Each VO must implement `_enforce_types()` and call it in `create(...)`.
- VOs normalize input in `create(...)` and return a valid instance.
- Reuse the common VOs before writing a new one: `Quantity`, `Percentage`,
  `DateRange`, `UtcDateRange`, `Name`, `Url`, `Metadata`, `IdempotencyKey`,
  `DedupeKey` (`app/modules/common/domain/value_objects/`).
- For timestamp wrappers, use `UtcTimestamp`
  (`app/modules/common/domain/value_objects/utc_timestamp.py`) instead of
  repeating timezone checks across modules.
- Keep `UtcTimestamp` domain-strict: it should wrap only UTC-aware `datetime`
  values that were already normalized before entering the domain.
- For free-form audit fields, use `Metadata`
  (`app/modules/common/domain/value_objects/metadata.py`) to enforce JSON-only
  payloads, deterministic serialization, and size/depth guardrails.
- For money (bids, reserve prices, charges per attention-second), keep the
  domain canonical representation as major-unit `Decimal` (`Money.amount`) and
  enforce currency quantum in `Money.create(...)`. When a boundary speaks minor
  units (cents), use:
  - `Money.from_minor_units(currency=..., minor=...)`
  - `Money.to_minor_units()`
  This keeps minor-unit conversion at boundaries while preserving domain
  invariants in one place.
- Physical measurements with a unit (attention seconds, distance, dwell) get a
  VO, not a bare `float` passed around.
- **A VO owns its own (de)serialization.** When a VO is persisted (JSONB column,
  idempotency cache, event payload), put the canonical mapping on the VO itself:
  `to_plain()` for the persisted shape and a strict `from_plain(payload)` for
  rehydration. Don't hand-build the dict in each repository. Repositories that
  re-implement the mapping drift apart, and the drift is the bug: one reader
  defaults a missing key (fabricating data), another drops a required field
  (crashing on read). `from_plain` must be **strict**: a missing or malformed
  payload raises, it never defaults. The helpers in
  `app/modules/common/domain/value_objects/plain_payload.py`
  (`require_plain_object`, `require_plain_str`, `require_plain_uuid`, …) do the
  checking. Adapter concerns that differ per call site (psycopg `Json(...)`
  wrapping, `None` passthrough) stay at the call site; only the field mapping is
  centralized.

Pattern:
```python
@dataclass(frozen=True, slots=True)
class AttentionSeconds(ValueObject):
    value: Decimal

    @classmethod
    def create(cls, raw: Decimal | int | str) -> "AttentionSeconds":
        obj = cls(Decimal(str(raw)))
        obj._enforce_types()
        if obj.value < 0:
            raise ValueError("AttentionSeconds cannot be negative")
        return obj

    def _enforce_types(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("AttentionSeconds.value must be Decimal")
```

Serialization pattern (VO owns `to_plain` / strict `from_plain`):
```python
def to_plain(self) -> dict[str, object]:
    # the single source of truth for the persisted shape
    return {"segment_id": self.segment_id, "confidence": str(self.confidence)}


@classmethod
def from_plain(cls, payload: object) -> "SegmentTag":
    # strict: never fabricate/default missing keys — surface corruption
    data = require_plain_object(payload, field="segment_tag")
    require_plain_keys(data, {"segment_id", "confidence"}, field="segment_tag")
    return cls.create(
        segment_id=require_plain_str(data["segment_id"], field="segment_id"),
        confidence=Decimal(require_plain_str(data["confidence"], field="confidence")),
    )
```
```python
# repository — only adapter glue (None / Json) lives here, never the mapping:
"segments": Json([tag.to_plain() for tag in moment.segments])
segments = tuple(SegmentTag.from_plain(item) for item in (row["segments"] or []))
```

### 1.2 Entities and Aggregate Roots
Files:
- `app/modules/common/domain/primitives/entity.py`
- `app/modules/common/domain/primitives/aggregate_root.py`

Rules:
- Entities are identity-based and **never emit events**.
- Aggregates are the only event emitters.
- Aggregates must use `record_event()` and **never mutate `_events` directly**.
- `rehydrate(...)` must never include pending events; repositories must call
  `_assert_no_pending_events()`, and must call `_mark_persisted(version)` so the
  aggregate carries its optimistic-concurrency anchor.
- Intent methods copy themselves with `self._evolve(...)`, **never**
  `dataclasses.replace(self, ...)`. `replace` resets every `init=False` field
  (`version`, `_events`, `_persisted_version`), so chaining two intent methods
  inside one command silently drops the first one's events before the outbox
  ever sees them, and discards the OCC anchor. Enforced by
  `tests/unit/architecture/test_domain_pattern_guardrails.py`.

Pattern (immutable aggregate):
```python
new_version = self._bump_version()
updated = self._evolve(...)
updated._set_version(new_version)
updated.record_event(event)  # event.aggregate_version == new_version
return updated
```

### 1.3 Domain Events
File: `app/modules/common/domain/primitives/events.py`

Rules:
- Events are facts (past tense) and immutable: `AudienceMomentChanged`,
  `AuctionCleared`, `CreativeReady`.
- `DomainEvent.create_base(...)` builds the envelope.
- Required fields: `event_id`, `organization_id` (unless explicitly allowed missing),
  `aggregate_id`, `aggregate_type`, `aggregate_version`, `occurred_at`, `actor`, `ctx`.

### 1.4 Domain Errors
File: `app/modules/common/domain/primitives/errors.py`

Rules:
- Domain errors must be `DomainError(code, msg, params)`.
- Domain uses typed `DomainError` subclasses for invariants.
- **Do not raise generic exceptions** from domain logic.

---

## 2. Application Layer Artifacts

### 2.1 Command Base
File: `app/modules/common/application/command.py`

Rules:
- Commands express **intent**, not facts.
- Commands are immutable and include `command_id` (UUIDv7).
- Commands may include `occurred_at` and `ctx`.

### 2.2 Message Bus
File: `app/modules/common/application/message_bus.py`

Rules:
- The bus owns `uow.commit()` for commands and events.
- Command handlers **must not** call `uow.commit()` directly.
- Event handlers are best-effort by default; commit only if `uow.has_writes`.
- Event routing uses `event_type` (stable string), not class type.
- The bus is synchronous. From async FastAPI/WebSocket code, call it through
  `anyio.to_thread.run_sync(bus.handle, cmd)`.

Pattern:
```python
bus = MessageBus(uow_factory=..., command_handlers=..., event_handlers_by_type=...)
bus.handle(cmd)
```

### 2.3 Timestamp Parsing
File: `app/modules/common/application/parsers/timestamps.py`

Rules:
- Parse timestamp strings at the API/application boundary, not inside domain VOs.
- Use `parse_utc_datetime(...)` when a handler/DTO needs a normalized
  `datetime`.
- Use `parse_utc_timestamp(...)` when the boundary should return the strict
  domain VO directly.
- Accept valid ISO 8601 instants with explicit offsets (`Z`, `+00:00`,
  `-03:00`, etc.) and normalize them to UTC.
- Reject loose timestamp formats such as whitespace-padded or space-separated
  datetime strings.
- Replace repeated `datetime.fromisoformat(...)` + timezone handling with the
  shared parser so timestamp semantics stay consistent across modules.
- When a **command handler** normalizes the timestamp it stamped onto a command
  (`occurred_at` / `created_at`) and must surface a *bounded-context-specific*
  error on failure, use `normalize_command_timestamp(value, *, field_name,
  on_invalid)` rather than re-writing the `try/parse_utc_datetime/except → raise
  <BCError>(str(exc))` block in every handler. This is the **error-factory
  injection** pattern: the rule (parse + wrap) lives in one place, while each
  context keeps its own error type by passing its error class as `on_invalid`.
- The general transport parser rounds to milliseconds. For a persisted instant
  that must round-trip exactly (for example, one that is part of a hash), use
  `parse_persisted_utc_datetime`, which keeps the exact microsecond codec.

Pattern:
```python
from app.modules.common.application.parsers.timestamps import parse_utc_timestamp

measured_at = parse_utc_timestamp(payload["measured_at"], field_name="measured_at")
snapshot = AudienceSnapshot.create(measured_at=measured_at, received_at=clock.now(), ...)
```
```python
# command handler — one fact, per-context error via the injected factory:
from app.modules.common.application.parsers.timestamps import normalize_command_timestamp


def _normalize_occurred_at(occurred_at: datetime) -> datetime:
    return normalize_command_timestamp(
        occurred_at, field_name="occurred_at", on_invalid=PlaybackReportError
    )
```

### 2.4 Unit of Work (UoW)
File: `app/modules/common/application/uow.py`

Rules:
- UoW is the **transaction boundary**.
- UoW drains events exactly once via `collect_new_events()` using `pull_events()`.
- Repositories participating in event draining must maintain `.seen` with the
  aggregates loaded or saved in the current UoW so the UoW can drain events.
  Use `SeenAggregates` (`domain/primitives/seen_aggregates.py`), never a bare
  `set`.
- A command or worker that can record aggregate events must obtain its repository
  through `uow._get_repo("<stable key>", factory)`, not by directly constructing
  `RepositoryPostgres(uow)`. The UoW drains events only from cached repositories;
  a directly constructed repository can commit its database write while silently
  losing its aggregate events and therefore its transactional outbox row.
- `commit()` persists state + integration outbox entries **atomically**.

Usage:
```python
with uow:
    handler(cmd, uow)
    # do not call uow.commit() here if using MessageBus
```

#### Read-only UoW for reads (rules §5.11)
File: `app/modules/common/infrastructure/postgres/uow.py`

| Factory | Transaction | Use for |
|---|---|---|
| `make_uow_factory` | `SERIALIZABLE, READ WRITE` | commands, workers, anything that writes or reads-then-writes |
| `make_read_uow_factory` | `READ COMMITTED, READ ONLY` | GET handlers, lookup helpers, query services |

A GET route opens the read factory with the same tenancy arguments:

```python
@router.get("/screens/{screen_id}", response_model=ScreenResponse)
def get_screen(screen_id: UUID, ctx=Depends(...), db=Depends(get_db)):
    with make_read_uow_factory(db, organization_id=ctx.organization_id)() as uow:
        screen = ScreenRepositoryPostgres(uow).get(
            organization_id=ctx.organization_id, screen_id=ScreenId(screen_id)
        )
    ...
```

- A write inside it fails with SQLSTATE 25006, which is the point: a GET must
  not commit a side effect.
- `uow.commit()` is still allowed and commits an empty transaction.
- Unit tests that monkeypatch the factory must patch `make_read_uow_factory` on
  the routes module. A patch of `make_uow_factory` there is silently unused, and
  the test hits a real connection.
- Message buses and POST/PUT/PATCH/DELETE handlers never use the read factory:
  a bus's `uow_factory` is built from `make_uow_factory`.
- The hot path (moment → auction → creative) reads the in-memory campaign book,
  not Postgres. Postgres is the record.

### 2.5 Command/Event Handler Providers
File: `app/modules/<bc>/application/providers.py`

Rules:
- Exports factory functions (not static dicts) that return handler maps when called with concrete dependencies.
- `command_handlers(repo_factory, ...) -> dict[type[Command], CommandHandler]`
- `event_handlers() -> dict[str, list[EventHandler]]` keyed by `EVENT_TYPE` string.
- Pure: no I/O, no environment access, no import-time side effects.
- Called exclusively from `app/bootstrap/` (the composition root).

---

### 2.6 Typed Data Without Repeated Serialization

Apply [rules §6.3.1](rules.md#631-validation-boundaries-and-conversion-costs):
validate at each trust boundary, keep trusted computation typed, and serialize
when the representation actually needs to leave Python.

| Responsibility | Representation and conversion |
| --- | --- |
| External HTTP, WebSocket, device or AI input | Pydantic boundary schema; shared parsers normalize instants |
| Persisted/message input | Explicit schema or domain `from_plain` codec; validate on rehydration |
| Trusted internal result | Typed dataclass/projection; no dictionary or JSON round trip between helpers |
| Domain behavior | Aggregates/VOs and their factories; invariants remain mandatory |
| Public output | Explicit response model mapping and canonical serializers; exclude internal fields |

For a trusted projection, build the response directly. This retains output
validation without recursively copying the source into a temporary dictionary:

```python
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class CampaignDeliveryRow:
    campaign_id: str
    attention_seconds: Decimal
    plays: int


class CampaignDeliveryResponse(BaseModel):
    campaign_id: str
    attention_seconds: str
    plays: int


def delivery_response(row: CampaignDeliveryRow) -> CampaignDeliveryResponse:
    return CampaignDeliveryResponse(
        campaign_id=row.campaign_id,
        attention_seconds=str(row.attention_seconds),
        plays=row.plays,
    )
```

Do not replace this with `asdict(row)` or a model → JSON → model chain.
Frozen dataclasses do not make nested mutable containers immutable; select
immutable members where sharing is intended. Keep `Money` and typed ids wherever
those concepts apply.

For identical schemas, reuse a compatible model instance. For different schemas,
explicit mapping makes renames and field exclusion visible. Attribute-based
validation (`Response.model_validate(projection, from_attributes=True)`) is useful
for already-loaded nested projections, but can invoke properties and ignores
unknown source attributes. It does not replace `extra="forbid"` on incoming maps.
Keep response models and test their JSON output, not only the mapper's return type.

Compile a static `TypeAdapter(...)` at module scope. For raw JSON, consider
`Payload.model_validate_json(raw)`; when the payload is already a dictionary,
validate that dictionary instead of encoding it first.

Some conversion is an intentional protection. Persisted snapshots, idempotency
fingerprints and the screen↔backend WS messages keep their exact established
codecs; a faster serializer is not interchangeable by default.

## 3. Outbox Pattern (Integration Events)

### 3.1 Integration Event
File: `app/modules/common/application/integration_event.py`

Rules:
- Integration events are the **only** events that leave the service.
- Payload is JSON-safe and schema-versioned.
- Envelope carries trace IDs + actor metadata.

### 3.2 Domain → Integration mapping
File: `app/modules/common/infrastructure/outbox/event_mapper.py`

Rules:
- The mapper extracts the event payload only (no envelope duplication).
- Mapping must be deterministic and JSON-safe.

### 3.3 Outbox Repository
File: `app/modules/common/infrastructure/outbox/repository_write.py`

Rules:
- Outbox writes are **idempotent on `event_id`**.
- `add_all(...)` is the primary method used by UoW.
- Outbox inserts occur **inside the same transaction** as aggregate writes.
- The table is `outbox_event` (`db/migrations/0001_outbox.sql`). The kernel
  ships the writer only; write a dispatcher when a consumer needs one.

---

## 4. Idempotency Pattern

File: `app/modules/common/application/idempotency.py`

Rules:
- Writes that can be retried (device uploads, `playback.report`, API POSTs)
  carry an idempotency key or a natural dedupe key (`seq` per screen for
  `audience.snapshot`).
- The same key with a different body must fail: compute the request hash with
  `compute_request_hash(payload)`, or `idempotency_fingerprint_for(command)` for
  a command that declares `idempotency_semantic_payload()`.
- `IIdempotencyRepository.try_begin(key, request_hash)` returns
  `IdempotencyBeginResult` with an `IdempotencyStatus`:
  - `BEGIN_OK`: proceed
  - `IN_PROGRESS`: reject or retry
  - `COMPLETED`: return cached result
  - `CONFLICT`: same key, different request hash → `IdempotencyConflictError`
- The repository must be used inside the same UoW transaction as the write.
- The kernel defines the port, not a Postgres adapter. When the first flow needs
  one, write a single helper that branches on the status once, and have handlers
  pass only `run` / `restore` / `serialize` callbacks. Don't duplicate the
  `try_begin` branching in each handler.

---

## 5. Boundary Parsers Pattern

File: `app/modules/common/application/parsers/timestamps.py`

Rules:
- Boundary normalization for shared transport primitives must live in
  `app/modules/common/application/parsers`, not in route-local helpers.
- API and WS payloads accept flexible external input and normalize it before the
  application/domain layer consumes it.
- Timestamps must be parsed through `parse_utc_datetime(...)` /
  `parse_utc_timestamp(...)` so boundaries consistently accept explicit-offset
  ISO-8601 inputs and normalize to canonical UTC semantics.
- Currency codes go through `CurrencyCode.create(...)`.
- Shared parsers must fail with field-specific errors and should be reused by
  Pydantic validators instead of duplicating regex/format checks across modules.

Pattern:
```python
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.modules.common.application.parsers import parse_utc_datetime


class PlaybackReportPayload(BaseModel):
    displayed_at: datetime

    @field_validator("displayed_at", mode="before")
    @classmethod
    def validate_displayed_at(cls, value: object) -> datetime:
        return parse_utc_datetime(value, field_name="displayed_at")
```

---

## 6. Creation Facts (Uniqueness Checks)

A uniqueness rule needs a query, and the domain cannot query. So the application
layer gathers the fact and the aggregate enforces the invariant:

```python
# application handler: gather the fact through a repository port
name_is_free = not screen_repo.exists_by_name(
    organization_id=cmd.organization_id, venue_id=cmd.venue_id, name=cmd.name
)

# domain: receive the fact, enforce the invariant, raise a DomainError
screen = Screen.register(
    screen_id=ids.new(ScreenId),
    name=Name.create(cmd.name),
    name_is_unique=name_is_free,
    registered_at=clock.now(),
)
```

Rules:
- The check is organization-scoped (rules: multi-tenant).
- The fact is a plain boolean; the error is raised by the aggregate, not the
  handler.
- A database unique constraint backs the check; the fact gives a clean domain
  error in the common case, the constraint catches the race.

---

## 7. Correct End-to-End Flow

1. API validates payload + idempotency key, builds Command.
2. MessageBus receives the Command and builds a fresh UoW.
3. Handler loads aggregates via repositories, calls domain methods.
4. Aggregates record DomainEvents.
5. MessageBus calls `uow.commit()`.
6. UoW drains events and persists outbox entries atomically.
7. Dispatcher/worker publishes outbox IntegrationEvents.

---

## 8. Do / Don’t (AI Guardrails)

Do:
- Use `VO.create(...)` and call `_enforce_types()` inside it.
- Use `AggregateRoot.record_event(...)` for domain events.
- Let MessageBus own `uow.commit()`.
- Map DomainEvents to IntegrationEvents inside UoW commit.

Don’t:
- Call `datetime.now()` inside domain.
- Generate IDs in domain.
- Emit DomainEvents from entities/VOs.
- Publish IntegrationEvents directly from handlers (outbox only).

---

## 9. Correctness Patterns (Reference Shapes)

Canonical rules live in [rules.md](rules.md); this section shows the shape.
Adopt a pattern when it removes a concrete failure mode, not because it looks
sophisticated.

### 9.1 Optimistic Concurrency (rules.md §5.7)

Wire level: the client's version travels as `If-Match`, never as a body field.

```python
@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(
    campaign_id: UUID,
    expected_version: int = Depends(require_if_match_version),  # 428 if absent (write this dep with the first versioned route)
    ...
) -> CampaignResponse:
    ...  # a mismatch surfaces as a stale-version DomainError -> 412
```

Storage level: compare against the version actually loaded, not `version - 1`.
A single command may bump the version more than once before `save()`:

```python
def save(self, campaign: Campaign) -> None:
    params = self._to_params(campaign)
    params["expected_version"] = campaign.persisted_version
    rowcount = self._execute_rowcount(
        """
        UPDATE campaign
        SET ..., version = %(version)s
        WHERE id = %(id)s
          AND organization_id = %(organization_id)s
          AND version = %(expected_version)s
        """,
        params,
    )
    if rowcount == 0:
        raise CampaignStaleVersion(...)
    campaign._mark_persisted(campaign.version)
```

**Don't:** re-read the aggregate inside the handler to "refresh" `expected_version`.
That defeats the check and silently overwrites the other writer.

### 9.2 Specification vs Policy vs Aggregate Method (rules.md §4.4, §4.10)

| Question | Artifact |
|---|---|
| "Is this true of that object?" (yes/no, reusable) | `Specification` |
| "Given these facts, what is the decision/value?" | `Policy` |
| "Change this aggregate's state" | Aggregate intent method |

```python
# Specification — one predicate, reused by the auction and the console
class NotCompetitors(Specification[Bundle]):
    def is_satisfied_by(self, bundle: Bundle) -> bool:
        categories = [c.brand_category for c in bundle.campaigns]
        return len(categories) == len(set(categories))
```

Both `Specification` and `Policy` are **pure**: they take already-loaded values,
never a repository, UoW, session, client, gateway, or bus. Enforced by
[tests/unit/architecture/test_domain_pattern_guardrails.py](../tests/unit/architecture/test_domain_pattern_guardrails.py).

### 9.3 Snapshot vs Reference (rules.md §4.9)

```python
@dataclass(frozen=True)
class ClearingPriceSnapshot(ValueObject):
    """Captured when the auction clears. A later charge must never pick up today's bid."""

    campaign_id: CampaignId
    price_per_attention_second: Money
    cleared_at: UtcTimestamp
```

Snapshot: clearing prices, the moment's segments, the layout used: anything
whose later change must not rewrite a charge already made. Reference: the
campaign, the screen, the brand kit: things the reader should see the current
version of.

`*Snapshot` types must be `@dataclass(frozen=True)`; a mutable snapshot silently
rewrites history.

### 9.4 Prepared Request → Submit → Reconcile (rules.md §5.9)

For any irreversible external call (a paid image or video generation, a printed receipt):

```
1. persist intent + canonical fingerprint + external reference   (same tx as the decision)
2. submit to provider
3. on success  -> record provider response, advance state
   on timeout  -> DO NOT resubmit
4. reconcile: query the provider by fingerprint/reference
   - found     -> adopt it (record the provider id, advance state)
   - not found -> retry is now safe
```

**Don't:** `timeout -> submit again`. That is how duplicate charges are created.

Fingerprints must be canonicalized before hashing (stable key order, normalized
numbers/dates, trimmed strings) via `compute_request_hash` /
`idempotency_fingerprint_for`.

### 9.5 Independent State Axes (rules.md §4.8)

```python
# Right — axes advance independently
creative.render_status  # PENDING -> READY -> FAILED
creative.copy_source  # STOCK -> AI_UPGRADED
creative.playback_status  # QUEUED -> DISPLAYED -> REPORTED
```

```python
# Wrong — one enum encoding a combination
class CreativeStatus(Enum):
    READY_AI_COPY_DISPLAYED = "..."  # explodes; every new axis value is a migration
```

Derived facts are computed, not stored: `exhausted = spent >= budget`.
A persisted `EXHAUSTED` column goes stale the moment a charge is reversed.

### 9.6 Append-Only + Reversal (rules.md §4.9)

The delivery ledger (attention-second charges) and `outbox_event` are
append-only. Correct by appending a reversal (a negative charge that references
the original), never `UPDATE` the amount or `DELETE` the row. Register each new
append-only table in `APPEND_ONLY_TABLES` in
`tests/unit/architecture/test_domain_pattern_guardrails.py`.

A deliberate exception on an append-only table must carry an inline
`# append-only-exempt: <reason>`.
