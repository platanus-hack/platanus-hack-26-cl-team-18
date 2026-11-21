# Evals: the product feedback loop

Unit tests prove a function. **Evals prove the product.** They drive the real
loop (real backend, real WebSockets, real auction and creative path), score
what the screen and console actually received, and compare it with a
committed baseline. Every change then shows its effect on demo outcomes,
invariants, KPIs and latency, and an improvement is locked in so it can't
silently regress.

```
change code ──► make eval ──► evals/results/latest.md ──► better? make eval-baseline (commit it)
     ▲                                  │                    worse? CI fails, fix it
     └──────────── read the scorecard ◄─┘
rehearsal ──► make eval-record ──► make eval-rehearsal ──► new scenario JSON for what broke
```

## 1. Layers

| Layer | What it proves | Mocks | Where | Runs |
|---|---|---|---|---|
| Domain unit tests | One rule, one table row | Fake clock/ids | `tests/unit/**` | `make test`, CI |
| **Auction properties** | Invariants hold for *random* books, not only hand-written ones | None | `tests/unit/auction/test_bundle_properties.py` | `make test`, CI |
| **Scenario evals** | Each demo beat and backup moment, end to end | **None** (live uvicorn on loopback) | `evals/scenarios/*.json` | `make eval`, CI |
| **Soak** | No missing creative, invariants and latency under a fast random walk | None | `python -m evals --soak N` | `make eval`, CI |
| **Perception on real video** | The agent counts people, sees held products and flags minors on recorded clips | **No mocked frames** | `make eval-perception` | On purpose, before demo |
| **Real-video replay** | Real perception output through the live backend: flicker, fill, latency | None | `make eval-replay` | On purpose |
| **Rehearsal scoring** | What actually happened in a run-through, glass to glass | None | `make eval-record` / `make eval-rehearsal` | Every rehearsal |

### Known-gap ledger (strict xfail)

A property the engine *should* satisfy but doesn't yet is written as a test
marked `xfail(strict=True)` that cites the requirement. Today:

- **R-P5/R-P6** (`docs/auction-plan.md`): price per AS can exceed the bid
  when several viewers match, because the price is money per screen-second.
- **R-P4**: the reserve floor can charge a winner more than its own slot
  value. Pinned by `reserve_floor_case()`: two CLP 15 bids win a duo on
  affinity and each pays CLP 10 for a slot worth 7.5.

When the fix lands the test XPASSes, strict mode fails the build, and the
fixer deletes the marker. A fixed gap can never come back unnoticed.

## 2. The scorecard

`make eval` writes `evals/results/latest.md` (also the CI job summary and an
artifact), a timestamped JSON, and appends `history.jsonl`, so the scorecard
shows the trend across runs. Results are gitignored. Only `evals/baseline.json`
is committed.

| Kind | Rule | Metrics |
|---|---|---|
| **Gate** | Absolute bound. It never moves; failing one fails the run. | invariant violations = 0, scenario checks failed = 0, soak missing creatives = 0, server decision p95 ≤ 30 ms, agent→player round trip p95 ≤ 100 ms, glass-to-glass p50 ≤ 1.5 s / p95 ≤ 2.5 s, perception minor windows missed = 0 |
| **Ratchet** | Versus `baseline.json`, in its declared direction. Worse fails; better asks for `make eval-baseline`. | scenario pass rate, fill rate, basket rate, mean coverage, basket value lift, cleared revenue / single-brand value |
| **Info** | Tracked and shown with history, never judged against the baseline. | p50/max latencies, exclusions by reason, brands per play, throughput |

Latency is gated absolutely, not against the baseline, because wall-clock
time varies by machine. Product metrics of the scripted suite are
deterministic, so their tolerance is effectively zero.

**Invariant oracles are independent of the engine.** `evals/metrics.py`
re-derives "competitors together" (from bid categories), "minor targeted"
(from its own restricted-category list), "screen disagrees with auction",
"blank screen", negative price and out-of-order traces from the wire
messages. A bug that drops the engine's own `age_restricted` flag is still
caught.

### Baseline (2026-11-21, `e880589`)

All 6 scenarios and 62 checks pass, with 0 violations. Server decision p95 is
about 0.6 ms and the loopback round trip p95 is about 7 ms. Coverage is 1.0
and basket value lift is +20 %. **Cleared revenue is 0.46× the single-brand
value**: auction-plan §2.1 gap 1, now a number that should go up when the
pricing fix lands. Glass-to-glass is `n/a` until the player reports
`displayed_at` (see §6).

## 3. Add a scenario (a data change)

Scenes are named in `evals/scenarios/scenes.json`: structured perception
facts whose labels match the simulator's fixture catalogs. A scenario is a
timeline:

