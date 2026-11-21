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
| **Stock-video ad fit** | On real footage of real people, the ad the screen chose *makes sense* for the scene, and a must-never brand never airs | **None** (CC stock clips → edge agent → live backend) | `make eval-video` | Before every demo, after any catalog or auction change |
| **Ad pipeline journeys** | The whole ad system end to end (auction → stock/cached copy → AI copy → voice-over → play → receipt), timed per stage, with the bottleneck named | Provider *timing* replayed (recorded Claude latencies, TTS knob); everything else real, incl. the ElevenLabs HTTP adapter against the sandbox server | `make eval-pipeline` | CI, and after any change to creative, voice or billing |
| **Demo video** | The five beats of [demo-video.md](demo-video.md) in order: Dúo → Trío → held cup → bidding war → close, QR redeem, receipt, charged $0. Every paid ad must be complete (AI copy + voice + video + logos + approved price + QR) | Same as the pipeline journeys: replayed Claude timing, sandbox ElevenLabs; no credits | `make eval-demo-video` | Before every rehearsal or recording, and after any loop change |
| **Rehearsal scoring** | What actually happened in a run-through, glass to glass | None | `make eval-record` / `make eval-rehearsal` | Every rehearsal |

### Known-gap ledger (strict xfail)

A property the engine *should* satisfy but doesn't yet is written as a test
marked `xfail(strict=True)` that cites the requirement. When the fix lands
the test XPASSes, strict mode fails the build, and the fixer deletes the
marker. A fixed gap can never come back unnoticed.

The ledger worked as intended on its first two entries: **R-P5/R-P6** (price
per AS above the bid) and **R-P4** (the reserve floor charging more than the
slot value, pinned by `reserve_floor_case()`) were found by the properties,
held as strict xfails, and fixed by the auction rework, which removed the
markers. The ledger is empty today.

The creative generation engine has its own offline suite (targeting,
safety, quality, latency and cost): `make eval-creative`, documented in
[`creative-generation.md`](creative-generation.md) §4. It runs inside
`make check` too (`tests/unit/creative/test_creative_eval.py`).

### Adversarial product checks

`tests/unit/api/test_product_adversarial_flow.py` exercises the real ASGI app
and sockets. Six two-screen journeys permute bid changes, stock-outs and
minor safety, then check reconnects, stale audience events, cross-screen
playback rejection, duplicate billing, concurrent QR redemption, expiry and
attribution to the original basket. Separate failure-injection cases verify
same-socket billing retries, skewed player clocks and repeated display acks.

`tests/unit/delivery/test_adversarial_recovery.py` injects failures before and
after ledger append. Billing and redemption retain the original record for
retry; append adapters deduplicate by their natural keys. A failed second
brand charge leaves every budget unchanged. Playback is capped at the server
receipt time, so future windows cannot bill unseen attention or suppress later
reports. Pending redemption retries can finish after expiry when the original
attempt was valid. These are in-process recovery guarantees.

`tests/unit/delivery/test_attention_adversarial_properties.py` adds 350 generated
examples. An independent half-second oracle checks crowd churn and the minimum
look; overlapping windows and retries check attention conservation, exact
agreement between budget spend and ledger charges, and budget caps. Player
component tests reject both ready messages and upgrades for another screen,
even when its creative id matches the visible ad.

Run the focused checks with:

```sh
uv run pytest tests/unit/api/test_product_adversarial_flow.py tests/unit/delivery
cd web && npm run test -- src/features/screen/ScreenPage.test.tsx
```

For broader evidence, run the full backend and web checks, `make edge-check`,
`make test-sandbox`, the Postgres integration tests,
`make eval ARGS="--soak 500"` and `uv run python -m evals pipeline --dwell 3`.
The sandbox runs real backend and agent processes against a local provider
stand-in; it does not validate a live paid provider or camera-to-TV latency.
Process-crash recovery is also separate: the current write-behind queue and
pending records are in memory, and a full queue can drop persisted writes.
Passing these tests does not establish durable recovery across a restart.

## 2. The scorecard

### Product performance targets

Use **fitting paid exposure on eligible moments** as the primary product
metric. Label attention and relevant ad opportunities independently on
representative venue footage. Report total paid share separately so an
increase in irrelevant ads cannot look like an improvement. Proposed targets
below are engineering goals for that venue set.

