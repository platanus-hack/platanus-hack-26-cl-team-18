# Creative generation: targeted co-brand ads in real time

> **Doc status: LIVE (implemented).** Design, metrics plan and runbook for the `creative`
> context's generation engine. Binding rules come from
> [`product.md`](product.md) (invariants 1, 2, 4, 5), [`architecture.md`](architecture.md)
> §3/§6.1/§7.3/§8 and ADR 0001 (three paths) / ADR 0003 (video). The code is in
> `app/modules/creative/`; the eval is `make eval-creative`.

## 1. What has to be true for the wow moment

The ad is the only thing judges *see*. It lands when, within a second of a
change in front of the screen, the copy obviously answers the moment:

| Moment in front of the screen | What the screen must say (and why the judge believes it) |
|---|---|
| Someone says "uf, qué hambre" | A **food** angle: *"¿Hambre? Café Patio + Miga…"* (it heard me) |
| Four people looking | An offer **for four**, priced for four: *"Para los 4: Pack x4 por $11.990"* |
| One person holds a Café Patio cup | **Complete the basket**: *"Tu Café Patio pide una Miga"* |
| Gym bottle, morning | A **post-workout** angle with the approved combo price |
| A kid in the group | No alcohol words, ever (invariant 2) |
| A queue forms | A **queue** angle: *"Fila de 2 min: pide tu Café Patio"* |

And it must stay true when generation is slow, wrong or broke: never a wrong
price, never a competitor name, never a blank screen, never an uncapped bill.

## 2. Ideas, ranked by what they buy the demo

1. **Deterministic targeting first, AI second (the core design).** Targeting
   precision must not depend on a network model. A pure **angle policy** maps
   the moment (explicit speech intent > held product > queue > top segment) to
   an `Angle` (hunger, energy, coffee break, post-workout, social, queue,
   complete-the-basket), and a pure **offer book** picks the basket offer for
   the exact **party size**. The hot path renders *targeted stock copy* from
   templates for that angle and party in < 1 ms. AI copy can only make it
   better; it cannot make it less targeted, because the brief is fixed before
   the model is called.
2. **A creative brief as the single contract.** Every generator (stock
   templates, Claude, a recorded fixture, a future fine-tuned model) takes the
   same frozen `CreativeBrief` and returns the same `CopyDraft`. Swapping or
   A/B testing a generator is one line in the composition root, and every one
   is scored by the same eval.
3. **A copy guard that is a specification, not a prompt.** Pure rules check
   every draft before it can air: price tokens must equal the approved price,
   no competitor or out-of-basket brand names, every basket brand is named
   (co-brand fairness), party-size words agree with the offer, no
   age-restricted words when a minor is likely, no forbidden inferences or
   unapproved claims, per-stage length limits for readability at distance.
   A rejected AI draft falls back to stock copy; the guard's verdict is stored.
4. **Best-of selection with a quality score.** The same deterministic
   `score_draft` that the eval uses runs at runtime: an AI draft replaces the
   stock draft only if it passes the guard, is **not worse on any targeting
   part** (angle, party, brands, price, held product) and its total is within
   `ai_tolerance` (0.05) of stock's. The rubric measures targeting and
   safety, not wit, so a fresher line may trail by a hair but never miss the
   moment. Quality can't regress on air because of a bad sample.
5. **Progressive upgrade in place (three paths).** Hot: cache or stock →
   `creative.ready`. Warm: Claude Haiku 5.5 copy (structured output, frozen
   cached system prompt) → guard → score → `creative.upgraded` (kind `copy`),
   then the voice-over of the new line. Stale results (the moment moved on)
   are dropped.
6. **Copy cache keyed by brief signature + cold prewarm.** The signature is
   (basket, party bucket, angle, held, minor, daypart). Prewarming the demo's
   briefs before the demo turns every live moment into an AI-quality cache
   hit at hot-path speed. Speculative warm-up of likely briefs is the same
   call.
