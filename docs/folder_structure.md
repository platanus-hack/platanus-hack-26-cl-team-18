# Folder structure

Clean Architecture + DDD + vertical slices. Each bounded
context is self-contained; HTTP stays thin and delegates to application handlers;
cross-cutting pieces live in `app/modules/common` (see its README).

```text
app/
  main.py                      # ASGI entrypoint (uvicorn app.main:app): lifespan, routers, /media
  config/
    settings.py                # AppSettings (pydantic-settings, env / .env)
    logging_setup.py           # one-line log format with bound context
    logging_context.py         # request-context binding for logs
  bootstrap/                   # composition root: the ONLY place concrete adapters are chosen
    container.py               #   build_container(): clock, hub, router, warm worker; register_contexts()
  contract/                    # THE wire contract (self-contained: edge/ vendors it)
    messages.py                #   Pydantic messages + socket unions (source of truth)
    codegen.py                 #   -> web/src/contract/messages.gen.ts (make contract)
  realtime/                    # transport plumbing, no business logic
    hub.py                     #   live sockets, fan-out, *_threadsafe publish from handlers
    router.py                  #   inbound message -> context handlers (run in a thread)
    mailbox.py, seq_gate.py    #   latest-wins buffer, monotonic seq filter (drop stale, never queue)
    warm_worker.py             #   warm-path asyncio jobs: per-provider limits, dedupe by key
  api/                         # thin transport: validate -> stamp received_at -> route
    ws.py                      #   /ws/agent, /ws/screen/{screen_id}, /ws/console
    health.py, dev.py          #   /health; /dev/push (not in prod)
  modules/
    common/                    # shared kernel (leaf: imports no bounded context)
      domain/primitives/       #   AggregateRoot, Entity, ValueObject, TypedId, DomainEvent, Policy, Specification, errors
      domain/value_objects/    #   Money, Quantity, Percentage, UtcTimestamp, ...
      application/             #   Command, MessageBus, UnitOfWork, Clock, IdGenerator, idempotency
      infrastructure/          #   Postgres UoW + outbox writer, SystemClock
    ai/                        # LLM kernel: AIService, provider port, Anthropic adapter, pydantic parser (see its README)
    # bounded contexts (CONTEXT-MAP.md): screens/, audience/, campaigns/,
    # auction/, creative/, delivery/
    <context>/                 # one folder per bounded context
      domain/
        <aggregate>/
          model.py             #   aggregate root: invariants + explicit transitions
          events.py            #   DomainEvent subclasses
          errors.py            #   DomainError subclasses
          repository.py        #   repository port (Protocol / ABC)
        value_objects/ids.py   #   TypedIds owned by this context
        policies.py            #   pure rules (no IO, no repositories)
      application/
        commands/<use_case>/
          command.py           #   Command dataclass (named intent, not "update")
          handler.py           #   load -> call domain -> uow.commit()
        queries/<query>/       #   read side, read-only UoW
        ports/                 #   interfaces to external systems (sensors, LLMs, APIs)
        providers.py           #   handler registration for the bus
      infrastructure/
        postgres/              #   repository implementations
        <provider>/            #   adapters implementing application ports
      api/
        http/routes.py         #   DTO validation -> bus.handle(command); no business logic
gpu/                           # planned: stateless YOLO inference service, Docker + TensorRT (ADR 0007)
edge/                          # perception agent scaffold (ADR 0004)
  agent/capture/               #   camera (OpenCV), mic (sounddevice), VAD
  agent/vision/                #   YOLO + ByteTrack, head pose/looking, face blur, keyframes
  agent/speech/                #   sound scene and opt-in speech recognition (ADR 0005)
  agent/contract.py            #   WS message models (mirror of the backend contract)
web/                           # Vite + React + TS; conventions in web/README.md
  src/app/                     #   pathname routing, one lazy chunk per route
  src/features/screen/         #   /screen: A/B double-buffer player (pure state machine), preload, house ad
  src/features/console/        #   /console: reducer, metrics (p50/p95, lift), panels
  src/shared/ui/, src/lib/     #   primitives; TypedSocket (zod-validated, reconnecting), store
  src/contract/                #   messages.gen.ts: GENERATED from app/contract/messages.py
db/
  migrations/                  # plain numbered SQL files
scripts/
  migrate.py                   # applies db/migrations once each (Render pre-deploy)
  tf.sh, terraform_init.sh     # Terraform wrapper + offline-first init
terraform/
  modules/render-app/          # web service + Postgres + secrets env group
  envs/prod/                   # one root/state per environment (docs/terraform.md)
tests/
  unit/                        # fast, no DB, no network
    architecture/              #   layering / purity / append-only guardrails (run in CI)
  integration/                 # real Postgres (@pytest.mark.integration)
CONTEXT.md                     # domain glossary
CONTEXT-MAP.md                 # context ownership, published interfaces and wiring
docs/
  product.md                   # product source of truth
  architecture.md              # runtime topology, latency budget, auction, contract
  demo.md                      # demo script, insurance, pitch
  adr/                         # this project's decisions
  rules.md                     # architecture rules
  common_patterns.md           # how to use the common kernel
  agents/                      # config for the agent skills (issue tracker, labels, domain docs)
.agents/skills/                # shared agent skills (.claude/skills symlinks here)
Makefile                       # `make help`; `make check` = what CI runs
docker-compose.yml             # local Postgres; db/migrations auto-apply on a fresh volume
.github/workflows/ci.yml       # lint + format + mypy + unit tests
```

Rules of thumb:

- Domain imports nothing from application/infrastructure/api.
- Application imports domain + ports, never a concrete adapter or SDK.
- A context never imports another context's internals; talk through events,
  a published query, or a port.
