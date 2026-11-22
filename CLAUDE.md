# team-18 — Agent Context Document

`CLAUDE.md` is the canonical instruction document. `AGENTS.md` is a relative
symlink to it so Claude Code and Codex read the same guidance. Edit this file;
keep the symlink.

## Context

Platanus Hack 26 (Santiago ft. Buk), track **Physical World**. A hackathon
build: speed matters, but the code is written by agents and must stay easy for
the next agent to extend. The architecture and the `common` kernel are
production-grade DDD. Use them to remove decisions, not to add ceremony.

**Pragmatism rule:** apply a pattern when it prevents a concrete bug, such as a
duplicate side effect on retry, time-dependent tests, mixed-up ids, or state
written without its event. Skip it when it is only ceremony. A demo that works
beats a perfect layer you never finished.

## Product (read `docs/product.md` before building a feature)

**Basket ads for physical screens.** A screen with a camera and mic sees the
group in front of it (anonymously), tags the moment with IAB interest
segments, runs a **bundle auction** in which 1 or 2 brands (ADR 0008 D4) with
*complementary* products (coffee + pastry, energy drink + burger) win the
screen together, and shows a joint **basket offer sized to the group** ("Trío
$X") within about 1.5 s. The ad **evolves while people stay** (teaser → offer
→ close with QR). Brands pay per measured **attention-second**, so **zero
attention costs zero**, and each basket's QR/promo **redemption is credited to
every brand in it**. We sell **moments, not slots**. The first market is
convenience stores and gas-station shops.

The thesis: online, one impression means one person. A physical screen is
seen by N people with different needs, so selling it to one brand wastes
attention. Basket ads raise **audience coverage** and revenue per screen, and
the redemption proves the sale.

The loop: **see → understand → auction → compose → play → measure → bill →
prove**.

Product invariants (never break these, even for the demo):

1. **Pre-approved partners only, never competitors.** A basket forms only
   from pairs on *both* brands' partner allow-lists with an approved basket
   loop; competitor exclusion is per brand category. Both are pure
   `Specification`s in the auction. AI copy never changes an offer's price or
   claims.
2. **Minors are protected, never targeted.** `minor_likely` only *blocks*
   age-restricted categories (alcohol, gambling, unhealthy food).
3. **No identity, no storage of frames, audio or transcripts, no forbidden
   inferences** (race, religion, health, orientation, politics, precise age).
   Raw frames go only to **our own** GPU inference service (TLS, in memory,
   never stored; ADR 0007). Anything sent to a third party (the VLM) is
   face-blurred first.
   Tracklets are anonymous and session-local. Speech mode is opt-in per screen
   with a visible indicator; utterances are ephemeral, and only IAB topics with
   a TTL survive (ADR 0005).
4. **Logos are never AI-generated.** Creatives use the brand kit's real assets;
   AI writes copy and optional hero imagery only, and the copy is validated
   (no competitor names, only approved claims). Stock copy is the fallback.
5. **The screen is never blank.** A house ad or the last good creative is
   always ready.
6. **Money is `Money`.** Bids and prices are per attention-second, and charges
   go to an append-only ledger.

## Latency rules (read `docs/architecture.md`)

- **Three paths** (ADR 0001): **hot** (YOLO perception, moment change,
  auction, cache/template creative, swap), **warm** (VLM tags, speech topics,
  AI copy, TTS voice-over, hero image, turbo video upgrade), **cold** (brand
  kits, a pre-generated video loop per compatible bundle, pre-made
  voice-overs). **Hot-path code never awaits
  a network model call.** Warm results *upgrade* the moment or the creative in
  place.
- Targets: glass-to-glass p50 < 1.5 s and p95 < 2.5 s; a cache hit < 300 ms; the
  server-side decision < 30 ms (in-memory campaign book; Postgres is the
  record, not the hot read).
- Drop stale frames, never queue them. Debounce moments so passers-by don't
  re-auction.
- Every decision carries a latency trace (`measured_at`, `received_at`,
  `cleared_at`, `ready_at`, `displayed_at`) shown live in the console. If you
  add a step to the loop, add its timestamp.
- Video is never generated in the hot path (ADR 0003, amended). Play the
  bundle's pre-generated loop now; a turbo image-to-video model may upgrade it
  later, under a budget cap. Logos, copy, QR codes and prices are player
  overlays, never inside generated video. Fast-video strategy:
  `docs/architecture.md` §8.