7. **Cost-aware generation by ROI.** Each upgrade kind has a cost estimate
   (`CostModel`: tokens × price, TTS characters, image/video per call).
   `plan_upgrades` spends only when the estimate is below a fraction of the
   moment's expected revenue (bundle value per second × expected dwell) and
   the sliding hourly budget allows it. Copy (~$0.0002) almost always passes;
   a video upgrade (~$0.30) only for a valuable moment with no cached loop.
   Every spend is recorded in an in-memory generation ledger.
8. **Dwell stages carried in the spec.** The spec carries `teaser` → `offer`
   → `close` copy, a structured `offer` (label, party size, price) and the
   `angle` and copy source, so the player and the console can show *why this
   ad* and the eval can check each stage.

Backlog (good, but not before the demo works): an LLM judge for quality on
live runs (port is in place: `QualityJudge`), a bandit over variants rewarded
by attention-seconds and redemptions, distance-aware layouts, warm hero-image variants.
Bundled product photography and campaign drafting are implemented in §11.

## 3. Pipeline

```
AudienceMoment + AuctionResult + BrandKits + OfferBook + context
        │  build_brief()  (pure, < 0.1 ms)
        ▼
CreativeBrief ──► angle policy · offer for party size · competitors to avoid · stage limits
        │
  HOT   ├─► CopyCache.get(signature) ──hit──► CopyDraft (AI quality, cached)
        │                             └miss─► StockCopywriter.compose(brief)  (templates)
        │   render_spec() → CreativeSpec(slots, stages, offer, angle) → creative.ready
        │
  WARM  └─► plan_upgrades(brief, value, budget)  → [copy, voice, (image), (video)]
              copy:  CopyWriter.write(brief) ─► CopyGuard ─► score ≥ stock? ─► cache
                     ─► creative.upgraded(copy) ─► voice-over of the new line
              cost:  GenerationLedger.record(kind, estimate)  (hourly budget)
```

Groups larger than 6 are addressed as "todo el grupo", never with a wrong
count. Composition is memoized per brief (about 2 µs on a repeat, about 1 ms
for a never-seen brief), and the memo is dropped when new AI copy is cached.

Layering: `domain/` is pure (brief, angle, offer, guard, score, cost,
budget). `application/` holds the hot composer, the studio orchestration,
the copy cache and the ports (`CopyWriter`, `QualityJudge`).
`infrastructure/` holds `ClaudeCopywriter` (via the `ai` module's
`AIService`), `RecordedCopywriter` (fixture replay) and the stock templates
live in `application/` because they are deterministic product copy.

## 4. Metrics plan

Four families, each with a **gate** (absolute; failing it fails the run) or a
**ratchet** (vs `evals/creative/baseline.json`; worse fails, better asks for
`make eval-creative-baseline`), plus info metrics.

### 4.1 Targeting and precision (the "no errors" family)

| Metric | Definition | Kind |
|---|---|---|
| `angle_accuracy` | Share of cases where the chosen angle equals the labelled one | gate = 1.0 on the suite |
| `offer_accuracy` | Share where the offer's party size and price equal the labelled ones | gate = 1.0 |
| `party_mention_rate` | Share of party ≥ 2 cases whose aired copy names the group ("para los 4", "Trío") | ratchet ↑ |
| `must_contain_rate` | Labelled required tokens (price, partner name, intent word) present in aired copy | ratchet ↑ |
| `must_not_violations` | Labelled forbidden tokens (competitor, alcohol with minors) present in aired copy | gate = 0 |

### 4.2 Safety (what can air)

| Metric | Definition | Kind |
|---|---|---|
| `aired_guard_violations` | Guard violations in the copy that would air after fallback | gate = 0 |
| `adversarial_rejection_rate` | Share of recorded bad model outputs (changed price, competitor, invented claim, wrong party, alcohol + minor, too long) the guard rejects | gate = 1.0 |
| `fallback_rate` | Share of AI drafts replaced by stock (rejected, failed, timed out or lower score) | info |

### 4.3 Quality

`score_draft` (pure, 0..1) is a weighted rubric: guard pass (hard 0 if it
fails), angle keyword hit, party fit, brand coverage, price present in the
offer stage, readability (words per stage vs limits), stage distinctness.

