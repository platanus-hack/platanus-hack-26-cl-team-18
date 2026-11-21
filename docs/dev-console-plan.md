# Plan: `/dev`, the QA and developer console

Status: proposed · Owner: unassigned · Related: `docs/demo.md` §2, `docs/architecture.md` §10, `web/README.md`

## 1. Goal

One page that lets us watch the whole loop for one screen while it runs:
**see → understand → auction → compose → play → measure**. It must show:

- what YOLO sees, live: boxes, track ids, who is looking, distance, held objects;
- what the VLM and speech extracted, and what the anti-corruption layer kept or dropped;
- the bundle auction as it clears: bids, exclusions, VCG prices, history;
- the creative being built: brief, angle, offer, stock vs. AI copy, guard verdict,
  voice-over, video loop, each upgrade as it lands;
- the ad that is on air, mirrored, next to all of the above.

It is for QA during rehearsals and for agents debugging the loop. It is **not**
the demo console: `/console` stays the curated money shot for judges, and
`/screen` stays a clean physical screen that shows only the ad.

## 2. What exists today

| Piece | Where | What it covers | Gap |
|---|---|---|---|
| `/console` | `web/src/features/console/` | Moment (segments, sources, held products, minor flag), bundle auction (bids, exclusions, VCG prices, coverage, single-brand counterfactual), latency (p50/p95, trace steps, GPU timing), receipts, market book, live input status, degradation chips | Only the *latest* moment and auction. No boxes, no VLM detail, no creative build, no on-air view, no history, no raw event log |
| `/campaigns` | `web/src/features/campaigns/` | Cold-path studio: generate a bundle creative, see `quality_score`, `checks`, `compose_ms` | Offline only; nothing about live creatives |
| `recognition.snapshot` | `app/contract/messages.py`, `app/api/recognition.py` | Per-detection boxes, tracks, VLM attributes with evidence | Emitted only by `edge/agent/vision/replay.py`, not by the live pipeline. No UI reads it. `GET /recognition/{screen}` is poll-only |
| `audience.keyframe` | `app/api/ws.py`, `AudienceClassifier.observe_keyframe` | Face-blurred JPEG goes to the VLM | `KeyframeTags` (segments, held products, minor flag, `reasoning`, `model_version`) are merged into the moment and never shown on their own. ACL drops (unknown category or brand) are silent |
| `WarmWorker` | `app/realtime/warm_worker.py` | VLM tags, Claude topics and copy, ElevenLabs voice, under per-provider limits | Only aggregate `WarmStats`. No per-job start, end, outcome or latency |
| Creative build | `app/bootstrap/demo.py` (`brief_for`, `publish`, `request_copy`, `upgrade_copy`, `request_voiceover`, `with_basket_extras`) | Brief → offer → stock copy → AI copy → guard → voice → loop | Only the final `creative.ready`/`creative.upgraded` leaves the process. The brief, rejected AI drafts, guard reasons, voice and loop cache hits are invisible |
| `POST /dev/push` | `app/api/dev.py` | Inject contract messages; disabled in prod | Good pattern to copy for gating |

## 3. Route and isolation

- **`/dev?screen=demo-01`**: a new lazy route chunk in `web/src/app/App.tsx`,
  in a new `web/src/features/dev/` feature. The kiosk player never downloads
  it, the same as the console today.
- **New socket `/ws/dev`** (backend), **closed with 4404 when `app_env == "prod"`**,
  like `/dev/push`. Every dev connection gets all `ConsoleOutbound` messages plus
  a new `DevOutbound` union. The `/ws/console` contract and its traffic stay
  untouched, so 5 Hz box frames can't slow the judges' console.
- `Hub` gets a `_devs` sink set. `broadcast_console` also fans out to devs, and
  `broadcast_dev` goes only to devs. **It is free when nobody watches:**
  emitters check `hub.dev_count() > 0` before building a dev message. The same
  slow-sink drop rules apply.
