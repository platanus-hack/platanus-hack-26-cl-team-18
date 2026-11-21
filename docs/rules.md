# Architecture Rules

> **Doc status: LIVE GUIDE.** Kept current; it describes the system as it is.
> Binding decisions are in [`docs/adr/`](adr/). Never let a decision live only
> here. The shared kernel these rules lean on is listed in
> [`app/modules/common/README.md`](../app/modules/common/README.md); how to use
> it is in [`docs/common_patterns.md`](common_patterns.md).

These rules capture the patterns we enforce across bounded contexts (`screens`,
`audience`, `campaigns`, `auction`, `creative`, `delivery`) so agents,
developers and automated checks stay aligned. The codebase follows DDD with
clear layer separation, immutability where possible, and explicit tracing
metadata for every mutation. Vocabulary comes from [`CONTEXT.md`](../CONTEXT.md).

The pragmatism rule in `CLAUDE.md` applies: use a rule when it prevents a
concrete bug, and skip it when it is only ceremony.

## 1. Core Commitments
- Type-check everything (`pyright`/`mypy`) so that no `Any` leaks across layers.
- Document intent: every aggregate, command, DTO and workflow should explain
  _why_ it exists, not just _what_ it does.
- Preserve existing documentation context during edits: do not delete existing
  docstrings or meaningful comments unless asked to. Update them to reflect code
  changes instead.
- Never store money, bids, prices or amounts as floats. Use `Money` (`Decimal` +
  currency) from `common/domain/value_objects/money.py`.
- Keep auditable metadata close to the data it describes (timestamps, actors,
  correlation/causation IDs).
- Keep behavior centralized in the domain; avoid sprinkling bookkeeping logic
  through handlers, repositories, or API routes.

### 1.1 SOLID Principles
Apply these as concrete failure-mode prevention, not abstract OOP theory. Each
maps to a pattern required elsewhere in this doc.

- **Single Responsibility.** One aggregate/handler/policy owns one reason to
  change. A handler that both computes an attention-second charge and formats
  the console payload has two reasons to change and will eventually get one of
  them wrong under the other's edit. Split: domain computes, an adapter renders.
- **Open/Closed.** Add a new layout, pricing rule or provider by adding a new
  Policy/Specification/adapter, not by adding another branch to an existing
  `if/elif` chain (see §4.4, §4.10). If adding a case means editing unrelated
  existing cases, the abstraction is wrong.
- **Liskov Substitution.** Any adapter behind a port must be swappable without
  the caller changing behavior or exception types. An adapter that raises a
  provider's raw error instead of the port's declared error, or silently no-ops
  instead of failing, violates this.
- **Interface Segregation.** Ports expose only the methods a given adapter
  needs. Don't force every VLM or image provider adapter to implement methods it
  can't support; split the port instead of stubbing methods with
  `NotImplementedError` or a silent `pass`.
- **Dependency Inversion.** Domain and application code depend on ports
  (abstractions) defined in the module, never on a concrete provider SDK or DB
  session. Infrastructure implements the port. A domain import of a provider
  SDK is always a bug.

## 2. Identity & IDs
- Use UUIDv7 everywhere (primary keys, foreign keys, event IDs, command IDs,
  correlation IDs, idempotency rows). The domain does **not** own identity
  creation; the application layer owns it and uses the injected `IdGenerator`
  (`app/modules/common/application/id_generator.py`).
- Wrap aggregate ids in a `TypedId` subclass, one per aggregate (`ScreenId`,
  `CampaignId`, `MomentId`), so a screen id cannot be passed where a campaign id
  is expected.
- Repositories only rehydrate aggregates; they never invent IDs or emit events.
- Avoid DB-side UUID defaults; supply the ID before persistence so the domain
  stream stays consistent even when commands are retried.
- Uniqueness facts that need a DB lookup are checked by the application layer
  and passed to the domain as explicit facts, while the unique index stays the
  final guard. A pre-check improves the error message; the constraint decides.
- Never rely on integer/serial keys.

### 2.1 User-facing record labels
- Never display internal record identifiers in the console or screen copy,
  including shortened UUIDs: not in titles, detail views, notifications, button
  labels, fallback text or human-readable errors.
- Identify records by a brand or campaign name, a screen name/location, or the
  moment's time and segments. Use a descriptive record-type label when those
  details are unavailable.
- Keep identifiers needed for navigation and actions in link targets, request
  parameters or action payloads.

## 3. Time & Clock
- Every time reference flows through the `Clock` port
  (`app/modules/common/application/clock.py`). Application handlers fetch UTC
  instants or local dates and pass them into domain methods as arguments.
- Domain never depends on `Clock`. The application layer reads the clock and
  passes timestamps as explicit arguments.
- Temporal concepts stay explicit: business dates stay `date` values and
  instants stay timezone-aware datetimes.
- Instants (`occurred_at`, `measured_at`, `received_at`) live as `timestamptz`
  (UTC) and are never modeled as naive datetimes.
- **Sensor readings carry two instants.** An `audience.snapshot` carries the
  device's `measured_at` and the server's `received_at`. They are different
  facts: device clocks drift, and the latency trace needs both.
- Domain timestamp wrappers stay strict. `UtcTimestamp`
  (`app/modules/common/domain/value_objects/utc_timestamp.py`) accepts only an
  already-normalized UTC-aware `datetime` or another `UtcTimestamp`; it does not
  parse raw transport strings.
- A timestamp that represents a past server-side event must be strictly `< now`.
  Device-reported instants are validated against a bounded skew window instead,
  because the device clock is not ours.
- API/application boundaries own transport parsing and UTC normalization. Use
  the shared parser in `app/modules/common/application/parsers/timestamps.py`
  instead of ad-hoc `datetime.fromisoformat(...)`, `replace("Z", "+00:00")`, or
  bespoke offset checks.
- API instant inputs are ISO 8601 strings with an explicit offset:
  `YYYY-MM-DDTHH:MM:SS[.fraction](Z|+/-HH:MM)`. Reject naive datetimes,
  date-only values for instant fields, surrounding whitespace, space-separated
  date/time values and other loose variants.
- Instants are normalized to millisecond precision at the boundary (pad missing
  or short fractions, round longer ones). The canonical serialization is
  `YYYY-MM-DDTHH:MM:SS.sssZ`, in both directions.
- Application handlers convert normalized UTC `datetime` values into
  `UtcTimestamp` VOs before passing them into domain objects that require them.