| Metric | Kind |
|---|---|
| `quality_mean` (aired) | ratchet ↑ |
| `quality_min` (aired) | gate ≥ 0.6 |
| `ai_win_rate` (AI draft beat stock on the score) | info |
| LLM-judge score (live runs only, `--judge`) | info |

### 4.4 Latency and cost

| Metric | Definition | Kind |
|---|---|---|
| `hot_compose_p95_ms` | brief + cache/stock + spec render | gate ≤ 5 ms |
| `warm_copy_p95_ms` | writer call → guarded draft (recorded latencies offline, real ones live) | gate ≤ 2500 ms |
| `cache_hit_rate` | Second pass over the suite after prewarm | ratchet ↑ |
| `cost_per_creative_usd` | Ledger spend / creatives | ratchet ↓ |
| `cost_per_1k_moments_usd` | Projection for the pitch | info |
| `budget_denials` | Upgrades refused by ROI or the hourly cap | info |

Offline (`--writer recorded`, the default and the CI mode) is deterministic:
the recorded latencies and outputs are fixtures, so the numbers only move
when code or fixtures move. `--writer claude` runs the same suite live
(costs real money; run on purpose, never in CI).

## 5. Testability

- Everything is a pure function of a `CreativeBrief` except the writer call.
- The `CopyWriter` port has three adapters: stock (deterministic),
  recorded (fixture JSON with latency and token counts), Claude (live).
  Tests and CI never call a live provider.
- Time comes from an injected clock; the ledger and budget take `at`.
- Table-driven unit tests: angle policy, offer book, guard (one row per rule),
  scorer, cost/budget, studio (fallback on reject, on error, on lower score,
  stale drop, cache).
- `tests/unit/creative/test_creative_eval.py` runs the offline eval and
  asserts every gate, so a regression fails `make check`.

## 6. Implementation map

| Piece | File |
|---|---|
| Angle policy (speech > held > queue > segment > category) | `app/modules/creative/domain/angle.py` |
| Group-sized offers, CLP price text | `domain/offer.py` |
| `CreativeBrief`, signature, stage limits | `domain/brief.py` |
| `CopyDraft`, stages, provenance | `domain/draft.py` |
| Copy guard (`CopyGuard` spec, `check_copy`) and word lists | `domain/guard.py`, `domain/lexicon.py` |
| Quality rubric `score_draft` | `domain/scoring.py` |
| `UsdCost` (micro-dollars), `CostModel`, ledger, `plan_upgrades` | `domain/cost.py` |
| Brief from a cleared auction (`build_brief`, `assemble_brief`) | `application/briefing.py` |
| Targeted stock templates | `application/stock_copy.py` |
| Studio: hot compose, warm upgrade, prewarm, records | `application/studio.py` |
| Copy cache, spec rendering | `application/copy_cache.py`, `application/rendering.py` |
| Ports `CopyWriter`, `QualityJudge` | `application/ports/copywriter.py` |
| Claude Haiku 5.5 adapter (frozen prompt, typed failures) | `infrastructure/claude_copywriter.py` |
| Recorded replay adapter | `infrastructure/recorded_copywriter.py` |
| Demo wiring (seed offers, warm `creative.upgraded(copy)`) | `app/bootstrap/demo.py`, `app/bootstrap/container.py` |
| Player: stages + offer badge | `web/src/features/screen/CreativeView.tsx`, `stages.ts` |
| Eval suite, fixtures, runner, baseline | `evals/creative/` |

Wire contract (additive, optional): `CreativeSpec.stages[]`, `offer {label,
text, price, party_size}`, `angle`, `copy_source` (`stock|ai|cache`).

### Baseline (2026-11-21)

| Mode | Angle | Offer | Aired violations | Adversarial rejected | Quality mean / min | Hot p95 | Warm p95 | Cost / creative |
|---|---|---|---|---|---|---|---|---|
| stock | 21/21 | 21/21 | 0 | 15/15 | 0.982 / 0.964 | < 2 ms | n/a | $0 |
| recorded | 21/21 | 21/21 | 0 | 15/15 | 0.978 / 0.948 | < 2 ms | 1100 ms | $0.00017 |

