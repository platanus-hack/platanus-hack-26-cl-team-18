# `web/`: player (`/screen`) and console (`/console`)

Vite + React + TypeScript (`npm`), per `docs/stack.md` §6. Read this before
changing anything under `web/`.

```bash
make dev           # backend on :8000 (from the repo root)
make web-dev       # Vite on :5173, proxies /ws /health /media /dev to :8000
open http://localhost:5173/screen?id=demo-01
open http://localhost:5173/console
make web-check     # typecheck + lint + prettier + vitest + knip (what CI runs)
```

To see the player react before the bounded contexts exist, `POST /dev/push`
any outbound contract message (`creative.ready`, `console.*`). It is disabled
in prod.

## Layout and boundaries

```text
src/
  app/            routing (pathname switch, one lazy chunk per route), root error boundary
  features/
    screen/       player: player-machine.ts (pure A/B state machine), preload, CreativeView, house ad
    console/      console-state.ts (pure reducer), metrics.ts, ConsolePage panels
  shared/ui/      visual primitives used by several features (StatusBadge, Panel, ErrorBoundary)
  lib/            infrastructure: TypedSocket, external store, config, cn()
  contract/       messages.gen.ts (GENERATED) + index.ts barrel
  styles/         global.css: design tokens (@theme) and base styles
```

Dependency direction: `app -> features -> shared -> lib -> contract`. ESLint
enforces it:

- A feature is imported only through its `index.ts` (`@/features/screen`),
  never through its internals.
- `shared/`, `lib/` and `contract/` never import a feature or `app/`.

Put code used by one feature in that feature. Move it to `shared/` only when a
second feature needs it.

## The contract is generated

`src/contract/messages.gen.ts` (zod schemas plus inferred types) is generated
from `app/contract/messages.py`. Never edit it by hand. After changing the
Pydantic contract, run `make contract` and commit both files. A pytest check
fails CI when they drift.

- **Every inbound frame is validated** (`TypedSocket` + the socket's schema).
  UI code only ever sees parsed, typed messages. Invalid frames are counted and
  dropped.
- Import from `@/contract`, never from the `.gen` file.
- Money arrives as decimal strings (`MoneyDTO`). Display them as strings. Any
  float math is presentation-only and is labelled as such (`coBrandLift`).

## State

`TypedSocket.start()` is idempotent. Only the active connection's callbacks may
change state, deliver frames or schedule a reconnect. Stopping invalidates the
connection before closing it, so late callbacks from a previous React effect
cannot open duplicate sockets or deliver stale creatives.

- Realtime state lives in an **external store driven by a pure reducer**
  (`lib/store.ts`, `useSyncExternalStore`). Socket data never goes through
  `useState` or `useEffect` copies.
- Model state as **discriminated unions, not boolean soup**: `ConnectionState`,
  the player `Phase`.
- Reducers and metrics are pure and unit-tested. Effects (preload, timers,
  sends) stay in the page component.

## The player: never blank

- There are two stacked layers (A/B). A new creative mounts hidden in the back
  layer and its required logos are decoded (each distinct URL once). It then
  crossfades over the front for 250 ms (`--duration-crossfade`). Hero images
  and video load in the mounted layer without delaying copy, price or logos.
  Video stays hidden until that element emits `canplaythrough`.
- The front layer always holds the bundled house ad or the last good creative.
  If a preload fails, the current creative stays on screen.
- Latest wins: a newer creative replaces one that is still preloading.
- Copy/voice upgrades update the matching creative in its existing layer when
  auction ownership, brand ids and logo URLs match. They keep the current video,
  dwell stage and play window, without another decode or crossfade. An upgrade
  never replaces a newer queued creative or revives a retired one. Changed brand
  assets require a new `creative.ready` and the normal logo checks.
- An in-flight preload retains its original asset request when copy/voice is
  upgraded, so warm work cannot keep restarting the logo decode.
- Voice preparation runs independently when the creative reaches the front.
  Cached audio plays immediately; late audio plays only if its creative is
  still on air. Replacing the creative cancels pending audio and stops playback.
- When a creative leaves the screen, `playback.report` is sent. The backend
  computes attention-seconds.
- Motion is CSS transitions and WAAPI only. No animation libraries.
  `prefers-reduced-motion` makes swaps instant.

## Design system

Tokens live in `src/styles/global.css` and come in three layers: palette →
semantic roles → Tailwind utilities (`bg-surface`, `text-muted`, `text-kpi`,
`rounded-container`, `shadow-resting`).

- **The scale is closed.** Don't use arbitrary color, radius, shadow, duration
  or z-index values. If you need a new value, add a token. Viewport-relative
  layout sizes (`h-[14vh]`) are the only exception.
- There are exactly two elevations: `shadow-resting` and `shadow-overlay`.
- **Never show status by color alone.** Use `StatusBadge` (icon, label and
  color together).
- Numbers use `tabular-nums`. KPIs use the `text-kpi` tier.
- Use the one global `:focus-visible` ring, and never `outline-none` without a
  replacement.
- Every data view handles its empty state. During a reconnect, keep showing
  the last data with a "Reconnecting" badge. Never blank it.
- Each console panel has its own error boundary (`Panel`).
- The workspace is white-first: white surfaces, ink text, fine dividers and teal
  accents. Status colors use accessible dark text on pale backgrounds. Player
  creatives own their presentation independently of the workspace theme.

## Tooling

- `tsconfig.json` sets `strict`, `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `noPropertyAccessFromIndexSignature` and
  `verbatimModuleSyntax`.
- ESLint uses typescript-eslint `strictTypeChecked`, with
  `no-floating-promises`, `switch-exhaustiveness-check` and
  `consistent-type-imports`. It runs with zero warnings allowed.
- Prettier uses organize-imports and the Tailwind class sorter.
- There are two compilers (ADR 0006 amendment). TypeScript 7 (`typescript-native`) runs
  `npm run typecheck`, which is about 7 times faster. TypeScript 5.9 stays because
  typescript-eslint needs TS `<6.1`. Keep `tsconfig.json` valid for both (no
  `baseUrl`). `npm run typecheck:legacy` checks with 5.9 explicitly. Call the
  compilers by explicit path: the bare `tsc` link resolves to TS 7.
- Vitest tests sit next to the code as `*.test.ts`. knip catches dead files
  and dependencies.