- Exception: a wall-clock local concept whose meaning depends on civil time
  (a campaign's daypart "weekdays 08:00–10:00 in America/Santiago") is not
  collapsed into a UTC instant. Keep local time plus IANA timezone, and say why
  in a short comment next to the model. Audit fields, event timestamps and
  historical facts still follow the UTC rules.
- Tests use a fixed clock so wall-clock variance never leaks into assertions.
- No domain code ever reads the system clock directly.

## 4. Domain Layer Principles

- All invariants are enforced at aggregate-root and value-object creation
  (`.create()`), so invalid objects can never exist.
- Every Value Object implements `_enforce_types()` and calls it inside
  `create(...)`.
- All entities are created with a non-null ID, supplied by the application layer.

### 4.1 Value Objects
- Value objects inherit from `ValueObject`
  (`app/modules/common/domain/primitives/value_object.py`).
- Value objects are `@dataclass(frozen=True, slots=True)` and live in
  `app/modules/*/domain/value_objects`. Equality is by value; VOs are immutable
  and normalized via a `create(...)` factory (e.g. `CurrencyCode.create(...)`).
- Physical measurements with a unit are VOs, not bare floats: dwell time,
  distance band, attention-seconds, confidence.
- Use `UtcTimestamp` for domain instants instead of ad-hoc `datetime` handling.
- Do not make domain VOs parse transport strings. Parse JSON/HTTP/WS text in the
  API/application layer, then create the VO.
- Use `Metadata` (`app/modules/common/domain/value_objects/metadata.py`) for
  auditable JSON metadata, not a raw `dict[str, object]` or an unvalidated blob.
- Mapping-style VOs store canonical immutable mappings (e.g. `MappingProxyType`)
  and validate keys/values inside `create(...)` and `_enforce_types()` so
  direct construction cannot bypass invariants.
- Avoid giant blobs; prefer small, composable VOs (currency + amount, segment
  id + confidence, timezone).

### 4.2 Entities & Aggregates
- Aggregate = Entity + consistency boundary.
- `Aggregate.create()` enforces the invariants the aggregate needs to exist.
- Aggregates inherit from `AggregateRoot`
  (`app/modules/common/domain/primitives/aggregate_root.py`). That base gives you
  `record_event`, `pull_events`, `pending_events`, etc. Always emit domain events
  via those helpers instead of mutating `_events` directly.
- Provide two factories per aggregate:
  - `create(...)` for brand-new instances (may emit a `Created` event).
  - `rehydrate(...)` for loading existing rows (must not emit events).
- Domain state changes happen through **explicit intent methods**, one method
  per business transition, named after the business action, not the field.

  **Forbidden: generic setters leak implementation details and hide intent.**
  ```python
  def set_status(self, status: CampaignStatus) -> ...: ...
  def update_budget_status(self, status: BudgetStatus) -> ...: ...
  def set_online(self, online: bool) -> ...: ...
  ```

  **Required: explicit, domain-meaningful transitions.**
  ```python
  def submit_for_review(self, ...) -> ...: ...
  def approve(self, ...) -> ...: ...
  def activate(self, ...) -> ...: ...
  def pause(self, ...) -> ...: ...
  def resume(self, ...) -> ...: ...
  def record_spend(self, ...) -> ...: ...
  def exhaust_budget(self, ...) -> ...: ...
  def end(self, ...) -> ...: ...
  ```

  Each intent method validates preconditions, changes state and emits the
  corresponding domain event. A caller never combines two methods to complete
  one business transition.

- There are no public attribute setters. Aggregates are immutable at the
  dataclass level; changes happen exclusively inside intent methods, through
  the base class's evolve helper (CI rejects a bare `replace(self)`).
- Do not call `pull_events()` in handlers. The UoW drains events via
  `collect_new_events()`. `rehydrate()` implementations assert no pending events.
- The domain layer does not log. Logging happens one layer up.
- Invariants that need a DB check (e.g. a unique screen name) are enforced
  collaboratively: the application layer does the lookup and the domain receives
  the result as an explicit fact (`is_name_unique: bool`).

### 4.3 Domain Events
- Domain events inherit from `DomainEvent`
  (`app/modules/common/domain/primitives/events.py`). Each event captures
  `event_id`, `organization_id`, `aggregate_id`, `aggregate_type`, `occurred_at`
  (UTC), `actor`, and a `RequestContext` (optional `correlation_id`,
  `causation_id`, etc.).
- Events are facts (past-tense names: `AudienceMomentChanged`, `AuctionCleared`,
  `CreativeReady`) and immutable. They do not trigger side effects inside the
  domain; dispatch happens later via the bus.
- Keep event contracts stable: define `EVENT_TYPE`, `SCHEMA_VERSION`, and
  `AGGREGATE_TYPE` constants on the dataclass.

### 4.4 Policies & Domain Services
- Entities own facts, not policies. Policies are derived pure objects created
  at the domain boundary (by an aggregate or the application layer).
- Derive exactly one policy per decision (e.g. a reserve-price policy, a
  moment-debounce policy, a layout-selection policy). Avoid "God policies".
- Policies never fetch data or call infrastructure. They only combine
  already-loaded values or aggregates.
- Domain services are stateless helpers that operate solely on entities/VOs.
  The bundle auction is one: a pure function of (moment, eligible campaigns,
  layouts).

### 4.5 Domain Errors
- Errors inherit from `DomainError` (`app/modules/common/domain/primitives/errors.py`).
- Each invariant corresponds to one error class; raise it when the invariant
  fails. Application handlers translate those into 4xx responses.
- Avoid generic exceptions inside domain methods; fail fast with typed errors.
- Each error has a docstring explaining the invariant and a `msg` for logs.

### 4.6 Testing
- Keep the suite layered: unit tests protect domain logic, integration tests
  protect API + persistence wiring, and end-to-end tests protect only the
  critical journey (device → API → screen).
- Push most correctness checks down to the fastest stable layer. The auction,
  pricing and charging rules are tested at unit level, table-driven.
- Optimize for fast local feedback and low flakiness before adding full-stack
  coverage.
- Domain tests run without infrastructure: create aggregates/VOs, exercise
  behavior, and assert pending events, invariants and snapshots.
- Application tests use in-memory fakes for the UoW, repositories and `Clock`.
- Use a deterministic `IdGenerator` so event streams stay stable.
- Perception and creative adapters are tested against recorded fixtures, never
  live providers.

### 4.7 Entity vs Value Object Heuristics
- "Do we track it over time?" → Entity.
- "Is it defined fully by its fields?" → Value Object.
- If a concept can change without a deploy (e.g. the IAB segment taxonomy, brand
  categories), prefer a VO + reference data instead of an enum.

### 4.8 State Machines & Derived State
- **One enum per independent axis.** A campaign's `lifecycle_status`
  (draft/active/paused/ended), `review_status` (pending/approved/rejected) and
  `budget_status` (available/exhausted) advance independently. Never encode a
  combination as one value (`ACTIVE_APPROVED_EXHAUSTED`): combined enums
  multiply states, make transitions ambiguous, and force a migration every time
  one axis gains a value. Each axis's enum lives in the domain of the bounded
  context that owns the transitions.
- Every axis declares its legal transitions explicitly, and the intent methods
  of §4.2 are the only way to advance one. An illegal transition raises a
  `DomainError`; it never silently no-ops.
- **Derive state instead of persisting it** when it is a pure function of data
  you already store. A screen is offline when `now - last_heartbeat_at >
  timeout`; a persisted `OFFLINE` column goes stale the moment a heartbeat
  lands. Persist a status only when it records a *decision* (an operator paused
  the campaign, a reviewer approved the creative), not when it restates other
  columns.
- If a derived value is expensive, cache it in a read model (§5.10) clearly
  labelled as a projection, never as a second source of truth.

### 4.9 Snapshots, Temporal Versioning & Append-Only History
- **Snapshot what must not drift, reference what must stay current.** The
  cleared price, the winning bundle, the layout and the creative version are
  copied onto the auction decision when it clears. Re-reading a decision from
  last week must not pick up today's bids or reserve price.
  Types named `*Snapshot` are `@dataclass(frozen=True)`.
  *CI: `tests/unit/architecture/test_domain_pattern_guardrails.py`.*
- **Temporal versioning over in-place edits.** Rules that change over time
  (reserve prices, rate cards, layout catalogs) are stored as effective-dated
  rows with `effective_from` / `effective_to`, and selected by the
  `evaluated_as_of` instant of the computation, never by "current row".
  Updating a rate is an insert, not an `UPDATE`.
- **Append-only history.** Charges and audit history are corrected by appending
  a reversal (a compensating ledger entry), never by editing or deleting the
  original row. The attention-second ledger, the outbox and audit logs are
  append-only. Register each such table in `APPEND_ONLY_TABLES` (and immutable
  fact tables in `IMMUTABLE_ROW_TABLES`); a deliberate exception carries an
  inline `# append-only-exempt: <reason>`.
  *CI: `tests/unit/architecture/test_domain_pattern_guardrails.py`.*
- Draft rows (e.g. a campaign still in draft) are not history and may be
  rewritten; once a fact is recorded, §4.2's transition rules apply.

### 4.10 Specifications & Decision Tables
- A **Specification** (`app/modules/common/domain/primitives/specification.py`)
  answers a yes/no question about one object and nothing else: "are these two
  brands competitors?", "is this category blocked when `minor_likely`?". Use it
  when the same predicate is needed in more than one place (an auction filter, a
  guard, a console explanation) and must not drift between them.
- Specifications and Policies are **pure**: they take already-loaded values,
  never a repository, UoW, session, client, gateway or bus.
  *CI: `tests/unit/architecture/test_domain_pattern_guardrails.py`.*
- If the logic changes state or produces a value, it is an aggregate method or
  a Policy (§4.4), not a Specification.
- **Decision tables.** When an outcome depends on three or more independent
  inputs (brand category × `minor_likely` × daypart × venue type), express it as
  an explicit data table with one row per combination, plus a total-coverage
  test. Nested `if` chains hide the missing combination; a table makes the hole
  visible.

### 4.11 Result Types vs Exceptions
- Raise a `DomainError` for **rule violations**: states the caller should never
  have reached (§4.5).
- Return an explicit result for **expected outcomes** the caller must branch
  on: "no fill", "below reserve", "moment unchanged", "provider returned no
  tags". Expected outcomes carried as exceptions get swallowed by a broad
  `except` and turn into silent no-ops, or into a blank screen.
- Rule of thumb: if the caller has a sensible next action, it is a return
  value; if the caller can only abort, it is an error.

### 4.12 Functional Core, Imperative Shell
- The auction, VCG pricing, coverage computation, charge computation and
  rounding are **pure functions over inputs**: no IO, no clock, no randomness,
  no repository. The application layer loads the facts, calls the core, and
  persists the result.
- Practical test: every price and charge is reproducible from its inputs alone.
  If reproducing a number requires a database, the boundary is in the wrong
  place.
  *CI: domain purity checks in `tests/unit/architecture/test_layer_isolation.py`.*

## 5. Application Layer Contracts

### 5.1 Commands & Message Bus
- Commands inherit from `Command` (`app/modules/common/application/command.py`)
  and represent intent. They carry `command_id`, `occurred_at`, and the business
  payload.
- Each command is handled by exactly one handler. Events may have zero or more
  handlers.
- The `MessageBus` (`app/modules/common/application/message_bus.py`) binds
  logging context, constructs a fresh UoW, and dispatches commands/events.
- The `MessageBus` owns `uow.commit()`; handlers do **not** call `commit()`.
- UoW commit drains events and persists outbox entries; handlers only change
  aggregates.
- The kernel is synchronous. From async code (FastAPI, WebSocket handlers) call
  `bus.handle()` via `anyio.to_thread.run_sync`.
- Promote repeated guard logic (existence, active state, scope) into a shared
  application validator instead of copying it per handler.

### 5.2 Bootstrap & Composition Root
- The composition root lives in `app/bootstrap` and is the **only** place where
  concrete dependencies are wired (DB, clocks, adapters, repos).
- Bounded contexts export **pure provider functions** from
  `app/modules/<bc>/application/providers.py` (no I/O, no env access). These are
  factory functions that return handler maps when called with concrete
  dependencies, not static dict constants.
- The composition root calls those provider functions with wired dependencies
  and builds the `MessageBus`, passing a `uow_factory` that produces a **fresh**
  UoW per handler.
- API routes do **not** construct a `MessageBus`. They receive a prebuilt one.
- Bootstrap ensures idempotency and outbox repositories live inside the UoW
  scope, and that clocks and ID generators are injected, never created ad hoc.

#### 5.2.1 Dependencies Are Injected, Never Defaulted Away

A wiring seam declared as `dep: SomePort | None = None` is **invisible to mypy**:
a composition-root call that forgets `dep` type-checks cleanly and fails only at
runtime, in one of two silent shapes.

- **Silent degradation.** The handler branches on `dep is None` and takes a
  fallback path. Nothing raises; the feature quietly does less.
- **Silent non-registration.** `if dep_factory is not None: handlers[Cmd] = ...`
  leaves the command with no handler. Boot succeeds; the endpoint fails the
  first time someone reaches it, on stage.

Rules:

- Wiring dependencies (`*_factory`, `*_repo`, `*_service`, `*_port`, `clock`, …)
  are **required keyword-only parameters**, with no `None` default. Mypy then
  proves every call site injects them.
- Genuine optionality is an explicit **Null Object** (e.g. a
  `StockCopyWriter` when no LLM is configured), chosen **once at the
  composition root**.
- Command handlers are registered **unconditionally**. A wiring gap surfaces as
  a startup failure, not a 500 on first use.
- A tuning knob rather than a collaborator (`max_retries`, `page_size`) may keep
  a `None` default; mark it `# optional-dep-exempt: <reason>`.

### 5.3 Unit of Work & Repositories
- UoW implementations live in `app/modules/common/application/uow.py` (port) and
  `app/modules/common/infrastructure/postgres/uow.py` (Postgres). They provide
  transaction boundaries and repository factories.
- Repository interfaces (usually `typing.Protocol`) live in the domain. Concrete
  implementations live in infrastructure and are instantiated per UoW.
- Repositories are lightweight; no DB calls in `__init__`.
- Repositories hydrate aggregates via `rehydrate(...)` and expose intent-based
  operations (`add`, `save`, `get_by_id`). They do not embed business rules.
- Any repository path that recreates a domain object from rows uses that type's
  `rehydrate(...)` factory, never direct dataclass construction.
- Repositories track loaded aggregates in a `SeenAggregates`
  (`app/modules/common/domain/primitives/seen_aggregates.py`), never a `set`:
  aggregates compare equal by `(type, id)`, so a `set` silently drops the evolved
  instance that holds the pending events, and the state commits without its
  event.
- Only repositories obtained through the UoW (`uow._get_repo(...)` /
  `uow.register_repository(...)`) are drained. A repository constructed
  directly commits state without its event.
- All writes happen inside a transaction controlled by the bus/UoW.

### 5.4 Idempotency & Outbox
- Retryable writes (device uploads, API POSTs, webhooks) carry an idempotency
  key or a natural dedupe key. The port is `IIdempotencyRepository`
  (`app/modules/common/application/idempotency.py`); record it inside the same
  transaction as the write. Partition keys by `organization_id` when present.
  Same key and same hash returns the stored result; same key with a different
  payload raises `IdempotencyConflictError` (HTTP 409).
- Request hashes come from **stable command intent only**: scope, target ids and
  the client-supplied business payload, via `idempotency_fingerprint_for` /
  `compute_request_hash`. Never include server-generated execution metadata
  (`occurred_at`, `command_id`, correlation ids): a retry would hash
  differently and run twice. A timestamp the client supplies as business
  intent (`measured_at`) is part of the hash, in canonical form.
- Sensor input is event-idempotent, not command-idempotent: an
  `audience.snapshot` is deduped on `(screen_id, seq)` and a stale or duplicate
  `seq` is dropped, not replayed.
- Every domain event that must leave the process is mapped to an integration
  event and stored in the outbox (UoW commit + outbox repository) in the **same
  transaction** as the state change. A dispatcher publishes later with
  retries/backoff.
- Integration events (not raw domain events) are what gets published. Keep
  payloads stable and versioned, with `event_id`, `aggregate_id`,
  `aggregate_type` and tracing ids.
- **All consumers are idempotent.** Every delivery channel (in-process bus,
  outbox dispatcher, WebSocket reconnects, webhook redelivery) is at-least-once.
  A subscriber that runs twice on the same message produces the same end state
  as running once: no duplicate charges, no duplicate outbound calls, no
  duplicate events. In order of preference:
  1. Dedupe on the inbound `event_id` / natural key via a unique constraint or
     an inbox table checked in the same transaction as the side effect.
  2. Use the idempotency repository keyed on the message's stable business id
     when the handler issues a command.
  3. Make the side effect intrinsically idempotent (upsert by natural key,
     `INSERT ... ON CONFLICT DO NOTHING`, set-state-to-X instead of
     increment-by-one).
  Handlers that call external systems guard the call with a persisted record
  committed before ack. Never trust a broker's "exactly once" and never use
  in-memory dedupe for anything that bills.

### 5.5 Logging & Observability
- Application handlers log use-case boundaries and handle exceptions.
- Infrastructure logs system-level failures (failed queries, external APIs)
  but avoids business rules.
- Never log camera frames, audio, face crops, embeddings, secrets or tokens.
- Enrich logs with `correlation_id`, `command_id`, `event_id`, `screen_id`,
  `organization_id` and idempotency context.
- Every loop decision carries its latency trace (`measured_at`, `received_at`,
  `cleared_at`, `ready_at`, `displayed_at`). A new step in the loop adds its
  timestamp.

### 5.6 Clock & Time Management
- Handlers request UTC instants or local dates from `Clock` and never call
  `datetime.now()`. Domain methods receive the exact timestamps/dates they need.
- Local business dates (dayparts, daily budgets) are derived in the screen's or
  campaign's IANA timezone, stored on the aggregate, never `settings.timezone`
  or a hardcoded `"UTC"`.
- Conversion to UTC happens before persistence or event creation.
- Name the instant you mean. These are different facts and never share a column
  or a parameter: `occurred_at` (when the business fact happened),
  `measured_at` (when the device observed it), `received_at` (when the server
  got it), `recorded_at` (when we stored it), `effective_at` (when it starts
  applying), `evaluated_as_of` (the instant a computation was anchored to).
- IDs are injected the same way time is: the application layer generates UUIDv7
  and passes it in. Domain code never calls `uuid4()`.
  *CI: `tests/unit/architecture/test_layer_isolation.py::test_domain_layer_never_generates_ids`.*

### 5.7 Optimistic Concurrency Control
Any aggregate that can be modified by two flows at once (a campaign edited in
the console while delivery records spend against it, a screen's configuration)
carries a `version: int`. OCC applies at two distinct levels; do not conflate
them.

**Wire level (client staleness).** The client's last-known version travels as
the standard `If-Match` header, and read/write responses return it as `ETag`.
Missing header → **428**; version mismatch → **412** via the aggregate's own
stale-version error (e.g. `CampaignStaleVersion` with `http_status_hint=412`).
Don't invent a per-route body field for this.

**Storage level (lost updates).** Repository `save(...)` writes
`... WHERE id = :id AND organization_id = :org AND version = :expected_version`
and bumps `version` in the same statement. `expected_version` is the aggregate's
`persisted_version`, the version actually loaded from the DB, **not**
`version - 1`: one command may bump the version more than once before the save.
Zero rows updated means someone else won the race; raise a concurrency error.
Never fall back to a blind overwrite, and never silently ignore it.

Prefer optimistic locking over `SELECT ... FOR UPDATE` for user-driven edits;
reserve row locks for worker-side claim loops (§5.10).

### 5.8 Process Managers & Sagas
- A workflow that spans several aggregates or external systems (moment → auction
  → compose → play → measure → charge) is coordinated by events, each step
  reacting to the previous step's event. When a step needs its own retry and
  timeout state, give it an explicit **process manager** with persisted state,
  not a chain of handlers each doing one more thing.
- The process manager owns the current step, the retry/backoff state, the
  timeout and the terminal outcome. Every step is individually replayable.
- One transaction modifies **one aggregate**. Cross-aggregate consistency is
  reached by emitting an event and reacting to it, not by saving two roots in
  the same commit. When a step fails midway, compensate explicitly (release the
  slot, fall back to the house ad); never leave the workflow half-done.

### 5.9 Prepared Requests & Reconciliation Over Blind Retry
- Before calling an external system that does something irreversible or costly
  (a paid video or image generation job, an advertiser charge), persist the **prepared request** (its
  payload, its canonical fingerprint and its external reference) in the same
  transaction that authorized it. Then submit. A crash between the two leaves a
  recoverable record, not a mystery.
- On timeout or ambiguous failure, the sequence is
  **prepare → submit → timeout → reconcile by fingerprint/reference → retry only
  if reconciliation proves nothing happened.** Never re-submit immediately:
  a duplicate advertiser charge or a double-spent generation budget cannot be
  undone.
- Canonicalize before fingerprinting (stable key order, normalized numbers and
  dates, trimmed strings) so the same intent always yields the same hash. Use
  the shared helpers in `app/modules/common/application/idempotency.py`.

### 5.10 Read Models & Materialized Work Queues
- The console (coverage, counterfactual, exclusions, latency) reads from
  **purpose-built read models**, not by joining aggregate tables at request time.
  A read model is a projection: it can be rebuilt from the write side at any
  time, and it is never the source of truth for a decision.
- Projections live under `application/projections` and are updated from domain
  events. Regenerate rather than patch incrementally when the input set changes.
- The hot path reads an **in-memory campaign book**, not Postgres. Postgres is
  the record; the book is a projection refreshed from campaign events.
- Work that must be picked up exactly once (dispatch, retries, warm-path jobs)
  is materialized as **queue rows with an explicit status**, claimed with
  `FOR UPDATE SKIP LOCKED`, a lease and a DB-side attempt counter. Don't derive
  a work queue from an ad-hoc query over business tables: it can't be leased,
  retried or observed.

### 5.11 Reads Open Read-Only Transactions
- A code path that only reads opens its unit of work with
  `make_read_uow_factory` (`READ COMMITTED, READ ONLY`), not `make_uow_factory`
  (both in `app/modules/common/infrastructure/postgres/uow.py`). That covers GET
  handlers, helpers a route uses only for a lookup, and query services.
- Why: `make_uow_factory` defaults to `SERIALIZABLE, READ WRITE`. Under SSI every
  read takes predicate locks, so a polled console can make a concurrent writer
  (delivery charging spend) fail with a serialization error (SQLSTATE 40001),
  and it costs more per request. A read-only transaction takes no such locks,
  and an accidental write fails loudly (SQLSTATE 25006) instead of committing a
  side effect behind a GET.
- `uow.commit()` stays valid on a read-only UoW: it commits an empty transaction.
- A read that decides a write (read-then-write in one handler) is not a read. It
  stays in the command's writable transaction, where SERIALIZABLE or the
  aggregate's `version` protects it (§5.7).
- The converse holds too: write paths never open `make_read_uow_factory`. A
  command wired to a read-only transaction fails with SQLSTATE 25006 on its
  first write.

## 6. API Layer Rules
- Routes are thin: validate the DTO → build a named-intent command →
  `bus.handle()`. No business logic in routes.
- Keep one router per bounded context, split by trust boundary:
  - Public APIs under `/public/<bc>` (unauthenticated or end-user flows).
  - Internal APIs under `/internal/<bc>` (console/admin operations).
- Enforce security policy at router include level (a dependency on
  `include_router`) instead of repeating auth checks per handler.
- Keep public and internal route modules in separate files
  (`api/http/public/...`, `api/http/internal/...`).
- Business transitions are **named intents**, not generic field updates. Prefer
  `POST /campaigns/{id}:pause`, `:resume`, `:approve` over
  `PATCH /campaigns/{id}` with a `status` field: a named route maps one-to-one
  to a command, authorizes one action, and can't smuggle an unrelated change.
  Reserve `PATCH` for genuinely editable attributes (name, metadata, draft
  fields).
- Commands change state and return the minimum needed to continue; queries
  never change state and never emit events. A route that reads does not go
  through the command bus.
- Resolve auth/tenant context once in a shared dependency and store it in
  `request.state`. Route handlers don't read auth headers directly.
- Mutating endpoints that can be retried require an `Idempotency-Key` (§5.4).
  Explicit exceptions are listed in one place with a reason.
- DTOs use Pydantic for structural validation only. No business logic or
  persistence details leak into DTOs.
- Never return internal critical ids (auth user, organization member) in an API
  response.

### 6.1 API Change Management & Versioning
Treat contract changes as change management, not versioning. Reach for a new
version only when old and new semantics cannot safely coexist; otherwise evolve
the contract in place.

- **Default to additive, optional changes.** Anything you add to a request or
  response is optional. Anything previously optional stays optional.
- **Add, do not replace.** When a field needs richer semantics, add a new field
  and deprecate the old one. Don't change an existing field's shape, type or
  units in place.
- **Behavior is part of the contract.** Once an operation ships, its server-side
  behavior (idempotency guarantees, side effects, partial vs full replace) is
  locked in. A behavior change needs a new operation or an opt-in flag.
- **Prefer a new operation over a new version.** When a workflow diverges, add a
  sibling operation rather than piling flags onto an endpoint or cloning the API
  under `/v2`.
- **Do not tighten validation on shipped endpoints** without a transition window.
- **Servers may add fields; clients are tolerant readers.** Contract tests
  ignore unknown response properties.
- **Version deliberately, not reflexively**, and pair a new version with a real
  deprecation of the old one.

### 6.2 Screen ↔ Backend Message Contract
- The WebSocket message types live in **one** TypeScript module (`web/`) and
  **one** Pydantic module. Change both in the same commit; a message that exists
  on one side only is a bug.
- Messages are versioned and tolerant: a receiver ignores unknown fields and
  rejects unknown message types loudly.
- Every device → server message that reports a measurement carries `seq` and
  `measured_at`; the server stamps `received_at` on arrival (§3, §5.4).
- Stale frames are dropped, never queued. A slow consumer gets the latest
  moment, not a backlog.
- The screen always has a house ad or the last good creative ready. No server
  message, missing message or error leaves it blank.

### 6.3 Backend Performance

Performance changes preserve correctness, tenant isolation, response contracts
and reliable delivery. Measure the affected operation before and after with the
same inputs. Microbenchmarks establish local costs; p50/p95 claims need
representative load. The loop's budget is in `docs/architecture.md`.

- **Hot path never awaits a network model call.** VLM tags, AI copy and hero
  images are warm-path work that upgrades the moment or the creative in place.
- **Find the bottleneck first.** Inspect query counts/plans, N+1 reads,
  unbounded result sets, pool waits, external-call latency and serialization
  before tuning the framework. Keep pagination bounded and batch related reads;
  never remove tenant predicates for speed.
- **Keep blocking work off the event loop.** Use `async def` for awaitable I/O.
  Synchronous DB/kernel calls go through an explicit, bounded thread offload
  (`anyio.to_thread.run_sync`). Calling an ordinary helper from `async def` does
  not offload it. Keep transaction ownership inside the offloaded operation.
- **Budget concurrency together.** The AnyIO thread limiter is shared by sync
  routes, dependencies and offloads. Tune it only after measuring saturation,
  together with DB pools, provider limits, CPU and memory. Don't add a second
  executor to bypass backpressure.
- **Reuse resources, not request authority.** Initialize clients/pools in the
  app lifespan and close them at shutdown. Never globally cache a request,
  UoW/session, actor or mutable tenant state. Data caches need tenant-aware
  keys, bounds and explicit invalidation.
- **Compile static validators once.** Reuse `TypeAdapter` instances instead of
  constructing schemas per call. Use dataclasses/VOs for trusted internal
  computation; keep Pydantic at untrusted boundaries, including persisted
  payloads and AI output.
- **Bound large outputs at the source.** Paginate interactive lists; stream
  exports from bounded batches.
- **Prefer pure ASGI for new cross-cutting middleware** over
  `BaseHTTPMiddleware`, which wraps responses and breaks streaming/WebSockets.
- **Make deferred work durable when required.** Charging and anything that must
  survive a restart uses the outbox and idempotent workers (§5.4).
  `BackgroundTasks` is only for disposable process-local work.

These are review responsibilities (§7.1), not automatic enforcement. References:
[FastAPI async execution](https://fastapi.tiangolo.com/async/),
[Starlette thread capacity](https://www.starlette.dev/threadpool/),
[Starlette middleware](https://www.starlette.dev/middleware/),
[Pydantic performance](https://docs.pydantic.dev/latest/concepts/performance/).

#### 6.3.1 Validation Boundaries and Conversion Costs

- **Choose representations by responsibility.** Pydantic for runtime boundary
  schemas (HTTP, WebSocket, provider/AI responses, persisted documents, message
  payloads). Typed dataclasses for trusted internal projections and computation.
  Domain factories for business invariants.
- **A new trust boundary may need new validation.** Static typing cannot
  validate network, device or model output.
- **Avoid conversion chains inside a boundary.** Pass an already-typed value or
  map public fields explicitly instead of `model_dump()` → dict →
  `model_validate()` per row.
- **Match the codec to the input.** For raw JSON entering a Pydantic boundary,
  use `model_validate_json()` instead of `json.loads()` first. Never change
  canonical hash/idempotency bytes as a serialization optimization.
- **Keep schemas static and specific.** Compile adapters once; prefer concrete
  collection types and discriminated unions for tagged variants (WS messages).
  Don't drop validation via `Any`, `SkipValidation` or `model_construct()` to
  win a benchmark.
- **Compare equivalent guarantees.** A dataclass constructor does not perform
  Pydantic's validation; `dataclasses.asdict()` deep-copies. Measure
  representative data with the project's pinned versions.

Examples: [common patterns §2.6](common_patterns.md#26-typed-data-without-repeated-serialization).

## 7. Enforcement (AI + CI)
- Rules backed by a check fail CI when violated. The rest are review duties.
- Layer import rules (hard boundaries):
  - Domain does not import application/infrastructure modules, logging, I/O or
    external SDKs.
  - Application does not import infrastructure implementations; only ports
    (except `providers.py`).
  - Infrastructure can import application/domain but does not define business
    rules or import API routes.
  - `common` never imports a bounded context.
- Domain purity:
  - No `datetime.now()` / system clock in the domain. Only application calls
    `Clock`.
  - No ID generation in the domain. Application supplies IDs.
  - Domain raises only `DomainError` with `code` + `msg`.
- UoW/outbox/idempotency:
  - `AggregateRoot.pull_events()` is the only drain point.
  - Repositories track loaded aggregates in `SeenAggregates` (§5.3).
  - Outbox writes are idempotent on `event_id`.
  - Idempotency is key uniqueness plus request hash, partitioned by
    `organization_id` when present.
- Review gates:
  - Any domain change is reviewed for invariant correctness and purity.
  - Any application change keeps `uow.commit()` ownership with the `MessageBus`.
  - Any infrastructure change preserves transaction boundaries and idempotency.
- Use a consistent error contract (`code`, `message`, field errors). Map
  `DomainError` to 4xx, everything else to 500 unless explicit.
- Status codes:
  - 200/201 for success (201 for create); 202 for async acceptance; 204 for
    deletions with no body.
  - 400 malformed request/DTO failure; 401 missing/invalid auth; 403 denied;
    404 missing resource.
  - 409 idempotency conflict or duplicate operation; 412/428 OCC (§5.7);
    422 well-formed payload that violates rules.
  - 429 rate limiting; 503 dependency outage.
  - 500 only for unexpected failures, never leaking stack traces.
- Cursor pagination is the default for list endpoints: tuple comparison
  `(sort, id)`, fetch `limit+1` rows to detect the next cursor.
- API routes translate HTTP to application commands and return DTOs; they never
  call domain methods directly, open transactions or emit domain events.

### 7.1 What Is Mechanically Enforced vs Review-Only

`make check` runs lint, format check, mypy and `pytest tests/unit`, the same as
CI. Only checks under `tests/unit/` block a merge; integration tests need a
database and are verification, not gates.

| Rule | Mechanical check (blocking) |
|---|---|
| Domain never imports infrastructure or application (§7) | `test_layer_isolation.py::test_domain_layer_never_imports_infrastructure`, `::test_domain_layer_never_imports_application` |
| `common` never imports a bounded context (§7) | `test_layer_isolation.py::test_common_non_api_modules_do_not_import_other_bounded_contexts` |
| Infrastructure never imports API routes (§7) | `test_layer_isolation.py::test_infrastructure_layer_never_imports_api_routes` |
| Application never imports concrete adapters (§5.2) | `test_layer_isolation.py::test_application_layer_never_imports_concrete_adapters` |
| Domain does not log or print (§4.2) | `test_layer_isolation.py::test_domain_layer_has_no_print_or_logging` |
| No ID generation in the domain (§2, §5.6) | `test_layer_isolation.py::test_domain_layer_never_generates_ids` |
| Domain events are primitives / inherit `DomainEvent` (§4.3) | `test_layer_isolation.py::test_domain_events_are_primitives` |
| Policies/Specifications stay pure (§4.10) | `test_domain_pattern_guardrails.py::test_policies_and_specifications_take_no_io_collaborators` |
| `*Snapshot` types are frozen (§4.9) | `test_domain_pattern_guardrails.py::test_snapshot_types_are_frozen_dataclasses` |
| Append-only tables never deleted from (§4.9) | `test_domain_pattern_guardrails.py::test_append_only_tables_are_never_deleted_from` |
| Immutable rows only updated through annotated markers (§4.9) | `test_domain_pattern_guardrails.py::test_immutable_rows_are_only_updated_through_annotated_markers` |
| Aggregates evolve instead of bare `replace(self)` (§4.2) | `test_domain_pattern_guardrails.py::test_aggregates_evolve_instead_of_calling_replace_directly` |

All files are under `tests/unit/architecture/`.

**Review-only: no mechanical check exists; these are PR-review duties.**

- No float for money, VO `_enforce_types()`, `rehydrate()` in repositories,
  `SeenAggregates` tracking and handler commit ownership (§1, §4.1, §5.1, §5.3).
- Backend performance and the hot-path rule (§6.3).
- Separate state-machine axes and legal-transition tables (§4.8).
- Derived state over persisted state (§4.8).
- Snapshot-vs-reference choice (§4.9): the frozen check proves the type is
  immutable, not that the right fields were snapshotted.
- Temporal versioning / effective-dated rows (§4.9).
- Result types vs exceptions (§4.11) and decision-table completeness (§4.10).
- One aggregate per transaction and process-manager design (§5.8).
- Optimistic concurrency threading of `expected_version` (§5.7).
- Prepared-request-then-reconcile ordering (§5.9).
- Read-model discipline and work-queue leasing (§5.10); read-only UoWs (§5.11).
- Idempotency fingerprints built from stable intent only (§5.4).
- Injected, non-defaulted dependencies (§5.2.1).
- Named-intent routes vs generic `PATCH` (§6); the shared WS contract (§6.2).

**Bar for adding a new mechanical check:** it lands at **zero existing
violations**. Never ship a category with a pre-populated allowlist: fix the
violations first, or use a ratchet baseline (a checked-in file that blocks
net-new violations and only shrinks). Every detector also has a fixture test
proving it fires on the bad shape and stays silent on the good one
(`test_domain_pattern_guardrails.py::test_detectors_catch_their_violations` is
the model). A check that never fails is worse than no check.

## 8. Infrastructure & Persistence

### Migration deployment compatibility

Render applies migrations (`scripts/migrate.py`, pre-deploy) before replacing
the running app. Schema and application changes are not atomic: every migration
preserves the currently deployed version's reads and writes, including a
rollback of the app against the migrated database.

Use expand → deploy → contract across separate releases for incompatible
changes. Expand the schema without breaking old readers/writers and deploy
compatible code; then backfill and switch behavior; only once old processes are
gone may a later migration remove the legacy shape or tighten constraints. Two
migrations in the same deploy are not two rollout stages. Apply this to stored
JSON too: a new required key can break a strict old parser.

Migrations are forward-only plain SQL in `db/migrations/`. Never edit an
applied file; `migrate.py` fails on a checksum change.

- Repositories belong to infrastructure and implement domain ports. They live in
  `app/modules/*/infrastructure/...` and depend on concrete DB clients.
- Use the Postgres outbox table to persist integration events atomically with
  state.
- Publishers use `FOR UPDATE SKIP LOCKED`, dedupe by `event_id`, and are
  idempotent.
- Keep audit logs for ledger-impacting operations (who/when/what changed).
- Infrastructure may log errors or retries, following the structured logging
  contract (key-value, context fields like `correlation_id`).
- Avoid `SELECT *`; project the required columns and validate them before
  passing them to the domain.
- All infrastructure wiring happens in `app/bootstrap`, the only place that
  instantiates DB pools, clocks, UoWs and handler wiring.

## 9. Tenancy, Auditability & AI Output
- **Tenant scoping.** If the domain has organizations, every read and write is
  scoped by `organization_id` (`OrganizationId` in
  `app/modules/common/domain/value_objects/ids.py`). Repository lookups take it
  as a required parameter and every query filters on it.
- **Money and charges.** Store every amount with its currency. Charges are
  attention-seconds × cleared price, written to an append-only ledger and
  corrected only by reversal entries (§4.9).
- **AI output never defines truth by itself.** Persist every AI decision with
  `model_version`, `confidence_score` and a short `reasoning_trace`. Low
  confidence routes to a safe fallback (stock copy, the previous moment's tags)
  or manual review.
- **Translate at the adapter.** Raw VLM output becomes IAB segment ids in the
  adapter; free-text labels never reach the auction. AI copy is validated (no
  competitor names, only approved claims) before it reaches a creative. Logos
  always come from the brand kit and are never generated.
- **No forbidden inferences.** Never ask a model for race, religion, health,
  orientation, politics or precise age, and never store frames, audio, face
  crops or embeddings.

Refer to `docs/common_patterns.md` for wiring details (MessageBus → UoW →
handlers, idempotency flow, outbox publishing). This document is the high-level
rulebook those patterns implement.

## External Systems Rules

External systems are the camera/mic device, LLM/VLM and image providers, and any
third-party API. They sit behind ports.

### Anti-Corruption Layer Rule

**Provider vocabulary is translated exactly once, at the adapter edge.**

- Raw provider strings (model labels, provider status values, error codes) do
  not appear in domain or application code. The adapter maps them to internal
  enums/VOs; everything upstream speaks only the internal vocabulary.
- Provider payloads are parsed into internal types at the adapter boundary.
- Provider identifiers may be stored on aggregates only as explicit external
  references or provenance, never as the aggregate's own state vocabulary.
- Unknown provider values fail loudly at the boundary; they are never passed
  through as opaque strings.
- Provider SDKs are imported only in `infrastructure/`.

### Bulkhead & Timeout Rule

**One failing dependency must not take down unrelated flows.**

- Slow or flaky external calls (VLM tagging, copy, image generation) run on the
  warm path with their own concurrency budget. Saturating that lane never
  delays the hot loop or the API.
- Every external call has an explicit timeout. No unbounded waits.
- Repeated failures back off (DB-side attempt counter, capped exponential delay)
  and, past a threshold, stop and surface as a degraded dependency instead of
  retrying forever. The screen keeps playing cached creatives.
- Don't blind-retry an irreversible call after a timeout; reconcile first (§5.9).

### Credential Security Rule

**Never store, log or display raw API keys, tokens or client secrets.**

- Secrets come from the environment (`.env` locally, Render secret env vars in
  deploy) and are never committed, put in `.tf`/`.tfvars`, or printed.
- Log a masked identifier or the setting's name, never the value.

## Queue Consumer Rules

These apply to every at-least-once consumer we run: the outbox relay, the
`WarmWorker` jobs (tagging, copy, TTS, image and video upgrades) and
playback-report ingestion.

### Consumer Idempotency Rule

**Every queue consumer is idempotent.** All delivery channels (outbox
dispatcher, `WarmWorker` jobs, playback-report ingestion, provider
webhook redelivery such as fal job callbacks) are at-least-once. A consumer that processes the same message twice
produces the same final state.

- Check status (`status IN ('pending','failed')`, `cancelled_at IS NULL`) before
  processing.
- Playback reports: a unique `(screen_id, seq)` constraint plus a payload-hash
  comparison on conflict, so a re-sent report never charges twice.
- Commands: the idempotency repository keyed on a stable business id.
- Outbox events: `event_id` uniqueness via the outbox primary key.

### Dead-Letter Logging Rule

**Every consumer logs at ERROR when a message is dead-lettered.** Silently moving
a row to `status='dead'` or `status='failed'` with no retries left makes failures
invisible.

- Outbox dispatcher: `outbox.event_publish_failed` (WARNING, non-final) and
  `outbox.event_dead_lettered` (ERROR, final).
- `WarmWorker` jobs follow the same shape (e.g. `warm.copy_job_dead_lettered`);
  the moment keeps its stock copy or cached creative.
- Each dead-letter log includes the message/event id, `organization_id`,
  attempt count and the error.

### Retry Backoff Must Use DB Attempt Count Rule

**Backoff uses the persisted `attempts` value from the row, not an in-memory
counter.** A hardcoded `0` or a stale in-memory value makes every retry use the
minimum backoff, defeating exponential backoff.

- Leases surface `attempts` and `max_attempts` from the DB `RETURNING` clause.
- Workers that compute backoff receive the real DB value.
- Unit tests assert that the correct attempt number flows into the backoff.

## Security Rules

### SQL Injection Rule

**User input is never interpolated into SQL.** Values go through bound
parameters. Identifiers or expression fragments that must be dynamic (a sort
column, a grouping dimension) are picked from an allowlist declared by the
owning repository, and aliases match `^[a-zA-Z_][a-zA-Z0-9_]*$`.

### Domain Error Response Rule

**5xx responses return a generic "internal server error" message, never the raw
error.** Domain errors mapped to 4xx may return their domain message; anything
that becomes a 5xx is generic, so internals (SQL fragments, field names, prices)
don't leak to clients.

### Repository Tenant Scoping Rule

**Repository `get()` methods take `organization_id` as a required parameter.**
No default, no `None`; the query always includes `AND organization_id = %s`.
Signature shape: `def get(self, *, campaign_id: CampaignId, organization_id: OrganizationId) -> Campaign | None`.

### Sensitive Data Logging Rule

**Never log perception payloads, money amounts or secrets.**

- No camera frames, audio, face crops, keyframes or embeddings at any level.
- No bid, price or charge amounts in logs: log `has_<field>: bool` or an id.
  Amounts live in the ledger and the console read model.
- No tokens, API keys or connection strings.

### Production API Documentation Guard Rule

**API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled outside dev.** The
settings model rejects `enable_api_docs=True` when `app_env != "dev"`.

### Database SSL Mode Rule

**Production database connections use `sslmode=require`, not `prefer`.**
`prefer` silently downgrades to plaintext if the server doesn't advertise TLS.
Only local dev may use `disable`.

### Device Trust Rule

**A screen is an untrusted client.** Device messages are validated like any
public input (schema, bounds, `measured_at` skew, `seq` monotonicity), are
authenticated per screen, and can never set prices, charges or campaign state.

## Checklist (PR Gate)
Mark each item PASS/FAIL with file references in the PR.

### Domain
- [ ] Every invariant is enforced in aggregate/VO creation or intent methods.
- [ ] Each invariant maps to one typed `DomainError` (no generic `Exception`).
- [ ] State changes use explicit intent methods (no `set_status`, `update_*`);
  one method per business transition.
- [ ] State changes emit domain events through aggregate helpers.
- [ ] Value Objects are used for domain concepts (no primitive obsession for
  money, ids, timezone, measurements).
- [ ] Instants and metadata use shared VOs (`UtcTimestamp`, `Metadata`).
- [ ] Domain layer has no infra/logging/clock/ID-generation imports.
- [ ] Independent state axes stay in separate enums (§4.8).
- [ ] Anything derivable from stored data is derived, not persisted (§4.8).
- [ ] Values that must not drift are snapshotted; `*Snapshot` types are frozen (§4.9).
- [ ] Rules that change over time are effective-dated (§4.9).
- [ ] Expected outcomes are returned; only rule violations raise (§4.11).
- [ ] Auction, pricing and charge computations are pure functions (§4.12).

### Application
- [ ] Handlers validate external references and state the domain can't guarantee
  alone (existence, active status, scope/ownership).
- [ ] `MessageBus` owns `uow.commit()`; handlers do not commit.
- [ ] Retryable writes are idempotent (key + hash behavior is correct).
- [ ] The UoW persists outbox/integration events atomically with state.
- [ ] One transaction changes one aggregate; cross-aggregate work uses events (§5.8).
- [ ] Concurrent-edit paths thread `expected_version` and write
  `WHERE version = :expected_version` (§5.7).
- [ ] Irreversible external calls persist a prepared request and reconcile before
  any retry (§5.9).
- [ ] No network model call on the hot path (§6.3).

### API
- [ ] Routes map HTTP/WS to commands/queries and return DTOs only.
- [ ] Error mapping follows the standard contract and status codes.
- [ ] Auth uses shared dependencies (no manual token parsing in handlers).
- [ ] Response DTOs don't expose internal identifiers.
- [ ] Request DTOs enforce structural constraints (lengths, ranges, formats, enums).
- [ ] Business transitions use named intent routes (`:pause`, `:approve`), not a
  generic status `PATCH` (§6).
- [ ] WS message changes touch both the TS and the Pydantic module (§6.2).

### User-facing presentation
- [ ] Console and screen copy use readable record labels, never internal ids (§2.1).

### Bootstrap / Composition Root
- [ ] Concrete wiring lives only in `app/bootstrap`.
- [ ] Bounded-context providers stay pure (no I/O, no env access).
- [ ] Bus wiring includes command handlers, event handlers and a fresh UoW factory.

### Tests
- [ ] Domain tests cover invariants and emitted events.
- [ ] Application tests cover UoW/idempotency/outbox/clock interactions.
- [ ] Auction tables cover competitor exclusion, the minor-safety block, VCG
  prices, reserve/no-fill and the single-brand counterfactual.
- [ ] Any new architecture check lives under `tests/unit/architecture/`, lands at
  zero violations, and has a fixture test proving it fires (§7.1).

### Observability
- [ ] Logs include trace ids (`correlation_id`, `causation_id`, `command_id`,
  `event_id`) and business context ids where appropriate.
- [ ] New loop steps add their timestamp to the latency trace (§5.5).
- [ ] No frames, audio, embeddings, amounts or secrets in logs or error payloads.