In recorded mode, 16 of 20 AI attempts air; the changed-price draft is
rejected, the bland one loses on score, and two provider failures (invalid
output, timeout) fall back to stock. The recorded responses are hand-written
stand-ins in Claude's output shape. Replace them with real ones via
`--writer claude --record` before quoting AI numbers in the pitch.

## 7. Runbook

```sh
make eval-creative                       # offline: stock + recorded writer, scorecard
make eval-creative ARGS="--writer claude" # live Claude copy (needs ANTHROPIC_API_KEY; spends)
make eval-creative-baseline              # lock improved ratchets
```

Scorecard: `evals/results/creative-latest.md` (gitignored). Add a case in
`evals/creative/cases.json` (a data change). Add an adversarial model output
in `evals/creative/adversarial.json`. Live copy in the demo turns on with
`CREATIVE_AI_COPY=true` plus `ANTHROPIC_API_KEY`; without them the demo
runs on targeted stock copy, which already passes every targeting gate.

## 8. Ad-loop latency investigation (2026-11-21)

The measured bottleneck was browser readiness, after backend composition.
Ranked hypotheses and findings:

1. **Optional media gates the swap:** confirmed. The preloader awaited optional
   image/video/audio promises after logos decoded; video/audio could each
   hold readiness until the 3000 ms timeout. Removing that gate removes this
   delay even if optional media never responds.
2. **Voice preparation shares the visual preload lifecycle:** confirmed by the
   call chain. Separating it allows late audio to play after the visual swap,
   and cancellation prevents a superseded voice from playing over a new ad.
3. **Duplicate logo URLs cause redundant decodes:** confirmed by a failing
   regression (two calls for the same URL). Decode distinct URLs once per ad.
4. **Server composition dominates:** unsupported by the measurements below.
   Keep the existing stock/cache hot path and guarded warm AI upgrades.

| Measurement | Before | After |
|---|---:|---:|
| Live server decision p95, six scenarios + 200-step soak | 2.704 ms | 1.289 ms |
| Agent → player message p95, same eval | 15.0 ms | 7.866 ms |
| Offline creative hot composition p95 | 1.267 ms | 1.031 ms |
| Chromium stalled-media POST → visible, median of 5 fresh pages | 3506.6 ms | 285.4 ms |
| Chromium stalled-media POST → visible, observed range | 289.4–3520.7 ms | 275.9–313.1 ms |
| Simulated agent snapshot → live auction → Chromium visible | — | 287.5 ms |

The browser comparison served an isolated copy of the original player and
the changed player through Vite, against the same live uvicorn backend.
Each trial opened a fresh Chromium page, pushed a creative via `/dev/push`
with a valid inline logo and unique hero/video/voice URLs, and intercepted
those optional URLs without returning a response. Timing ended when the
creative occupied the front layer (`aria-hidden="false"`), after the 250 ms
crossfade. This is a small local fault-injection sample, not a production
percentile. The lower backend timings are normal run-to-run variation; no
backend optimization is claimed. No product ratchet changed, so the existing
baseline stays valid (latency has absolute gates).

Regression coverage in `web/src/features/screen/preload.test.ts` and
`ScreenPage.test.tsx` checks visual readiness despite stalled optional media,
logo failure/timeout, superseded preloads, cleanup, duplicate logos, late
voice playback, stale voice cancellation, audio failure and video visibility
before readiness/after failure. Run it with
`cd web && npm test -- src/features/screen/preload.test.ts src/features/screen/ScreenPage.test.tsx`.
The fake-timer swap test gates the full 250 ms crossfade when logos decode
immediately; it is independent of machine scheduling. `make eval` and
`make eval-creative` still verify the actual backend loop and creative guards.

The remaining physical latency includes camera capture, remote inference,
edge publication/debounce, browser scheduling, logo downloads and the TV.
Those require a hardware rehearsal. The eval's glass-to-glass metric remains
unavailable until the player reports `displayed_at`; the browser measurements
above do not fill that missing production trace.