| Dimension | Target and measurement |
|---|---|
| Relevance | ≥95% fitting paid time and ≥80% of independently labeled eligible time covered by fitting ads. Report both per venue and across the corpus. |
| Safety and availability | Zero prohibited ads, off-screen asset leaks, or blank renders in safety and failure rehearsals. Measure confirmed minor vote to publication separately from the time it takes to acquire the vote. |
| Response time | Glass-to-glass p50 <1.5 s and p95 <2.5 s on hardware. Split capture → receipt, receipt → ready, and ready → displayed. Report intentional holds separately. Server decision p95 ≤30 ms. |
| Stability | Report unintended swaps per minute on stationary scenes and measure snapshots lost to stale timestamps or backpressure. Target zero unintended swaps in the stationary fixture. |
| Delivery integrity | 100% of completed paid plays have a receipt, no duplicate charges or redemptions, and attention error ≤5% against independent labels. |
| Warm generation | Complete ad p95 <3 s and warmed cache p95 <300 ms. Report upgrade acceptance, upgrades that arrive after a creative leaves, and provider cost per accepted upgrade with live credentials. |

For each report, include sample count, cold vs warm runs, device and provider
settings, and p50/p95 separately. Stock footage is useful for finding gaps.
It has no independent attention labels, so it cannot certify the eligible
coverage target. Use consenting venue footage with labeled arrivals,
attention, held products, and safety windows for that gate.

### Local performance investigation

- **Coverage:** eight stock-video recordings produced fitting paid exposure
  only in the café clip, at 78.6% of that clip. Of about 126.6 seconds of
  house play, 72.7 seconds had looking viewers without a classified segment
  or held product, 52.3 seconds had no reported attention, and 1.6 seconds
  reached auction no-fill with a target signal. The report now prints these
  reasons per clip. They are based on observed signals and are not venue
  ground truth. Sampling the raw detector output found no object detections
  in the tested gym-floor and supermarket frames. The removed market cue was
  a chair. This supports collecting better contextual evidence before
  relaxing auction eligibility or the looking heuristic.
- **Safety:** the same 10 fps edge fixture reported a confirmed minor block
  after 1,100 ms before the fix and after 600 ms with immediate publication.
  Clearing still requires a stable reading. The auction's existing
  immediate safety swap is covered by the scenario and adversarial tests.
- **Latency:** loopback scenario server p95 was 8.3 ms before and 10.4 ms
  after, inside the 30 ms gate. One local café browser run had 9 display
  acknowledgments, with p50/p95 of 393/547 ms from the snapshot frame to
  browser display. The browser readiness stage alone was 269/305 ms, which
  includes the crossfade. This sample does not include the time before the
  agent publishes a stable moment or the physical display scanout.
- **Inference:** a fresh 56-frame café recording measured 198/251 ms
  CPU inference p50/p95. Same-frame thread probes preserved person counts
  and object labels, but 4 threads had a p95 of 186 ms on the first run and
  271 ms on repeat, versus 198/270 ms at 8 threads. Two threads reached
  292 ms on the first run. Keep the current CPU configuration until the
  production device has a repeatable benchmark. Record mode now writes
  inference p95 and sample count. Live mode keeps no list of per-frame timings.
- **Generation:** the provider-timing replay measured 1.06 s complete-ad p95
  and 15 ms after warm-up. In the unwarmed run the recorded copy wait
  accounted for about 98% of median latency. This does not measure current
  Claude, ElevenLabs or fal service latency.

`make eval` writes `evals/results/latest.md` (also the CI job summary and an
artifact), a timestamped JSON, and appends `history.jsonl`, so the scorecard
shows the trend across runs. Results are gitignored. Only `evals/baseline.json`
is committed.

| Kind | Rule | Metrics |
|---|---|---|
| **Gate** | Absolute bound. It never moves; failing one fails the run. | invariant violations = 0, scenario checks failed = 0, soak missing creatives = 0, server decision p95 ≤ 30 ms, agent→player round trip p95 ≤ 100 ms, glass-to-glass p50 ≤ 1.5 s / p95 ≤ 2.5 s, perception minor windows missed = 0 |
| **Ratchet** | Versus `baseline.json`, in its declared direction. Worse fails; better asks for `make eval-baseline`. | scenario pass rate, fill rate, basket rate, mean coverage, basket value lift, basket revenue lift (Σ bundle_revenue / Σ single_brand_revenue − 1, same units) |
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

### Baseline (2026-11-21, after the auction rework, `d10b16e`)