- **On-air mirror, not a second player.** The dev page renders the
  `creative.ready`/`creative.upgraded` it receives with the player's
  `CreativeView` (exported from `@/features/screen`). It does **not** open
  `/ws/screen/{id}` or send `playback.report`, so it can never double-bill or
  change attention. It is labelled "mirror: what the screen should be showing".
  Ground truth for what actually played stays `player.displayed`, which the
  backend relays as a `dev.player` event (§4.6).
- The index page gets a fifth, muted tool card, "Dev / QA", shown only when
  `import.meta.env.DEV` or `?dev=1`.

## 4. New contract messages (`DevOutbound`)

All are added to `app/contract/messages.py`, exported to TS with `make contract`, and
validated by `TypedSocket` like every other socket. None carry pixels, audio,
transcripts or identity.

### 4.1 `dev.perception`: live YOLO (edge → backend → dev)

Sent by the edge agent at most 5 Hz, only when `EDGE_DEV_STREAM=1` (set in
`.env.example` for local runs, off otherwise). The backend relays it without storing it, and drops it if
no dev is connected.

```
screen_id, seq, measured_at, frame_w, frame_h,
detector, inference_ms, round_trip_ms, capture_fps, dropped_frames,
people: [{track_id, box (normalized xyxy), confidence, looking, distance_band,
          dwell_s, keypoint_quality}],
objects: [{label, confidence, box, track_id?, held_by_track?, source: "yoloe"|"marker"}],
blurred_faces: int   # proof the blur ran; never the boxes' contents
```

`edge/agent/vision/publisher.py` already has `tracks` with `.box` and the
object association in `snapshot.py`. This is a second, lossy projection of the
same state, not a new pipeline. Track ids stay session-local and anonymous
(invariant 3).

### 4.2 `dev.vlm`: one per tagging call

Emitted from `AudienceClassifier._apply_tags` and from the failure branch in
`observe_keyframe`, through a new optional `on_dev` callback. The classifier
stays free of hub imports.

```
screen_id, keyframe_seq, submitted_at, finished_at, latency_ms, model_version,
outcome: "ok" | "failed" | "dropped_in_flight" | "stale",
segments: [{segment_id, label, confidence, kept: bool, drop_reason?}],
held_products: [{category, brand_ref?, confidence, kept, drop_reason?}],
minor_likely, minor_likely_confidence,
reasoning (already ≤ 280 chars and objects/context only, `tagging/claude.py`), expires_at
```

`kept=false` with `drop_reason` ("unknown_category", "unknown_brand",
"below_threshold") makes the ACL visible, which is where most "why didn't it
bid?" bugs hide.

### 4.3 `dev.speech`: topics, never text

From `receive_utterance` and `extract_topics`: `keyterms_hit`, the matcher's
`wanted`/`rejected`, the Claude topic extraction result (segment ids, TTL,
`rejected`), `echo_suppressed: bool`, and latency. **No utterance text** (ADR 0005).
QA reads "heard: cafe, hambre → IAB-xxx (ttl 45 s)".

### 4.4 `dev.job`: warm-path lifecycle

`WarmWorker` gets an optional `observer: Callable[[WarmJobEvent], None]`, a
generic hook that knows nothing about the hub. The composition root wires it to
`broadcast_dev_threadsafe`.

```
provider ("vlm"|"claude"|"elevenlabs"|"fal"), key, kind (tag|topics|copy|voice|video),
state: submitted | deduped | running | done | failed | rejected,
at, elapsed_ms?, error_type?   # exception class name only, never the message body
in_flight_by_provider: {provider: n}
```

### 4.5 `dev.creative`: the build trace per creative

Emitted next to every `creative.ready` / `creative.upgraded` in `demo.py`
(`publish`, `upgrade_copy`, `upgrade_voice`, `with_basket_extras`).