## 9. Upgrade latency and connection races (2026-11-21)

The next investigation tested three ranked hypotheses: already-visible copy
and voice upgrades unnecessarily repeat decoding/crossfade; upgrades can
replace a newer queued creative; and retired WebSocket callbacks can create
duplicate connections after a stop/start cycle. Regression tests reproduced
the real React upgrade failure and all three socket lifecycle failures before
implementation. The player was discarding the distinction between
`creative.ready` and `creative.upgraded`.

Upgrades now patch the matching front, back or queued creative. A visible
upgrade retains the same layer, mounted video, dwell stage and play window.
It does not run another required-logo decode or 250 ms transition. Repeated
ready messages for a known compatible creative also reuse that layer. The
auction id, brand ids, slot order and logo URLs must match: an incompatible
upgrade is ignored, and a new ready message with changed assets still takes
the ordinary preload path. Retired upgrades are dropped; upgrading the
fading creative preserves any newer queued ad.

The preload phase retains its original request while a compatible upgrade
changes the back layer's copy. A regression found that depending on the whole
back-layer object restarted decoding on an upgrade (two decode calls instead
of one); retaining the request prevents warm work from delaying hot readiness.
A superseded preload's completion also checks its abort signal before it can
advance the player.

`TypedSocket` now treats start as idempotent through connecting, open and
backoff states. Stop invalidates the connection before close, and open,
message and close callbacks verify connection ownership. Tests inject late
callbacks after restart and verify that no stale frame or extra reconnect
escapes. This applies to the console as well as the screen.

A local Chromium comparison against the same real backend pushed five copy
upgrades with unchanged brand assets, through `/dev/push` and actual player
WebSockets. A MutationObserver measured inbound upgrade message to updated
text in the visible front layer:

| Measurement | Original player (`dfb9523`) | Changed player |
|---|---:|---:|
| Five observed upgrade latencies (ms) | 252.0, 480.4, 251.9, 495.8, 252.1 | 4.4, 1.7, 2.5, 1.6, 2.4 |
| Median (ms) | 252.1 | 2.4 |
| Mounted video retained through every upgrade | No | Yes |
| Uncaught browser errors | 0 | 0 |

The changed player also ignored a late upgrade for a retired creative.
These are local DOM-update measurements, not camera-to-TV or physical paint
percentiles. The original can repeat a transition when lifecycle races
deliver duplicate messages; the changed player avoids both duplicate
connection callbacks and repeat transitions. No server timing improvement is
claimed by these frontend changes.

Regression checks cover pending, visible, fading and queued creatives;
required-asset/auction mismatches; continuous billing windows; preservation
of video and dwell stage; decode continuity; stale upgrades; idempotent start;
and stop/start/backoff races. Run `cd web && npm run check`. Verification also
ran the production build, both TypeScript compilers, the backend unit suite,
`make eval`, `make eval-creative`, and `make test-sandbox` (including provider
quota failure). Product ratchets in the loop eval were unchanged.

## 10. Preserve the basket and prioritize the current moment (2026-11-21)

A live backend/player regression found that rebuilding a whole spec for an
accepted AI copy upgrade dropped the QR, redemption code/URL and approved
video loop that the hot path had attached. The player then removed the
playing video, and the close stage lost its usable redemption. The existing
scenario suite passed because it did not assert these fields after an upgrade.

`render_copy_upgrade` now changes copy in the existing spec: stage text,
slot copy and provenance. The approved offer, real brand assets, hero images,
video, QR and redemption remain intact. Voice is resolved again against the
current spoken line through the guarded cache. A cross-basket copy upgrade
fails before it can change the screen.

A second probe occupied a provider slot, queued co-brand → fitness → adult,
and released it with 125 ms copy calls. The old code generated all three
briefs even though only the last one could air. Live copy and voice jobs now
check relevance **after acquiring the provider slot, before calling it**.
Copy matches the current brief signature; voice matches the current line and
voice profile. Returning to the same brief/line while queued still shares the
original job. Calls already running finish normally and may warm the cache;
they are not cancelled after a potentially billable request.

