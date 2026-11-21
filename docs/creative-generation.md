# Creative generation: targeted co-brand ads in real time

> **Doc status: LIVE.** Design, metrics plan and runbook for the `creative`
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
   stock draft only if it passes the guard **and** scores at least as high.
   Quality can't regress on air because of a bad sample.
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
by attention-seconds and redemptions, distance-aware layouts, hero images.

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

## 6. Runbook

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