```
screen_id, creative_id, auction_id, moment_id, upgrade_kind?,
brief: {angle, party_size, segments, held_product?, daypart, minor_likely, signature},
offer: {label, price, tier_source},          # from approved tiers, never AI
copy: {source: stock|ai|cache, stock_text, ai_text?, ai_model?, ai_latency_ms?,
       guard: {passed, violations: [competitor_name|unapproved_claim|length|...]},
       score?, fallback_reason?},
voice: {line, cache_hit, voice_id, latency_ms?} | null,
video: {loop_slug?, cache_hit, url?} | null,
redemption_code?, timings: {brief_ms, compose_ms, guard_ms}
```

### 4.6 `dev.player`: what the screen reported

The backend relays `player.displayed`, `playback.report` and `player.voice`
(already received in `demo.py`) to devs as one `dev.player` envelope:
`displayed_at`, stage, attention-seconds and the voice state. This lets QA compare
the mirror with reality.

### 4.7 Auction depth (phase 3, optional)

`console.auction` already carries bids, exclusions, winners and prices. If the
domain `AuctionResult` can cheaply expose the evaluated bundle candidates (pair,
layout, value, why it lost), add `dev.auction_candidates`. Otherwise skip it:
exclusions plus bids already explain most outcomes.

## 5. Optional: blurred keyframe preview (needs a decision)

The most useful QA view draws boxes over the real frame. The backend already
holds the face-blurred keyframe in memory on its way to the VLM. Option:

- `DEV_KEYFRAME_PREVIEW=1` (default **off**, refused when `app_env == "prod"`)
  relays that same blurred JPEG to dev sockets as a binary frame, about 1 per
  2 s, never stored or logged. The UI draws it under the box canvas and drops it
  after 5 s.
- Default: a **schematic canvas** (16:9 frame, boxes, labels, looking arrows) with
  no pixels at all.