| Single-slot queue probe, five trials | Before | After |
|---|---:|---:|
| Current audience's copy wait, median | 385.11 ms | 128.74 ms |
| Provider calls per three-moment sequence | 3 | 1 |

This is a controlled ASGI/provider-queue measurement, not a live-provider
percentile. The deterministic regression checks call counts and ownership,
not machine-dependent timing. It also checks a moment that returns while
queued and a running call that completes after departure and warms a return.

The investigation also found `ready_at` was captured before redemption
issuance, QR encoding and loop lookup. It is now captured after those steps.
A fake-clock test adds 40 ms inside QR composition and requires all 40 ms to
appear in the trace. Reported decision p95 in the live scenario/200-step soak
was 6.483 ms after the fix, under the 30 ms gate; the earlier 1.993 ms reading
omitted this work. This timestamp correction is not a speed regression.

Browser verification used real uvicorn/WebSockets, Chromium, an 800 ms local
copy provider and a generated H.264 fixture loop. It verified that AI copy
keeps the same playing video element with zero additional video requests,
then advanced to the close stage and decoded the QR. Redeeming its unchanged
code credited both brands; retrying did not create another redemption. There
were no uncaught browser errors. These checks do not measure live model,
remote-GPU or physical TV latency.

Validation: backend lint/format/types and unit suite; 72 web tests plus build;
scenario/soak eval; creative eval; pipeline journeys; and both process-level
speech/voice sandbox scenarios. The pipeline run completed copy+voice at
1055 ms p95 in generation mode and 13 ms p95 after warm-up (socket delivery,
recorded copy timing and sandbox TTS). Product ratchets did not change, so the
existing baselines remain valid.

## 11. Campaign studio, photography and media evaluation

`/campaigns` drafts creative packs from the **same live seed catalog** as the
auction. Three starter campaigns cover a post-workout group, a coffee break
and completing a held coffee purchase. Operators select one or two brands,
party size, moment, minor protection and photo/video. Only mutual partners
with an approved offer can form a pair; competitors, duplicate/unknown brands
and age-restricted campaigns with a minor are rejected. Completing a basket
requires a pair. Prices remain the approved offer tier, including the generic
group wording when the tier does not cover the exact group size.

`GET /api/campaigns/catalog` returns the available kits and approved pairs.
`POST /api/campaigns/generate` reuses `assemble_brief`, `CreativeStudio`, the
copy guard, scorer and renderer. Composition is synchronous, memoized with a
128-entry bound and independent of model providers and file IO. Reordered
brand selections share the same cached draft. Responses include the spec,
quality score, checks and composition timing. Export downloads a JSON draft;
it does not activate campaigns, spend a budget or issue a live QR.

The player and studio share `CreativeView`. Staged ads now show product
photography behind editable brand, copy, approved-price and QR overlays. The
layout uses container units so the 16:9 preview scales with its frame. Optional
video reveals only when playable and leaves the photo underneath on failure;
existing required-logo readiness and in-place upgrade rules are unchanged.

Four illustrative product photographs (energy, coffee, energy + coffee, beer)
and four 6-second 720p H.264 photo-motion loops live in `assets/campaigns/`.
The built-in imagegen tool produced the unbranded scenery; no logo, price,
claim or QR was generated. Exact prompts and provenance are in
`assets/campaigns/prompts.json`. Images are 1600×900 WebP; the local ffmpeg
build adds a subtle periodic zoom, not synthesized product motion. Regenerate
those loops with `uv run python scripts/build_campaign_media.py` (or pass
`--ffmpeg /path/to/ffmpeg`). Hosted fal video generation remains available
through `scripts/generate_loops.py` with its explicit cost and budget caps.

The asset manifest is loaded at market construction, then looked up in memory.
Bundled assets are served from `/campaign-assets`; a venue's existing generated
loop in `/media/loops` takes precedence. The same media is attached to live
creatives, while stock or cached copy remains immediately ready. Generated
imagery is demo scenery, not a representation of an actual advertiser SKU.
Production brands still review the exported draft and supply their real kits.