```json
{
  "name": "basket-ad",
  "beat": "2. Basket ad",
  "description": "…",
  "steps": [
    {"scene": "empty", "expect": {"brands": []}},
    {"scene": "co-brand", "hold": 3, "expect": {
      "brands": ["Voltio", "Café Patio"], "layout": "duo", "min_coverage": 1.0,
      "copy_contains": "$3.490", "excluded": {"Rayo": "competitor"}}},
    {"scene": "coffee", "measured_offset_s": -11, "expect": {"no_change": true}}
  ]
}
```

Expectations: `brands` (set; `[]` = house ad), `layout`, `excluded`
(brand → reason), `min_coverage`, `copy_contains`, `no_change` (nothing
arrives within 250 ms). `hold: n` repeats the scene n times and expects no
re-auction. `measured_offset_s` simulates stale facts or a fast device clock.
Run a single scenario with `make eval ARGS="--only basket-ad --soak 0"`.

**Every bug found in a rehearsal becomes a scenario** before it is fixed.

## 4. Real video, no mocks

Recorded clips of consenting teammates are the golden set for perception.
Invariant 3 is about production capture. These are test assets made with
consent, and they still **never enter git**: `evals/perception/videos/` and
`evals/perception/recordings/` are gitignored. Share them over the team drive.
The label file is committed and pins the clip by `sha256`.

**Label** (`evals/perception/labels/<clip>.json`), by time window:

```json
{"clip": "trio-walk-in.mp4", "sha256": "…", "consent": "team-18 members, 2026-11-21",
 "windows": [
   {"start_s": 0,   "end_s": 3,  "people": 0, "looking": 0},
   {"start_s": 3,   "end_s": 9,  "people": 3, "looking": 3, "objects": ["bottle"]},
   {"start_s": 9,   "end_s": 14, "people": 3, "looking": 2, "objects": ["bottle", "cup"]},
   {"start_s": 14,  "end_s": 18, "people": 2, "looking": 2, "minor": true}]}
```

**Agent replay contract** (for the vision adapter in `edge/agent/vision`):
running on a file instead of the webcam, the agent writes one JSONL line per
snapshot it would have sent, `{"t_s": <seconds since clip start>, "snapshot":
<AudienceSnapshot>}`. That covers demo insurance §4.2 ("replay mode") and
the eval input in one feature. Then:

```sh
make eval-perception LABELS=evals/perception/labels/trio-walk-in.json REC=evals/perception/recordings/trio-walk-in.jsonl
make eval-replay REC=evals/perception/recordings/trio-walk-in.jsonl
```

Perception scores: people exact rate and MAE, looking MAE, change-detect
delay p50/p95 (label change → first matching snapshot), changes missed,
held-object recall, false objects per sample, minor false-alarm rate, and
**minor windows missed (a gate: must be 0)**. A window is scored 0.5 s after
it starts (the debounce settles). The replay adds flicker (brand swaps per
minute) on top of the decision KPIs.

**Minimum golden set** (one clip per demo risk): empty → trio walks in;
someone picks up the Grano cup (held product); passer-by walks past without
looking (must not sell); a kid joins a beer moment (safety); the group turns
away (attention → $0); dim room; a queue of 5 or more. Record on the demo
webcam at the demo settings (`docs/stack.md`).

## 5. Rehearsals are data

```sh
make dev                       # backend (DEMO_SIMULATOR=true or live agent)
make eval-record OUT=evals/results/rehearsal-3.jsonl   # Ctrl-C when done
make eval-rehearsal REC=evals/results/rehearsal-3.jsonl
```

The recorder joins as one more player and one more console (the hub fans
out, so the real screen is untouched) and stores wire messages only: no
frames, audio or transcripts. Its scorecard has the same KPIs, invariant
oracles and latency as CI, plus flicker. Compare rehearsals 1 to 5 to see
whether the demo is getting tighter. The same numbers can close the pitch
("during this pitch: 14 moments, 9 baskets…", `docs/demo.md` §7).

## 6. Next, by value for the demo

1. **Player reports `displayed_at`** (in `playback.report`, or a small
   `creative.displayed` ack): it switches on the glass-to-glass gate, the
   headline latency claim. Until then it shows `n/a`, never a made-up number.
2. **Vision replay mode** in `edge/` (§4 contract) plus the golden clips.
3. **Copy guard evals:** a fixed set of recorded LLM responses (competitor
   names, invented claims, changed prices) that the copy validator must
   reject. Recorded fixtures, never live calls in CI.
4. **Visual regression of the player:** headless Chromium screenshots per
   scenario layout, so a broken logo or overflowing copy fails before the
   judges see it.
5. **Chaos scenario:** kill the GPU service mid-run and measure failover time
   and "never blank" (ADR 0007); unplug test as a number.
6. **Per-step trace budgets:** when a step gets its own timestamp (warm copy,
   TTS, video upgrade), add its p95 as a gate here in the same PR.