## Bounded contexts (planned, `app/modules/<name>`)

`screens` · `audience` · `campaigns` · `auction` · `creative` · `delivery`.
Flow: `audience` emits `AudienceMomentChanged` → `auction` emits
`AuctionCleared` → `creative` emits `CreativeReady`/`CreativeUpgraded` → WS
push → `playback.report` → `delivery` charges. Details, the auction algorithm
(VCG bundle auction), voice (§7), fast video (§8) and the agent↔backend↔player
message contract (§10) are in `docs/architecture.md`. Vocabulary is in `CONTEXT.md`; use those exact
terms in code.

## Demo-first priorities

The demo script in `docs/demo.md` decides what to build. In order: the live
loop works end to end → **simulator/replay mode** (demo insurance) → the
console shows coverage, the single-brand counterfactual, exclusion reasons and
latency → the five beats: basket ad with group offer → **complete the basket**
(held products) → **bidding war** (`/demo/<key>/advertiser`) → QR redemption + moment
receipt + "charged $0" → pre-generated bundle loops + voice-overs → backup
moments (stock-out via `/demo/<key>/clerk`, speech mode, context switch, queue) → polish
(thermal printer, warm video upgrade, distance-aware layouts). A feature that
doesn't make a demo moment land better is backlog.

## Stack (binding: `docs/stack.md`, ADR 0006; don't swap a component without a new ADR)

- **GPU:** YOLO runs on a **remote, stateless GPU inference service `gpu/`**
  (Docker, YOLO26 TensorRT, WSS, ≤ 2 frames in flight, never queued), with a
  hot-standby local detector and automatic failover (`FailoverDetector`,
  ADR 0007). The provider (serverless, VM or venue LAN box) is picked from
  `scripts/bench/gpu.py` results: round trip p50 ≤ 80 ms, p95 ≤ 150 ms.
  Generation stays on hosted APIs.
- **Devices:** one laptop (any; the GPU is remote) + wide-FOV UVC webcam
  (720p30 MJPG, manual exposure) + **USB speakerphone with hardware echo
  cancellation** (room mic *and* ad audio) + 16:9 TV in Game mode over HDMI.
- **Perception agent `edge/`:** Python **3.12**, its own uv project.
  **YOLO26-pose** every frame (people, looking from keypoints, face blur box,
  distance) and **YOLOE-26s** open-vocabulary objects at 5 fps (the demo
  vocabulary in `edge/agent/vision/vocabulary.py`, baked once by `make edge-yoloe`), called through the `Detector`
  port (remote GPU first, local fallback). ByteTrack runs locally. Capture
  thread with a single latest-frame slot.
  Silero VAD → **ElevenLabs Scribe v2 Realtime**, with our own keyterm matcher
  on *partial* transcripts and an echo guard. It is infrastructure: it emits
  facts and makes no decisions.
- **Backend `app/`:** Python 3.14, managed with `uv`; the venv is `.venv/`.
  FastAPI + uvicorn[standard], **single worker**, Pydantic JSON for WS (ADR 0006 amendment). Hot state lives in
  process memory. The kernel is synchronous: call `bus.handle()` from async
  code via `anyio.to_thread.run_sync`, never directly. Warm work runs in an
  asyncio `WarmWorker` with per-provider concurrency limits; hot-path code
  never awaits it.
