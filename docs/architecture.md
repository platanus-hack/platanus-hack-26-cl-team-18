# System architecture, latency budget and tech stack

> **Doc status: LIVE.** Runtime design for the product in
> [`product.md`](product.md). Binding decisions are recorded in
> [`adr/`](adr/). Code-level layering rules are in [`rules.md`](rules.md) and
> [`../CLAUDE.md`](../CLAUDE.md).

## 1. The one idea that makes it fast: three speeds

Latency is the product. Most of it is won by **architecture, not programming
language**: nothing slow is allowed to sit between "the audience changed" and
"the screen changed". Split all work into three paths and never let a slower
path block a faster one.

| Path | Runs where | Budget | What runs | Rule |
|---|---|---|---|---|
| **Hot** (per frame / per decision) | Perception agent + backend in-process + player | 15–60 ms per frame; < 100 ms server decision | YOLO detection, tracking, attention, audience-change detection, auction, cache lookup, creative swap | No network model call (VLM, LLM, image, video, TTS). No blocking IO except one Postgres commit. |
| **Warm** (per moment, async) | Backend async tasks | 0.1–15 s | VLM tagging, speech-to-text topics, AI copy, TTS voice-over, hero image, fast video upgrade | Results *upgrade* the moment or the creative in place; the hot path never waits. |
| **Cold** (offline / precomputed) | Batch jobs at seed / campaign setup | minutes | Brand-kit prep, pre-generated video loops per compatible bundle, pre-rendered creatives and voice-overs for likely bundles | Anything slow happens *before* it's needed. |

## 2. Runtime topology (three processes on the demo laptop)