All 6 scenarios pass, with 0 violations and 0 soak errors. Server decision
p95 is about 2 ms and the loopback round trip p95 is about 11 ms. Coverage is
0.97 (looking-viewer coverage, R-O4), basket value lift is +7.5 % (the unpaid
pair bonus is gone, R-P2), and **basket revenue lift is +4.7 %** against the
single-brand counterfactual. Before the rework the first baseline (`e880589`)
showed cleared prices at only 0.46× the single-brand value, which is how the
evals surfaced auction-plan §2.1 gap 1. Glass-to-glass is `n/a` until the
player reports `displayed_at` (see §6).

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
(brand → reason), `min_coverage`, `max_coverage`, `copy_contains`,
`no_change` (nothing arrives within 250 ms). Segment affinity
(`docs/architecture.md` §6 step 2) adds `matches` (brand → specs its bid line
must contain, each `"related"` or `"related:Coffee>Pastry/Doughnuts"`, i.e.
kind or kind:bid label>moment label), `no_bid` (brands that must not bid) and
`max_price_per_as` (brand → the most its winning slot may clear). A scenario
with `"kpis": false` is a **probe**: it scripts edge cases on purpose (weak
cues, no-fills), so its checks and every invariant gate still run, but its
decisions stay out of the product-KPI ratchets, which track the demo mix.

Segment-affinity oracles run on every decision (scripted, replay, rehearsal):
a bid reaches each moment segment once; an exact match keeps the whole bid and
an approximate one strictly less (`bid_match_inconsistent`); an age-restricted
category only ever matches exactly (`restricted_reach_widened`); a solo winner
never clears above its best matched rate × live multiplier
(`charged_above_matched_bid`). `affinity_bid_share` and `affinity_only_bids`
are informational KPIs. `evals/scenarios/11_segment_affinity.json` is the probe. `hold: n` repeats the scene n times and expects no
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

### 4.1 Stock-video ad fit: "does the ad make sense?"

Before we have our own clips, freely licensed stock footage of real people
(Wikimedia Commons: CC BY, CC BY-SA, CC0, each credited in the report) is run
through the **production perception agent** and the **live backend**, and
every screen change is judged against a human expectation for that scene.

```sh
make eval-video-fetch        # once: 8 clips (~200 MB) into the gitignored videos dir
make eval-video              # edge agent (needs `cd edge && uv sync --extra vision`)
make eval-video ARGS="--perceiver standin --only coffee-counter --reuse"
open evals/results/video/index.html
```

- **Clips** are declared in `evals/video/clips.json`: source page, license,
  artist, window (`start_s`, `duration_s`), a one-line `scene`, and
  `expect.sensible_brands` / `expect.never_brands`. That judgement is written
  by a person who has looked at the frames, and it is data, not code.
- **Perception** is `edge/agent/vision` in record mode (CPU, YOLO26, about
  200 ms/frame). `evals/video/perceive.py` is a self-contained `uv` script
  stand-in (`--perceiver standin`) that also cuts the review keyframes: one
  downscaled frame per fact change, **every detected head blurred**, written
  only to gitignored results.
- **Replay** runs in real time through a fresh live backend, so debounce,
  holds and swaps behave as live.
- **Report** (`evals/results/video/index.html`): play or scrub each local
  stock clip beside the auction's recorded ad and copy. This replay aligns
  decisions to source timestamps and does not measure browser display latency.
  For a source the browser cannot decode, place a full-length WebM preview
  alongside it as `<clip-id>.preview.webm` before generating the report.
  The timeline and change cards show the blurred frame, what perception
  saw (people, looking, held/near objects), what the audience context
  understood (segments), the ad and its copy, and a verdict (✓ makes sense,
  ? off-target, ✗ must never, house ad, "missed opportunity").