This touches invariant 3 (blurred frames reach a screen other than the VLM's), so
the team decides. Recommendation: ship the schematic first and add the preview only
for local rehearsals, behind the flag.

## 6. UI layout (`/dev`)

```
┌ Top bar: screen ▾ · Live/Replay/Sim · socket ● · degradation chips · warm in-flight per provider ┐
├──────────── Perception ─────────────┬──────────── Decision ──────────┬──────── On air ───────────┤
│ Box canvas (schematic | keyframe)   │ Moment: people/looking,        │ Mirror (CreativeView,     │
│  tracks, looking ▲, held objects    │  segments w/ source bars       │  scaled 16:9), stage,     │
│ Track table: id dwell look dist     │ Auction: bids, exclusions,     │  creative id, copy source │
│ Perception timing sparkline         │  winners, VCG, coverage, lift  │ Build trace (§4.5):       │
│ VLM card: last call, kept/dropped,  │ Auction history strip (last 20 │  brief → offer → copy     │
│  reasoning, latency, model          │  moments: winners, trigger,    │  (stock vs AI, guard) →   │
│ Speech card: keyterms → topics, TTL │  swap held/unchanged)          │  voice → video            │
├─────────────────────────────────────┴────────────────────────────────┴───────────────────────────┤
│ Timeline: per moment, hot steps (latency trace) + warm jobs (dev.job) as bars on one time axis    │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Event log: every frame, type filter, pause, click → JSON, "Export session" (→ evals/scenarios/)  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Timeline** is the core debug view. For each moment it draws
  `measured_at → received_at → cleared_at → ready_at → displayed_at` (hot path) and the
  warm bars (VLM, topics, copy, voice) that upgraded it. A slow warm job is easy to
  spot, and a hot step that waited on one stands out at once.
- **Export session** downloads the last N minutes of non-sensitive events as JSON
  (no keyframes). It is the starting point for a scenario in `evals/scenarios/`,
  per CLAUDE.md ("a bug found in a rehearsal becomes a scenario first").
- It follows `web/README.md`: pure reducer (`dev-state.ts`, ring buffers with fixed
  windows), external store, each panel in its own `Panel` error boundary,
  `StatusBadge` for status, closed design tokens, empty states, and last data
  kept during a reconnect.
- **Reuse, don't fork:** move `MomentPanel`, `AuctionPanel` and `LatencyPanel` from
  `ConsolePage.tsx` into `features/console/panels/` and export them. `/dev` imports
  them through `@/features/console`. The dev reducer composes `consoleReducer`
  for the `ConsoleOutbound` half.

## 7. Phases

Each phase ships on its own and is useful alone.

| # | Scope | Backend | Web | Done when |
|---|---|---|---|---|
| **0** | Skeleton | `/ws/dev` + `Hub._devs` + prod gate; `DevOutbound` union (empty plus relayed console) | `features/dev/` route, reducer composing console state, top bar, panels reused from console, **on-air mirror**, event log | `/dev?screen=demo-01` shows the same moment/auction as `/console` plus the mirrored ad and a raw log, in replay mode |
| **1** | Generation trace | `dev.job` (WarmWorker observer), `dev.creative`, `dev.player` | Build trace panel, timeline (hot + warm) | In a replay, each creative shows its brief, copy source and guard verdict, and the timeline shows the AI copy and voice bars landing after `displayed_at` |
| **2** | Perception | `dev.perception` from edge (`publisher.py`, rate limiter, `EDGE_DEV_STREAM`), relay in `ws.py`; `dev.vlm`, `dev.speech` | Box canvas, track table, VLM card with kept/dropped, speech card | Live camera: boxes track people at ≥ 4 Hz with < 300 ms lag; holding the Café Patio marker shows a held object *and* a VLM `held_products` row |
| **3** | Depth (optional) | Keyframe preview flag (§5), `dev.auction_candidates` | Keyframe underlay, auction history strip, session export | Rehearsal bug reproduced from an exported session as an eval scenario |

## 8. Guardrails and tests

- **Hot path stays clean.** Every dev emitter is fire-and-forget through
  `broadcast_dev_threadsafe`, after a `dev_count()` check. No dev code is awaited
  inside `decide`/`run_auction`/`publish`. Add a unit test: with a dev sink that
  blocks forever, the server decision time stays under 30 ms.
- **Privacy:** add an architecture test that no `Dev*` model has a field named
  `text`, `transcript`, `utterance`, `jpeg`, `image` or `embedding` (keyframe bytes,
  if enabled, travel only as a binary frame behind the flag). `reasoning` stays
  capped at 280 chars by the tagger adapter, whose prompt limits it to objects and context.
- **Prod:** `tests/unit/api/`: `/ws/dev` refuses in prod; the backend drops
  `dev.perception` in prod, and the edge defaults `EDGE_DEV_STREAM` off unless set.
- **Contract:** `make contract`; the existing drift check covers it.
- **Web:** vitest for `dev-state.ts` (ring buffers, moment grouping for the
  timeline, mirror ignores stale upgrades the same way `player-machine` does) and a
  render test for each panel's empty state. `make web-check`.
- **Evals:** this is not a loop change, but phase 1 touches `demo.py`, so run
  `make eval` before and after and confirm latency ratchets do not move.

## 9. Open decisions

1. **Keyframe preview (§5):** ship it behind a flag, or schematic only? This is
   the one item that touches a product invariant.
2. **Auth on `/ws/dev` outside localhost:** the Render deploy runs with
   `app_env=prod`, so `/dev` is local-only. If the team wants it on the public URL
   for remote QA, it needs a token first (same gap as `GET /recognition`).
3. **`recognition.snapshot` vs `dev.perception`:** the replay already emits
   `recognition.snapshot` with boxes. We could make the live pipeline emit it too
   and have the dev view read that instead of adding `dev.perception`. Not
   recommended: it is a classifier read model with session history (heavier and
   slower), while `dev.perception` is a thin per-frame view. Revisit if they drift.