```
┌──────────────── PERCEPTION AGENT  edge/  (Python, own uv project; ADR 0004) ──────────────┐
│ webcam (OpenCV) ─► Detector: remote GPU YOLO26 (WSS) | local fallback ─► ByteTrack       │
│                    keypoints ─► looking / distance band / face box ─► anonymous tracklets │
│                    face blur ─► keyframe every ~1.5 s or on change                        │
│ mic (sounddevice) ─► VAD ─► sound-scene class  +  (speech mode) ElevenLabs Scribe          │
│                                                   realtime WS ─► transcript (in memory)  │
└──────────────┬────────────────────────────────────────────────────────────────────────────┘
               │ WS localhost: audience.snapshot · audience.keyframe · audience.utterance
               ▼
┌──────────────────────────── BACKEND  app/  (Python 3.14, FastAPI + uvicorn) ───────────────┐
│ ws gateway ─► MessageBus (sync kernel via anyio.to_thread)                                  │
│  screens ─► audience ─(AudienceMomentChanged)─► auction ─(AuctionCleared)─► creative        │
│               ▲  warm: VLM tags, speech topics          in-memory            cache hit → push│
│               │                                         campaign book        miss → template │
│               │                                                              warm upgrades:  │
│               │                                                              copy · TTS ·    │
│               │                                                              image · video   │
│  delivery ◄── playback.report + attention samples ─► charges (Money, append-only)          │
│ Postgres (aggregates + outbox) · media cache (local disk: voice-overs, video loops)        │
└──────────────┬──────────────────────────────────────────────────────────────▲───────────────┘
               │ WS: creative.ready / creative.upgraded / console.*            │ playback.report
               ▼                                                               │
┌──────────────────────── WEB  web/  (TypeScript + Vite + React) ──────────────┴───────────────┐
│ /screen  : renders CreativeSpec (layout + real logos + copy + video loop + voice-over audio) │
│            preloads next, crossfade, never blank                                            │
│ /console : what the screen sees, live auction, KPIs, latency, transcript topics, simulator  │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

All three run on one laptop, so they **share one clock**. Attention samples
from the agent and `displayed_at` from the player can be joined on timestamps
without clock-sync work. If the agent later moves to separate hardware, add
WS ping/pong offset estimation and keep both raw instants.

**For the demo, run the backend on the demo laptop (localhost).** Render has no
South American region, so Santiago to Render adds roughly 150 ms or more of RTT
per hop, and venue Wi-Fi is unpredictable. Render hosts the public deploy URL
and console; the live demo loop runs locally. Same code, different `.env`.
Provider calls (VLM, ElevenLabs, video) still go over the internet, which is
another reason they are warm-path only. **Bring a phone hotspot.**

## 3. Latency budget (glass-to-glass)

Target: an audience change seen by the camera becomes a new creative on screen
with **p50 < 1.5 s and p95 < 2.5 s**. Cache hits should be **< 300 ms**.

| Step | Path | Budget | Notes |
|---|---|---|---|
| Frame → remote GPU YOLO → detections | hot | 40–150 ms round trip (budget p95 ≤ 150 ms) | JPEG 640 px, ≤ 2 in flight; local fallback 30–45 ms (ADR 0007) |
| Tracker (local ByteTrack) | hot (agent) | < 2 ms | |
| Looking from pose keypoints | hot (agent) | < 1 ms | Geometry on YOLO26-pose keypoints; no extra model |
| Change debounce (moment stable) | hot (agent) | 300–800 ms | Deliberate: avoids re-auctioning for a passer-by. Tune live. |
| Snapshot → backend (WS, localhost) | hot | < 2 ms | |
| Moment update + auction + commit | hot (backend) | < 30 ms | In-memory campaign book; brute-force bundles over top-K |
| Creative cache lookup | hot | < 5 ms | Key: (bundle, layout, segment signature) |
| **Cache miss:** template spec + stock copy + bundle's pre-made video loop | hot | < 20 ms | Renders immediately |
| Push → render → crossfade | hot (player) | < 100 ms | Assets preloaded; 250 ms crossfade |
| VLM tagging of keyframe | warm | 0.6–1.5 s | Refines the moment; re-auction only if the winner changes |
| Speech → committed transcript (Scribe realtime) | warm | ~150 ms model + network | VAD-gated; see §7 |
| Transcript → topics (keyterm match → LLM) | warm | < 5 ms keyterms; 0.4–1 s LLM | Keyterms can boost a segment almost instantly |
| AI copy | warm | 0.4–1.2 s | Upgrade in place |
| TTS voice-over (ElevenLabs Flash, streaming) | warm | ~75–100 ms model + network to first audio | Cached per (copy, voice) |
| Hero image | warm | 1–4 s | Optional upgrade |
| Fast video upgrade (image-to-video, 5 s, 480–768p) | warm | ~2–15 s (vendor-reported) | Only when no cached loop fits; see §8 |

**Instrument every step.** Each decision carries a trace: `measured_at`
(agent), `received_at`, `moment_changed_at`, `cleared_at`, `ready_at`,
`displayed_at` (player), plus `upgraded_at` per upgrade kind. The console
shows live p50/p95.

### Tricks that buy most of the latency

1. **Two-stage tagging.** YOLO tags (people, looking, objects like
   bottle/cup/backpack/laptop/dog/skateboard) are ready within 50 ms and can
   trigger an auction. VLM and speech topics arrive later and refine the moment.
2. **Progressive creative.** Template + real assets + stock copy + cached
   bundle loop go up now; AI copy, voice-over, hero image and fresh video
   replace them in place when ready.
3. **Speculative pre-computation.** When a new tracklet enters the frame
   (before it's stable), pre-run the auction for "current moment + likely
   segment" and pre-warm the creative, its voice-over and its video.
4. **Warm cache from the cold path.** At seed time, pre-generate video loops,
   creatives and voice-overs for every compatible bundle the demo can hit.
5. **In-memory hot state.** Campaign book and current moment per screen live
   in process memory (single backend instance); Postgres is the record.
6. **Never queue frames or audio.** Process the latest frame; backpressure
   means dropping, not buffering.

## 4. Tech stack

**The committed stack, device settings and the day-1 benchmark gate are in
[`stack.md`](stack.md) (binding, ADR 0006).** Summary:

| Layer | Commit |
|---|---|
| Devices | Laptop (NVIDIA RTX preferred) + wide-FOV UVC webcam (720p30 MJPG) + USB speakerphone with hardware AEC + 16:9 TV in Game mode |
| GPU | Remote stateless YOLO26 TensorRT service `gpu/` (provider from benchmark), local failover (ADR 0007) |
| Perception agent | Python 3.12, YOLO26-pose (every frame) + YOLO26s objects (5 fps), ByteTrack, TensorRT/CoreML/OpenVINO, Silero VAD, ElevenLabs Scribe v2 Realtime, partial-transcript keyterm matcher, echo guard |
| Backend | Python 3.14, FastAPI + uvicorn[standard], 1 worker, Pydantic JSON, in-memory hot state, DDD kernel, Postgres, asyncio WarmWorker |
| LLM | Claude Haiku 5.5 via the official `anthropic` SDK (effort low, structured output, prompt caching) for tags, topics and copy |
| Voice | ElevenLabs Flash v2.5 (cached MP3 voice-overs; streamed PCM replies) |
| Media | fal: FLUX-class images; MiniMax H3 Max Turbo warm video; top-quality model for cold loops; ffmpeg → H.264 720p |
| Player / console | Vite + React + TS in Chromium kiosk; A/B layers + 250 ms crossfade; Web Audio; CSS/WAAPI motion; uPlot for latency |

Anthropic does not generate images, video or audio. Claude covers tagging,
topics and copy, and the other providers cover media.

**When would we switch languages?** In production at scale: auction and
decisioning in Go or Rust, and perception in C++/TensorRT on Jetson-class
hardware behind the same message contract. Not this weekend.

Every AI output stores `model_version`, `confidence_score` and
`reasoning_trace`. Low-confidence tags are dropped from bidding (still shown in
the console), invalid copy falls back to stock copy, and a failed media
generation leaves the previous media in place.

## 5. Bounded contexts

The [context map](../CONTEXT-MAP.md) shows ownership, published interfaces and
the current scaffold.

| Context (`app/modules/…`) | Owns | Aggregates (first cut) | Emits |
|---|---|---|---|
| `screens` | Physical devices, layouts they support, heartbeats | `Screen` | `ScreenRegistered`, `ScreenWentOffline` |
| `audience` | Snapshots, keyframe tags, speech topics → audience moments | `AudienceMoment` (per screen) | `AudienceMomentChanged` |
| `campaigns` | Advertisers, brands, categories, brand kits (incl. voice id), bids, compatibility rules incl. **partner allow-lists**, **basket offers per group size**, budgets | `Advertiser`, `Brand`, `Campaign` | `CampaignActivated`, `BudgetExhausted` |
| `auction` | Running the bundle auction; prices | `Auction` (immutable once cleared) | `AuctionCleared`, `AuctionNoFill` |
| `creative` | CreativeSpec composition (basket offer, **dwell stages**, QR per basket), creative + media cache, copy/voice/image/video upgrades | `Creative`, `MediaAsset` | `CreativeReady`, `CreativeUpgraded` |
| `delivery` | Proof of play, attention-seconds, charges ledger, **redemptions** | `Playback`, `Charge`, `Redemption` (append-only) | `PlaybackCompleted`, `ChargeRecorded`, `RedemptionRecorded` |

Flow: agent → `audience` → (`AudienceMomentChanged`) → `auction` →
(`AuctionCleared`) → `creative` → WS push → player →
`playback.report` + attention samples → `delivery` → charges. Contexts talk
through events and published queries only.

## 6. The bundle auction (precise enough to implement)

Inputs: the audience moment (people present `n`, looking `k`, segments
`s → (count, confidence, source ∈ {vision, vlm, speech})`, flags like
`minor_likely`), eligible campaigns, and the screen's layouts (`solo`=1 slot,
`duo`=2; `trio`=3 is later, ADR 0008 D4).

1. **Eligibility (Specifications, pure):** active, budget left, within
   schedule, **in stock at this screen** (`InStockAtScreen`), passes
   brand-safety policies for this moment (e.g.
   `NoAgeRestrictedCategoryWhenMinorLikely`).
2. **Value per campaign:** `v_c = Σ_s bid_c(s) · relevant_viewers(s) ·
   confidence(s) · context_mult(category, context) · live_mult_c`, in `Money`
   per attention-second.
   - **Segment matching (`SegmentAffinity`, `auction/domain/affinity.py`):**
     `bid_c(s)` is the best of the campaign's bids that *reach* `s`, each times
     its affinity weight: **exact** 1.0; **broader** (bid on an IAB ancestor,
     "Sports" → "Cycling") 0.8 per level; **narrower** (bid on a descendant)
     0.4 per level; **related** (a curated, symmetric same-need pair in the
     catalog's `related` list, e.g. Coffee ↔ Pastry 0.4) its own weight. Each
     moment segment counts once per campaign. The table is built at startup
     from the catalog's paths and pairs and is a pure in-memory lookup, so
     there is no text similarity and no model call on the hot path. The weight
     also scales the **charge ceiling**: an approximate match never costs more
     than `bid × weight`. Coverage counts an approximate match by its weight.
     A campaign can opt out (`Bidder.broad_match=False`). Age-restricted
     segments have no related pairs, so affinity never widens alcohol's reach.
     The console shows the route (`BidLine.matches`: "via Coffee →
     Pastry/Doughnuts · related 40%").
   - Speech-derived segments carry an explicit-intent boost (someone *said*
     "tengo hambre"), capped so one loud person can't outweigh the group.
   - **Held-product boost:** if a held product's brand is an approved partner
     of `c`, add a partner boost (complete the basket). With conquest enabled
     (off by default), a held *competitor* product adds a conquest boost.
   - `context_mult` comes from a pure table (daypart × weather × category),
     set from the console in the demo.
   - `live_mult_c` is the `/advertiser` slider (bidding war), default 1.0,
     bounded (e.g. 0.2–5.0). It's a campaign setting change recorded as a
     command, not a side channel.
   - Retailer **house bids** are ordinary campaigns owned by the venue.
3. **Candidate bundles:** take the top K (≈ 8) by `v_c` and enumerate all
   subsets of size ≤ max slots, then keep the **compatible** ones: every pair
   is on **both** brands' partner allow-lists (pre-approved partners only), no
   shared competitor category, an approved basket loop and a basket offer exist
   for the pairing, and a layout fits.
4. **Bundle value:** `V(B) = Σ_{c∈B} v_c · efficiency(c, layout)` + the
   bidders' own opted-in basket bonuses. Slot efficiency (not screen share)
   is the value a slot keeps for its own viewers; it falls back to share
   when a member targets the same segments as another slot
   ([auction plan](auction-plan.md) D1).
5. **Winner:** `B* = argmax V(B)` with deterministic tie-break (value, fewer
   slots, campaign ids). Every slot must clear `reserve × looking × share`;
   with no such bundle, a house ad plays (no-fill).
6. **Prices:** VCG `p_c = V*(without c) − (V(B*) − own_c)`, then
   core-adjusted (default) so no losing basket or solo brand would have
   paid more, floored at the slot reserve and capped at the winner's own
   value. Payments are per screen-second; the clearing price per
   attention-second is `p_c / looking` (a `MoneyRate`, 4 dp).
7. **Record:** all bids, exclusions with reasons (competitor, safety, budget,
   below reserve), winner, prices, and the **single-brand counterfactual**.

Billing: `charge_c = price_per_as_c × attention_seconds_measured_during_play`
(`MoneyRate.total_for`), rounded `ROUND_HALF_EVEN` to `Money` at the ledger
boundary only. **Zero measured
attention → zero charge, recorded explicitly** (a `Charge` of 0 with reason
`no_attention`, so the console can show "charged $0: nobody looked").

### 6.1 Basket creative: group-size offers and dwell stages

- **Basket offer:** each approved pairing defines offers per group-size band
  (`1`, `2`, `3+`), e.g. "Combo $X" / "2×1" / "Trío $X". The creative takes
  the band from the moment's *looking* count. The offer text and price come
  from the brand kits; AI copy may phrase around them but never changes a price
  or a claim.
- **Dwell stages:** the spec carries three stages, and the player advances on
  the agent's dwell signal of the looking tracklets, not on a timer:
  `teaser` (0–3 s: video loop + logos, big type) → `offer` (3–8 s: basket
  offer sized to the group) → `close` (8 s+: QR + "2 min away"). If attention
  drops, the player holds the current stage; if the moment changes, a new
  auction replaces the creative. Each stage has its own voice-over line,
  pre-generated at seed time.

### 6.2 Redemptions (closed loop)

- Codes **expire after 10 minutes** (`expires_at` on the code; redeeming
  after that returns "expired" and records nothing).
- On redemption the backend builds a **moment receipt** (people, looking,
  attention-seconds, charge per brand, redemptions) and sends it to the
  console and, if connected, to the thermal printer (ESC/POS over USB, a
  `ReceiptPrinter` port with a console-only fallback adapter).
- Each cleared auction gets a short **redemption code** and QR pointing to
  `GET /r/{code}` (an offer page served by the backend). `POST /r/{code}/redeem`
  records a `Redemption` (append-only, idempotent per code + device nonce)
  and credits **every brand in the basket**. The console shows it live.
- The code identifies the auction (moment + basket), never a person. No login,
  no phone number, no cookies beyond the dedupe nonce.
- In production the same code is redeemed at the POS; the offer page stands in
  for checkout in the demo.

## 7. Voice: speech recognition and voice-overs (ElevenLabs; ADR 0005)

### 7.1 Speech recognition as an audience signal

What people *say* in front of the screen is the strongest intent signal we
have ("qué hambre", "vamos al gym", "¿dónde hay café?"). The pipeline:

1. **Agent:** mic → VAD (only speech segments) → stream PCM 16 kHz to
   **Scribe realtime** with keyterm prompting (brand names, category words,
   Chilean Spanish variants) → partial and committed transcripts in memory.
2. **Agent → backend:** `audience.utterance {screen_id, seq, measured_at,
   text, language, keyterms_hit[]}`. The text is **ephemeral**: held in memory
   for topic extraction and discarded afterward. The console shows only
   mapped topics.
3. **Backend:** keyterm hits map straight to IAB segments within milliseconds.
   The LLM `TopicExtractor` (structured output: `segments[] {iab_id,
   confidence}`, `intent_strength`, `reasoning`) refines within about a
   second. Topics merge into the moment with `source=speech` and a short TTL
   (e.g. 60 s).
4. **Auction:** speech-boosted segments can flip the winner. That's the
   demo's "it heard me" moment.

**Privacy (non-negotiable):** speech mode is **off by default** and turned on
per screen. When it's on, the screen shows a visible "listening" indicator.
Audio is never stored and transcripts are never stored. No speaker
identification and no voice embeddings. Only aggregate topics with a TTL
survive. Recording bystanders' conversations has legal weight (Ley 21.719,
GDPR), so the public pitch frames ambient listening as **opt-in venues with
signage**, and "talk to the screen" as the consent-based default.

### 7.2 "Talk to the screen" (consent by action)

Push-to-talk or a wake phrase → Scribe → LLM (brand agent constrained to the
brand kit's approved claims and offers) → ElevenLabs TTS streaming reply →
player audio, with the creative updated to show the answer (nearest store,
promo code). Runs alongside the ad loop and never blocks it.

### 7.3 Voice-overs that accompany ads

- Every creative can carry a `voiceover` (spoken copy, ≤ 12 words, e.g.
  "¿Post-entreno? Voltio y Café Patio, a dos minutos.").
- Generated with **Flash v2.5** when the creative's copy is final (warm), and
  cached by `(copy_hash, voice_id, model)` as a file in the media cache.
  Cache hits play instantly. Pre-generate at seed time for the demo bundles.
- Voice: solo creatives use the brand kit's voice id; co-brand creatives use a
  neutral "screen narrator" voice so neither brand owns the line.
- Player rules: duck the background audio under the voice, cap the volume, and
  play voice only when at least one person is looking (no talking to an empty
  hall). A venue setting can mute everything.

## 8. Generating ad video fast

Latest landscape (Nov 2026, **mostly vendor-reported, verify on day 1**):

| Model (host) | Reported speed | Use for |
|---|---|---|
| MiniMax H3 Max Turbo (fal) | ~2–4 s total request for a 5 s clip at 480–768p; ~5 s at 1080p | **Warm upgrades** (fastest public API reported) |
| LTX-2 / 2.5 distilled (fal "fast" endpoints, open weights) | ~seconds to ~25 s per clip via API; much faster self-hosted on big GPUs; includes audio | Warm upgrades, or self-host if we have a GPU |
| Veo 3.1 Lite | ~8–15 s for 8 s | Warm or cold |
| Runway (current gen) | ~9–12 s for 5–8 s | Warm or cold |
| Veo 3.1 Quality, Kling 3.0, Seedance 2.0, Wan 3.0 | ~1–6 min | **Cold path only** (best quality) |

Sora 2's API is being discontinued, so don't build on it.

**Strategy, in order of importance:**

1. **Pre-generate per bundle (cold).** Bundles are a small, known set (10
   brands → at most 45 pairs; fewer once competitors are excluded). At seed
   time, generate a 5–8 s loopable clip for every compatible pair and the demo
   trios, plus a solo clip per brand, using a quality model. The hot path just
   plays the bundle's loop. **This is where 95 % of demo video comes from.**
2. **Image-to-video from a composed first frame.** Build the first frame
   ourselves: product cutouts from both kits arranged in the layout, with an
   optional AI background. Then animate it with image-to-video. Products stay
   recognizable, and the composition matches the layout's slots. Prompts
   describe motion only ("slow push-in, condensation drips, steam rises").
3. **Logos, copy, QR and price are overlays**, rendered by the player on top
   of the video, never inside it. Video models garble text and trademarks.
4. **Warm upgrade with a turbo model** only for a bundle × mood combination
   with no cached loop. Generate at 480–768p and 5 s (the screen upscales;
   that is fine for ambient motion), loop it, and swap it in when ready. The
   template creative plays meanwhile.
5. **Speculate.** When the auction's runner-up bundle is close to winning,
   pre-generate its loop in the background.
6. **Budget guard.** Clips cost money per generation. Cap concurrent jobs
   (e.g. 2), dedupe by cache key, and set a per-hour generation budget in
   config.
7. **Motion fallback without video:** CSS/Lottie/PixiJS over stills (Ken
   Burns, particles), which always works.

## 9. Failure modes and safe fallbacks

| Failure | Fallback |
|---|---|
| Camera failing | Simulator mode (scripted audiences) or replay of a recorded video file into the agent |
| YOLO wheels broken on 3.14 | Agent's own uv project on 3.12/3.13 (ADR 0004) |
| VLM slow or failing | Bid on YOLO tags only; console shows "VLM degraded" |
| Scribe down or no network | Speech topics off; sound scene only; console shows "speech degraded" |
| Copy invalid | Stock copy |
| TTS fails | Creative plays without voice |
| Video generation slow or failing | Cached bundle loop, then solo loop, then animated still |
| WS disconnects | Player keeps the current creative, then a house ad; reconnect with backoff; snapshots idempotent by `(screen_id, seq)` |
| No bundle above reserve | House ad (no-fill) |
| Backend down | Player loops its last 5 creatives locally |

The screen is **never blank** and **never silent-fails**: every degradation
shows in the console.

## 10. Message contract

Versioned JSON, `type` + `v`. The source of truth is
`app/contract/messages.py` (self-contained, so the agent vendors it verbatim);
`web/src/contract/messages.gen.ts` (zod schemas + types) is generated from it
with `make contract`, and CI fails when they drift. Fields below are the first
cut; the Pydantic module is authoritative.

- `audience.snapshot` (agent → backend): `screen_id`, `seq`, `measured_at`,
  `people`, `looking`, `tracklets[] {id, dwell_s, looking, distance_band}`,
  `objects[] {label, score}`, `sound_scene[]`,
  `minor_likely {value, confidence}`, `queue {length, est_wait_s}?`
  (aligned, low-movement tracklets).
- VLM tagging output (warm, `AudienceTagger`) adds `held_products[]
  {brand_id?, category, confidence}`, read from labels in the face-blurred
  keyframe. It covers objects only, never people.
- `audience.keyframe` (agent → backend): `screen_id`, `seq`, `measured_at`,
  face-blurred JPEG (binary frame), ≤ 1 per 1.5 s.
- `audience.utterance` (agent → backend, speech mode only): `screen_id`,
  `seq`, `measured_at`, `text` (ephemeral), `language`, `keyterms_hit[]`.
- `creative.ready` / `creative.upgraded` (backend → player): `creative_id`,
  `auction_id`, `spec` (incl. `video_url?`, `stages[] {name, copy,
  voiceover_url?}`, `offer {group_band, text, price}`, `qr {code, url}`),
  `upgrade_kind?`
  (`copy|voice|image|video`), `trace`.
- `playback.report` (player → backend): `creative_id`, `started_at`,
  `ended_at`, `seq`. Attention-seconds are computed by the backend by joining
  the agent's tracklet `looking` samples with the playback window.
- `stock.set` (`/clerk` → backend): `screen_id`, `brand_id`, `in_stock`. A
  stock-out re-auctions immediately.
- `advertiser.bid_multiplier` (`/advertiser` → backend): `campaign_id`,
  `multiplier`, `seq`. Bounded, idempotent by `seq`; it re-auctions the
  current moment.
- `context.set` (console → backend): `daypart`, `weather`.
- `receipt.issued` (backend → console / printer): the moment receipt.
- `player.stage` (player → backend): `creative_id`, `stage`, `at`, so
  dwell-stage reach is measurable.
- `console.*` (backend → console): moment (with sources), auction book,
  cleared result, KPIs (incl. redemptions and zero-attention charges), latency
  trace, degradation flags.