`make eval-campaigns` now runs in CI. It exercises **576 valid combinations**
and **156 expected rejections**, combining group sizes 1/2/3/4/6/12, five
moments, minor protection, both media modes and every supported solo/pair.
It checks co-brand attribution, original kit logos, exact approved prices,
guarded quality ≥0.6, repeat-cache consistency, local photos/video and no live
redemption in a draft. Photos must be 16:9 and at least 1280 px wide, under
400 KB; videos must be H.264 MP4 under 1 MB. Composition p95 must be ≤5 ms.
The unit version excludes this wall-clock gate; the CLI/CI run enforces it.

Observed locally: 100% repeat-cache hits, mean copy score 0.9927, minimum
0.9639, composition p95 1.85 ms, four photos and four valid loops. These are
catalog/guard/readability measurements, not a claim of human-rated artistry.
The normal live scenario/300-decision soak and creative eval also passed with
unchanged product ratchets. Browser checks covered desktop and 390 px mobile
layouts, real 1280×720 video decoding, template selection, media switching,
JSON export and stage previews; no uncaught browser errors were observed.
Camera-to-TV latency and live-provider generation latency remain separate
hardware/provider measurements.

## 12. House style: copy that reads like an agency wrote it (2026-11-21)

The ad should read like copy from a senior retail copywriter, not a chatbot.
Stock templates, the Claude prompt and the guard all follow the same rules, so
an AI draft can only replace stock copy if it keeps them.

**Copy rules**

| Line | Rule | Example |
|---|---|---|
| teaser | A statement of 6 words or fewer that makes the angle obvious. Two beats work well. The daypart sharpens it. | "Entrenaron. Ahora recarguen." · "El primer café del día." · "Pilas bajas. Recarga aquí." |
| offer | The approved offer text (or label) **verbatim**, then the group, then the price at the end. | "Voltio + Café Patio x3 para los 3 por $8.490." |
| close | One instruction that matches the real redemption flow (scan, then *Canjear en caja*). | "Escanea y canjea en caja." |

- No question openers ("¿Hambre?"), exclamation marks, ellipses, em dashes or emojis.
- At most one Chilean idiom per ad ("pilas bajas", "bajón", "al paso").
- Filler never airs. `lexicon.CLICHES` lists it ("momento perfecto", "experiencia", "descubre",
  "irresistible", "disfruta", "te lo mereces"…). The guard rejects it as `CLICHE`; it also
  rejects `! ¡ … —` and `...`.
- The offer's quantities and items are the brands' claims. "Voltio + Café Patio x2" never becomes
  "Voltio más dos Café Patio". The guard rejects such a line as `OFFER_CHANGED`. Joining items with
  "+", "y" or "más" counts as the same text.

**Layout** (`web/src/features/screen/CreativeView.tsx`, `creative.css`), drawn from the Relvo
brand system:

- Three typefaces, one job each: Instrument Serif for the hook, Instrument Sans for products and
  the price, Geist Mono in caps for labels. All are self-hosted and preloaded for the kiosk.
- One accent, the lead brand's, used once per stage: on the price, or on the QR's edge.
- The price is the largest number on screen: a small "$", tabular digits. The offer stage drops the
  copy's trailing price (`splitOfferHeadline`) so the price isn't said twice. Only an exact match of
  the approved price at the end of the line is dropped.
- The co-brand lockup uses the real logos with "×" between partners. No filler labels.
- Distance-aware: far enlarges the hook and the price and hides fine print; near enlarges the QR.
- The accent must reach 4.5:1 contrast on the dark canvas (`accent.ts`). Otherwise the ad falls
  back to white, so a navy or deep-red brand colour never hides the price.
- The house ad and any spec without stages use the same composition: logo top-left over a thin
  accent rule, and the line large at the bottom. The kit's "Brand · " prefix is dropped, because
  the logo already names the brand.

**Targeting controls in the studio** (`CampaignGenerate`):

- `brands` order is slot order, so the first brand leads.
- `moment` adds `energy`, backed by IAB 997 Energy Drinks.
- `daypart` picks the hook.
- The preview can simulate viewer distance.

None of these changes the offer or its price.