- **Metrics:** paid share, **ad precision** (fitting paid time / paid time),
  **sensible-ad reach** (fitting paid time / clip time), screen changes per
  minute, raw vs published fact changes (the agent's smoothing), CPU time
  per frame, and objects "seen but not understood" (seen in ≥ 30 % of
  samples with no segment ever formed: a catalog gap, not a model gap). The
  **gate** is `inappropriate_ad_seconds = 0`.

**First result (2026-11-21, 8 clips, edge agent):** perception works on real
footage. In the café clip it sees cups 80 % and laptops 93 % of the time and
counts up to 7 people near the screen. **The screen still never sells:**
paid share is 0 % on every clip, because the demo's vision catalog maps only
`sports ball` and `tap handle fixture`, so `cup`, `laptop` and `backpack`
form no segment. It is never wrong (0 must-never seconds), but it never
reaches anyone (sensible reach 0 %). Fix the catalog, then rerun this eval:
ad precision and reach become the numbers to push up.

### 4.2 The ad pipeline, end to end: find the bottleneck, fix it, measure again

`make eval-pipeline` (`evals/pipeline.py`) boots a backend with the **whole
ad system** switched on: creative studio (guard, score, copy cache, ROI
budget), warm worker, voice-over service with its disk cache and character
budget, the real ElevenLabs HTTP adapter (against `tests/sandbox/fake_elevenlabs.py`
on loopback), attention tracking and billing. The eval plays agent, player
and console through the demo journey (co-brand → fitness → beer → minor →
co-brand → coffee → co-brand → walk away), sending 5 Hz snapshots with live
tracklets and a `playback.report` when each ad leaves the screen. One reader
thread per socket stamps every message on arrival.

Only provider **timing** is replayed. Copy latency is drawn from the 21
recorded real Claude calls (p50 640 ms, max 2.6 s; `--copy-scale` stresses
it). TTS latency is an explicit assumption (`--tts-latency-ms`, default 350)
until a live measurement replaces it.

It runs three modes and reports a waterfall per mode: hot decision, creative
on the player, first voice-over, AI copy on air, complete ad (AI copy + its
voice-over), and playback report → receipt. Each with p50/p95, the critical
path split and the **bottleneck**, plus our own overhead around the provider
(queue, guard, score, push).

| Mode | What it is | Complete ad p50 / p95 | Bottleneck |
|---|---|---|---|
| `gen` | Warm boot, every group seen for the first time | ~700 / ~1050 ms | AI copy (98 % of the path) |
| `cold` (`--cold`) | Live right after a restart | ~700 / ~1050 ms | AI copy; startup voice prewarm races live lines |
| **`demo`** | After `make demo-warmup` | **~16 / ~20 ms** | none left (hot path, ~15 ms) |

**Gates:** demo complete ad p95 < 300 ms (the cache-hit target), 0 live LLM
and 0 live TTS calls after warm-up, generation complete ad p95 < 3 s, hot
decision p95 < 30 ms, every paid play gets a receipt, the house ad is never
billed, attention-seconds within 5 % of truth, and the same voice line is
never synthesized twice.

**Loop 1 (2026-11-21), what it found and what changed:**

1. *TTS stampede (fixed).* On a cold start, the startup voice prewarm and a
   live moment synthesized the same offer line at once and paid twice
   (the warm worker only dedupes its own jobs). `VoiceoverService` now runs a
   single flight per cache key (`test_concurrent_requests_for_one_line_share_a_single_synthesis`).
   The gate `voice_duplicate_syntheses = 0` keeps it fixed.
2. *AI copy is the whole wait (mitigated for the demo).* 98 % of the time to
   the complete ad is the LLM. Our own overhead is about 10 ms. The copy cache
   already serves a returning group in about 15 ms, but nothing filled it
   before the demo (`studio.prewarm` exists and has no caller). `make demo-warmup`
   (`python -m evals warmup`) plays every scripted scene once on the running
   backend. That cut the complete ad from about 700 ms to 16 ms, first audio
   from about 350 ms to 16 ms, and live provider calls during the demo to 0.
   Run it after the last restart, in the same daypart (the copy cache is in
   memory, and the daypart is part of its key). Voice-overs persist on disk.
3. *Single-brand ads had no pre-made voice.* The startup prewarm covers
   basket offers only, so solo lines ("Voltio. Energía…") were synthesized
   live (audio about 350 ms late). Warm-up covers them. **Proposed** for the
   owners of `demo.py`: add solo stock lines to `prewarm_voiceovers`, call
   `studio.prewarm` at startup, and persist the copy cache to disk so a
   restart doesn't lose the warm-up.
4. *Stress* (`--copy-scale`, `--dwell`): at 2× Claude latency the complete ad
   stays under 2.1 s p95. At **4×, the 2.5 s copy timeout fires**: only 33 %
   of moments get AI copy, and tokens are still billed. With **1.5 s moments
   and 2× latency, half the groups leave before their AI copy lands**. Stale
   results are dropped (0 late upgrades, the screen stays correct), but that
   spend is wasted. Warm-up removes both for the scripted demo.

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

### 5.1 Glass-to-glass in a real browser

```sh
uv run --with playwright playwright install chromium   # once
make eval-browser                                      # ARGS="--moments 30 --reuse-build"
```

`evals/browser.py` builds the production web bundle into the gitignored
results directory, serves it with `vite preview` (proxying `/ws`, `/media`
and `/api` to a fresh eval backend), and opens the real `/screen` page in
Chromium at 1920×1080 with GPU rasterization. The eval plays the agent:
a snapshot stamped `measured_at = now`, then it watches the pixels.

A probe injected before the app loads measures the page independently of the
player: WebSocket arrival of `creative.ready`, the first animation frame in
which the new creative is visibly composited, and the first frame it is fully
visible. It reads each layer through its `data-creative-id` hook and
multiplies layer opacity by creative opacity. It also counts frames where no
layer fully covers the screen, frame rate and frame gaps. The player's own
`player.displayed` (via `console.latency`) is checked against the probe, so
the number the console shows is verified. Each fully visible ad is
screenshotted to `evals/results/browser/shots/`.

**Gates:** snapshot → fully visible p50 < 1.5 s and p95 < 500 ms (the
screen-side budget), the player's `displayed_at` within 50 ms of the pixels
(p95), 0 uncovered frames, 0 creatives never shown, and **0 hard cuts** (a
swap fully visible in under 100 ms skipped the crossfade).

**Loop (2026-11-21):**

1. *The 250 ms crossfade was a race (fixed).* The incoming layer mounted at
   opacity 0 and turned 1 in the same frame whenever its logos were already
   decoded. A CSS transition needs a painted starting value, so about half
   the swaps were hard cuts (19 of 21 in one run). The player's
   `displayed_at`, fired by a 250 ms timer, was then about 250 ms later than
   the pixels. The fix plays the fade as a keyframe animation (`player-crossfade-in`
   in `global.css`, applied in `ScreenPage.tsx`), which always starts from 0.
   Result: 21/21 swaps fade, the report sits within 16 ms of the pixels, and
   snapshot → fully visible is p50 ~260 ms / p95 ~300 ms (first pixels ~45 ms).
   Reverting the fix reproduces the failure (19 hard cuts, 248 ms report gap).
2. *Heavier campaign creatives (open).* After the campaign studio
   creatives landed (full-screen photo, two `color-mix` gradient overlays, a
   size container query), first pixels went from ~47 ms to ~93 ms. In the
   CPU-only headless shell the frame rate halved and the gates failed. No
   single element is the cause: hiding each one recovers only a few fps.
   With GPU rasterization (now the default) every gate passes at ~50 fps,
   with 35 frame gaps over 50 ms per run.

The screen side is now ~0.3 s. True glass-to-glass adds perception (camera
frame → snapshot: YOLO ~115–200 ms per frame on CPU in the video eval, much
less on the GPU service, plus the 500 ms debounce). That puts the estimate
near 1 s p50, inside the 1.5 s target.

## 6. Next, by value for the demo

1. **Run `make eval-browser` on the demo laptop** (§5.1), whose GPU and
   Chromium are what judges see. Then cut the first-pixel time of the
   campaign creatives (93 ms against 47 ms before them; 35 frame gaps over
   50 ms per run) before it eats the crossfade.
2. **Spend that never airs.** Count LLM/TTS calls whose result was dropped
   as stale, per journey, and gate it in demo mode. Feed it into the ROI
   planner, e.g. skip copy upgrades when expected dwell < p50 copy latency.
3. **Map real COCO labels in the vision catalog** (`cup`, `bottle`,
   `backpack`, …; §4.1 shows the gap), then record our own golden clips with
   the agent's replay mode (done) and label them (§4).
4. **More billing gates** (the pipeline already gates receipts per paid play,
   house ad never billed, attention within 5 %): a glance under 1 s bills 0,
   Σ receipt totals equal the ledger, and a stock-out of an on-air brand
   swaps immediately (examples in
   `tests/unit/api/test_auction_loop_e2e.py`).
5. **Copy guard evals:** a fixed set of recorded LLM responses (competitor
   names, invented claims, changed prices) that the copy validator must
   reject. Recorded fixtures, never live calls in CI.
6. **Visual regression of the player:** headless Chromium screenshots per
   scenario layout, so a broken logo or overflowing copy fails before the
   judges see it.
7. **Chaos scenario:** kill the GPU service mid-run and measure failover time
   and "never blank" (ADR 0007); unplug test as a number.
8. **Per-step trace budgets:** when a step gets its own timestamp (warm copy,
   TTS, video upgrade), add its p95 as a gate here in the same PR.

### Voice corrections and bidding-war controls

`make eval-speech` includes café → cerveza → café, minor safety, negated beer,
nonalcoholic modifiers, the curated `acfe` spelling, and unavailable water
inventory. `speech_text` in scenario files is authored synthetic input; it is
never populated from a microphone or live transcript. Request-lifecycle socket
tests cover retracted partials in a group. Echo tests cover actual playback
windows, interrupted audio and unrelated shopper requests.

The advertiser slider serializes writes and keeps only the latest queued drag.
It retains a failed draft for retry, confirms the final acknowledged value,
ignores obsolete polling responses, and orders writes after the last observed
market sequence (including another controller's clock). Tests cover coalescing,
unmounts, stale acknowledgements and decimal-string transport. Existing HTTP
auction tests continue to cover reserve/budget/safety, stock-outs and delayed
screen swaps. Increasing a multiplier does not guarantee an eligible winning bid.

### Semantic flexibility and microphone framing

`make eval-speech` now also replays the authored fixtures in
`evals/fixtures/speech/understanding.json` through the real structured-output
adapter, warm worker, audience evidence, auction and player sockets. It covers
indirect requests, Chilean slang, English, transcription spelling errors,
multiple needs, cancellation, quotes, hypothetical mentions, unavailable items,
model failure/uncertainty, rejected topics and prompt-injection handling.
These are **response-replay application evals**, not measurements of live LLM
accuracy; expected responses are authored rather than obtained from a live model.
No paid provider is needed, and no microphone transcripts are harvested.

`make edge-check` covers sample preservation under irregular capture chunks,
bounded notification scheduling, incomplete-turn pauses, exact PCM byte bounds,
stale audio, missing final transcripts, supplemental timestamp events, empty
commits, stream discontinuities and final-source flushes. `make test-sandbox`
then runs the real agent and backend processes against a local provider server.

### Speech robustness (`make eval-speech-robustness`)

`edge/agent/speech/robustness.py` mixes the authored TTS fixtures with venue
conditions (far talker -24 dB, clipped hot mic, DC offset, white noise at
20/10 dB SNR, mains hum, crowd babble, a two-part request, a 48 kHz stereo
speakerphone) and streams them through the shipped `SpeechGate` with Silero
and the adaptive energy fallback. **Gates:** >= 97 % of voiced frames streamed,
no more segments than sentences, gate closed after speech, end lag <= 1.3 s,
zero opens on stationary noise alone. Babble is reported, not gated (a VAD is
meant to fire on speech). The offline tier also runs inside `make edge-check`.

`ARGS="--live"` sends the same clips through real Scribe + `SpeechPipeline`,
with the room still talking for 4 s after the line. **Gates, babble included:**
the final request carries exactly the line's intents, it arrives <= 3 s after
the end of speech (no end-of-clip flush), and alcohol is never requested
early. Early non-alcohol mistakes are reported as `warn`.

What the live tier found, and the defence each finding drove (all in
`edge/agent/speech/pipeline.py` / `scribe.py`, measured 45/45 over 3 runs):

| Finding (10 dB babble unless noted) | Defence |
|---|---|
| Keyterm biasing makes Scribe invent our bias words: "Shop" (cerveza), "Sopaipilla" | Partial stabilization: a meaning counts after 2 agreeing partials |
| A 2-partial hallucination can still slip through | Alcohol is final-only: a partial never requests it (rejecting it early is fine) |
| The VAD never hears silence in a crowd, so finals waited for the 15 s cap | Settled-turn commit: longest partial unchanged for ~1.2 s, >= 2 words, no dangling "no/sin/quiero/y" |
| Babble-only segments finalize as "Un café", "Creo", "Hola, soy" | `include_timestamps`: finals with mean word logprob < -1.3 request nothing (real speech measured >= -0.95) |
| `turn_probability` hovers 0.4-0.5 after a finished request at 10 dB | Not used for commits |
| `filter_background_audio` still produced "Un café" / "Bricksoma" | Not enabled (no measured gain) |

Measured: final 0.5-1.8 s after speech in clean/noise/far/clipped, 1.5-2.3 s
in continuous babble. Accepted residual: under heavy babble a food term
(comida/hambre) can flash early; a third agreeing partial would fix it at
~300 ms extra latency for every shopper.

Remaining release evidence requires a separately authorized paid rehearsal on
actual hardware: short single words, long pauses, nearby speech, TV overlap,
clipping/noise, and reconnects. Measure WER, request meaning, wrong-ad rate,
request-to-visible-ad latency and provider usage separately. Passing protocol
fixtures alone does not establish acoustic accuracy or camera-to-TV latency.