- **LLM:** **Claude Haiku 5.5** (`claude-haiku-5-5`) through the **official
  `anthropic` SDK**, at effort `low`, with structured output and prompt
  caching on a frozen system prompt. It sits behind `AudienceTagger`,
  `TopicExtractor` and `CopyWriter`, via `AnthropicProvider` (the `ai`
  module's `ILLMProvider`); never an OpenAI-compatibility shim. Anthropic
  does not generate images, video or audio.
- **Voice:** **ElevenLabs Flash v2.5**, as cached MP3 voice-overs and streamed
  PCM replies (`VoiceSynthesizer`).
- **Media:** **fal** behind `ImageGenerator` / `VideoGenerator`. MiniMax H3 Max
  Turbo for warm video upgrades and a top-quality model for cold bundle loops.
  ffmpeg transcodes to H.264 720p.
- **Player + console `web/`:** Vite + React + TS (`npm`) in Chromium kiosk.
  The A/B double-buffered layers swap only after `decode()`/`canplaythrough`,
  with a 250 ms crossfade. Voice goes through Web Audio; motion is CSS/WAAPI
  only. The WS contract is `app/contract/messages.py` (backend + agent);
  the TS side is generated from it (`make contract`), never edited by hand.
- **Not used:** MediaPipe, an extra face/head-pose model, Redis/Kafka/Celery,
  an async psycopg rewrite, Lottie/Pixi, a cloud backend for the live demo.
- **Day-1 benchmark gate** (`docs/stack.md` §8): a component that fails is
  swapped alone and the swap is recorded in ADR 0006.
- Live demo runs the backend on **localhost** (`make station`); Render hosts the public URL.
  The station is `/screen` + `/console`; demo tools (bids, stock, campaigns, `/dev`) sit
  behind `DEMO_KEY` at `/demo/<key>` (`app/api/demo_access.py`, `web/src/lib/demo.ts`).
- PostgreSQL via psycopg2. Migrations are plain SQL in `db/migrations/`.
- Lint/format: ruff. Types: mypy / pyright. Tests: pytest.
- Hosting: Render, managed with Terraform in `terraform/` (`make tf-check`,
  `make tf-plan`, `make tf-apply`). Migrations run via `scripts/migrate.py`
  as Render's pre-deploy command. `make help` lists every target.

```bash
make up            # ONE command: set up what's missing (.env, deps, models, DB) + run the station
make venv          # uv venv + uv sync --extra dev
make check         # lint + format-check + mypy + unit tests (what CI runs)
make test ARGS="tests/unit/x.py::test_y"   # narrow while editing
make test-arch     # architecture guardrails only
make eval          # product evals on a live backend: demo beats, invariants, KPIs, latency (docs/evals.md)
make db-up         # local Postgres (docker compose) + scripts/migrate.py, same runner as Render
make help          # everything else
```

Copy `.env.example` to `.env` for local config. CI (`.github/workflows/ci.yml`)
runs the same steps as `make check`. Pre-commit hooks are optional:
`uv run --with pre-commit pre-commit install`.

If you hit `ModuleNotFoundError`, it is an environment problem: sync the venv.
Never patch production code around a missing package.

## Required reading before changing code

| File | Purpose |
|---|---|
| `docs/product.md` | **What we're building and why**: problem, loop, invariants, metrics, scope |
| `docs/stack.md` | **Binding stack** (ADR 0006): devices and their settings, every component, what we don't use, day-1 benchmark gate |
| `docs/architecture.md` | Runtime topology, latency budget, stack rationale, contexts, auction, creative, message contract |
| `docs/demo.md` | Demo script, console layout, demo insurance, pitch and judge Q&A |
| `docs/evals.md` | **Product feedback loop**: live-loop scenario evals, gates vs ratchets, real-video perception, rehearsal scoring |
| `docs/creative-generation.md` | **Ad generation**: brief → angle + group offer → stock/AI copy → guard → score; metrics plan and `make eval-creative` |
| `docs/privacy.md` | **Privacy evidence**: each privacy concern → enforcing code → test, plus open gaps; update it when you touch perception, speech or storage |
| `CONTEXT.md` | Domain glossary (moment, segment, bundle, attention-second…) |
| `docs/adr/` | Binding decisions (0001 latency paths, 0002 superseded, 0003 video cold loops + warm upgrades, 0004 YOLO perception agent, 0005 ElevenLabs speech + voice, 0006 committed stack, 0007 remote GPU perception, 0008 demo-critical decisions: vision first, prop markers, tunnel, max 2 brands, coverage headline, CLP/Spanish, two laptops) |
| `docs/rules.md` | Architecture rules (§1–7, Security and Queue-consumer rules apply as written) |
| `docs/common_patterns.md` | How to use the shared kernel: VOs, message bus, UoW, outbox |
| `docs/folder_structure.md` | Layout of this repo and of a bounded context |
| `docs/terraform.md` | Render/Terraform rules and deploy runbook; read before touching `terraform/`, `db/migrations/` or deploy scripts |
| `app/modules/common/README.md` | What the kernel provides and how to wire it |
| `app/modules/ai/README.md` | Read before any LLM feature: typed structured output, provider errors, env config |
| `web/README.md` | Read before touching `web/`: layering, generated contract, player state machine, design tokens |

## Layers (DDD)

| Layer | Responsibilities | Constraints |
|---|---|---|
| Domain | Aggregates, value objects, domain events, policies, specifications | Pure and deterministic: no IO, no logging, no `datetime.now()`, no `uuid4()` |
| Application | Command/query handlers, ports, orchestration | Depends on ports, never on concrete SDKs or adapters |
| Infrastructure | Postgres repositories, hardware/sensor/LLM/API adapters | Implements application ports |
| API | HTTP routes, auth, DTO validation | No business logic: validate → build command → `bus.handle()` |

`app/bootstrap/` is the composition root and the only place that picks
concrete adapters. `app/modules/common/` is a leaf: it never imports a
bounded context.

## Command handling flow

```
API          validate DTO → build a named-intent Command → bus.handle(cmd)
Application  load aggregate via repository → ids from IdGenerator → time from Clock
Domain       check invariants → explicit transition → record DomainEvent
Bus/UoW      commit state + outbox rows in one transaction → dispatch events
```

## What `common` gives you (do not rewrite these)

| Need | Use |
|---|---|
| Aggregate / entity / value object bases | `AggregateRoot`, `Entity`, `ValueObject` (`domain/primitives/`) |
| Typed ids | `TypedId` subclasses; one per aggregate |
| Events | `DomainEvent` (domain), `IntegrationEvent` + outbox (application/infrastructure) |
| Errors | `DomainError` family in `domain/primitives/errors.py` |
| Rules | `Policy`, `Specification` |
| Time / ids | `Clock` + `SystemClock`; `IdGenerator` (UUIDv7) |
| Dispatch + transactions | `Command`, `MessageBus`, `AbstractUnitOfWork`, `make_uow_factory` |
| Idempotency | `compute_request_hash`, `idempotency_fingerprint_for` |
| Money and quantities | `Money` (Decimal + currency), `Quantity`, `Percentage`, `RoundingPolicy` |
| Time values | `UtcTimestamp`, `DateRange`, `UtcDateRange`, `Timezone` |
| Misc VOs | `Name`, `Url`, `Address`, `Metadata`, `IdempotencyKey`, `DedupeKey` |

Search `app/modules/common` before writing a new helper. If it exists, use it.
If it's missing and generic, add it there with tests.

For LLM calls use `app/modules/ai` (`AIService` + `structured_output_for(Model)`
+ `parse_pydantic_json`). Don't add another LLM client.

## Guardrails CI enforces

`tests/unit/architecture/` fails the build when:

- the domain imports application/infrastructure, logs, prints, or mints ids;
- application code imports a concrete adapter or an API module (except `providers.py`);
- `common` imports a bounded context, or infrastructure imports API routes;
- a domain event doesn't inherit `DomainEvent`;
- a Policy/Specification takes a repository, session or client;
- a snapshot dataclass isn't frozen, or an aggregate uses bare `replace(self)`;
- SQL deletes from an append-only table or updates an immutable one.

Register new append-only and immutable tables in
`test_domain_pattern_guardrails.py` (`APPEND_ONLY_TABLES`, `IMMUTABLE_ROW_TABLES`).

## Agent skills

Shared skills live in `.agents/skills/` (`.claude/skills` points to it), e.g.
`tdd`, `diagnose`, `grill-me`, `to-prd`, `to-issues`, `triage`, `zoom-out`,
`improve-codebase-architecture`. Supporting config:

- Issues: GitHub Issues via `gh`. See `docs/agents/issue-tracker.md`.
- Triage labels: `docs/agents/triage-labels.md`.
- Domain language: `docs/agents/domain.md` (a `CONTEXT.md` is created lazily; ADRs go in `docs/adr/`).

## Core rules

- **Ids:** UUIDv7 from an injected `IdGenerator`, wrapped in a `TypedId`.
- **Time:** an injected `Clock`. Name the instant you mean: `occurred_at`,
  `recorded_at`, `measured_at` and `effective_at` are different facts.
  Sensor/physical-world readings carry both the device's `measured_at` and the
  server's `received_at`.
- **Money:** `Decimal` via `Money`, never `float`. Physical measurements with a
  unit go in a value object, not a bare float passed around.
- **Value objects over primitives:** validate at construction so invalid
  states cannot exist.
- **Aggregates change through explicit, named transitions** (`device.activate()`,
  not `device.status = "active"`). Use one state enum per independent axis.
- **Derive status when the data already implies it**; don't persist a status
  that can go stale.
- **One transaction, one aggregate.** Coordinate across aggregates with events.
- **State plus its event is atomic** (outbox in the same transaction).
- **Idempotency:** writes that can be retried, such as webhooks, device uploads
  or API POSTs, carry an idempotency key or a natural dedupe key. The same key
  with a different body must fail.
- **Optimistic concurrency:** aggregates edited by two flows carry `version`.
  Writes use `WHERE version = :expected`; a lost update raises.
- **Multi-tenant:** advertisers and venues are organizations; every query on
  their data (campaigns, budgets, charges, screens) filters by
  `organization_id`.
- **External systems** (hardware, LLM, third-party APIs) sit behind ports with
  an anti-corruption layer. Raw provider strings never leak into the domain.
  Don't blind-retry an irreversible call after a timeout; reconcile first.
- **Perception is a sensor.** An `audience.snapshot` carries `seq` (dedupe
  key per screen), the device's `measured_at` and the server's `received_at`.
  The raw VLM output is translated to IAB segment ids at the adapter
  (anti-corruption layer); free-text labels never reach the auction.
- **Auctions are pure and deterministic.** The bundle auction is a domain
  function of (moment, eligible campaigns, layouts): no IO, no clock, stable
  tie-breaking. Record every exclusion with its reason (competitor, safety,
  budget, below reserve) because the console shows them.
- **AI output never defines truth by itself.** Store `model_version`,
  `confidence_score` and `reasoning_trace`, and route low confidence to manual
  review or a safe fallback.

## Prohibitions

Agents must never:

- use `float` for money
- query the DB or do IO inside the domain layer
- call `datetime.now()` / `uuid4()` in domain code
- bypass repositories or put business logic in routes
- combine independent state axes into one enum
- let a Policy/Specification take a repository, session or client
- log secrets, tokens or personal data
- store or log camera frames, audio, transcripts, face crops, face or voice
  embeddings; add face recognition, speaker identification or
  re-identification; ask a model for a forbidden inference; run speech
  recognition when speech mode is off; read license plates (vehicles are
  class only) or describe a person when recognizing a held product (objects
  only)
- put a network model call (VLM, LLM, STT, TTS, image or video gen) in the hot
  path, or start an uncapped video-generation job
- let a model generate or redraw a brand logo
- commit `.env`, credentials or API keys (`.codex/config.toml` holds a team token URL; don't print it)
- put a secret value in `.tf`/`.tfvars`, or commit/print `*.tfstate`, `tfplan` or `.env.render`
- run `terraform apply` without the reviewed plan (`make tf-plan` then `make tf-apply`), or edit an applied migration

## Tests

- Domain: plain unit tests with a fake `Clock` and a deterministic `IdGenerator`.
- Handlers: in-memory repositories and a fake UoW.
- Postgres repositories: `@pytest.mark.integration`, run on purpose.
- Auction: table-driven tests for competitor exclusion, the minor-safety
  block, VCG prices, reserve/no-fill and the single-brand counterfactual. These
  are the demo's credibility, so they never go red.
- Perception and creative adapters: test against recorded fixtures (snapshot
  JSON, blurred keyframes, recorded model responses), never live providers in
  unit tests.
- Run the affected test files during edits and the full unit suite before
  pushing. `tests/unit/common/**` must stay green.
- A change to the loop (audience, auction, creative, delivery, demo wiring)
  runs `make eval` before and after. A ratchet that improved is locked in with
  `make eval-baseline` in the same PR; never lower the baseline without saying
  why. A bug found in a rehearsal becomes a scenario in `evals/scenarios/`
  first. A known gap becomes an `xfail(strict=True)` test citing its
  requirement.

## Working style

- Investigate before asking: read the code, run it, reproduce the bug. Ask only
  for facts you can't get yourself.
- A pushed fix is not a verified fix. Exercise the actual user path (device →
  API → screen) before calling it done.
- This is a shared team repo. Keep changes scoped to the task, don't rewrite a
  teammate's work you weren't asked to touch, and commit/push only when asked.
