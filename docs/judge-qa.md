# Judge Q&A: what to say, what proves it, what not to claim

> **Doc status: LIVE.** Checked against the code on `main` on 2026-11-22. Each answer
> is true today and cites its proof. "Don't say" lists claims the code doesn't back up
> yet. Privacy evidence in depth: [`privacy.md`](privacy.md). Demo script:
> [`demo.md`](demo.md).

## 0. Before the pitch: make the AI visible

**The default station runs no AI at all.** Without `--paid-providers`,
`scripts/station.py` (`provider_environment`) blanks the Anthropic, ElevenLabs and fal
keys and turns off AI copy, voice-overs and the vision tagger. Every ad then airs on
approved stock copy. A judge who asks "where's the AI?" would see none.

| Do | Why |
|---|---|
| Start with `make demo-live ARGS="--paid-providers"`, with `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` and `ELEVENLABS_NARRATOR_VOICE_ID` in `.env` | Turns on Claude copy, ElevenLabs voice-overs and Claude vision tags |
| Run once with `VOICEOVER_PREWARM=true` before the first rehearsal | Voices the 7 approved offer lines (~281 of the 2,000-character cap) and caches them on disk, so every play is a cache hit |
| Check the console's input status reads **"IA: copy y voz"** | `web/src/features/console/input-status.ts`: it says "Copy de stock" when AI is off |
| For "show me the AI write an ad", open the campaign studio (`/demo/<key>/campaigns`) | The draft appears at once on approved copy, then Claude rewrites it in place in about 5 s. If Claude's version doesn't beat stock, stock stays. The UI doesn't label which wrote it; the dev page below does |
| Keep `/demo/<key>/dev` open on the second laptop for the deep dive | The on-air panel shows the copy's source, the model version and the guard result (`web/src/features/dev/on-air.tsx`) |

## 1. "How does it work?"

| Question | Say this | Proof |
|---|---|---|
| How do you know someone looked? | From pose keypoints: nose centred between both eyes means facing the screen. One pose model, no face model | `edge/agent/vision/geometry.py` (`is_looking`) |
| What's an attention-second? | One looking person for one second while the ad plays. Glances under 1 s, people too far to read, and looks that started on the previous ad bill nothing | `app/modules/delivery/domain/attention.py` (`MIN_BILLABLE_LOOK`), `delivery/domain/validity.py` |
| How do you count the group? | Anonymous tracklets on the camera laptop (`t1`, `t2`…), reset every session | `edge/agent/vision/tracking.py` |
| How do you know what I'm holding? | Demo props carry a small printed marker read in ~7 ms; an open-vocabulary detector sees cups and cans; Claude can read the label on a crop with the head blacked out. Objects only, never the person | `edge/props/markers-chile.json`, `edge/agent/vision/vocabulary.py`, `app/modules/audience/infrastructure/recognition_vlm.py` |
| How fast is it? | The server decides in about 4 ms (p95). Camera to screen measured about 1.1 s with a simulated 80 ms detector; target p50 < 1.5 s. Live p50/p95 is on the console | `docs/latency-feedback-loop.md` (server decision p95 4.27 ms; 1,112 ms scene-to-visible trial) |
| What if the GPU or network dies? | The local detector takes over, and the screen falls back to the house ad. It is never blank | ADR 0007 (`FailoverDetector`), product invariant 5 |

**Don't say:** "the vision model reads the label" as the main mechanism (the demo
relies on markers), or that latency is benchmarked per auction (`scripts/bench_auction.py`
doesn't exist; the numbers above are end-to-end measurements).

## 2. "How are the ads made? Is any of it AI?"

| Question | Say this | Proof |
|---|---|---|
| Who writes the ad text? | Claude Haiku 5.5 writes it live for the moment: the group size, what's held, the time of day. Approved stock copy airs first, in about 1 ms, and Claude's version replaces it in seconds if it scores better | `app/modules/creative/infrastructure/claude_copywriter.py`, `creative/application/studio.py` |
| Can a brand make a campaign with AI? | Yes: the campaign studio drafts it from the approved catalog instantly, then Claude rewrites the copy through the same guard and spend cap as a live ad. Measured live: ~5 s, price unchanged | `app/modules/creative/application/campaigns.py` (`upgrade`), `POST /api/campaigns/upgrade`; tests `tests/unit/api/test_campaign_generator.py` |
| Can the AI change the price or say something false? | No. Every AI line passes a guard before it airs: the price must match digit for digit, no competitor names, only claims the brand approved. Cached lines are re-checked | `app/modules/creative/domain/guard.py` (`check_copy`); tests `tests/unit/creative/test_copy_guard.py`, `test_guard_regulatory.py` |
| How good is the AI copy? | On the creative eval, Claude's copy wins over stock 89 % of the time and airs 83 %; the guard rejects 100 % of the adversarial cases | `make eval-creative` (writes `evals/results/creative-latest.json`, local and gitignored). **Run on recorded model responses**, not live calls: re-run it before quoting |
| Is the voice AI? | Yes: ElevenLabs Flash v2.5 reads the brand-approved offer line, cached once and replayed | `app/modules/creative/infrastructure/elevenlabs_tts.py`, `creative/application/speech_text.py` |
| Are the images AI? | Some. The energy, coffee and beer scene photos were AI-generated (prompts in `assets/campaigns/prompts.json`) and the Chilean brand variants are recolours of them. The other Chile product shots are real, credited Wikimedia Commons photos. **Logos are never AI**: they're the brands' real SVGs | `assets/campaigns/photos/SOURCES.md`, `assets/campaigns/logos/SOURCES.md` |
| Is the video AI? | The ads play pre-made motion loops (ffmpeg moves over the photos). A budgeted offline job can generate loops with an open video model on fal (LTX-2); it never runs live | `scripts/build_campaign_media.py`, `scripts/build_bundle_loops.py`, `app/modules/creative/infrastructure/fal_video.py` |
| Why not generate video live? | It takes far longer than the moment lasts and costs per clip. Logos, prices and the QR are overlays the player draws, so a video can never misprint them | ADR 0003, `docs/architecture.md` §8 |

**Don't say:**
- "fal generates hero images". There is no image generator in the code.
- "Turbo video models upgrade the ad in the background". Docs only, not built.
- "Every basket's video is AI-generated and approved by both brands". The committed loops are photo motion.
- That the studio's AI copy is always used. Claude's draft must pass the guard *and* beat the stock score; otherwise the studio keeps stock.

## 3. "How does the auction work?"

| Question | Say this | Proof |
|---|---|---|
| What do brands bid on? | A rate per attention-second for the audience segments they want. A bid's value is that rate × the matching viewers × the tagger's confidence, capped at the people actually looking | `app/modules/auction/domain/valuation.py` |
| How does a basket form? | The top bidders are paired 1 or 2 brands at a time. A pair needs both brands on each other's partner list, different categories (never two colas) and an approved joint offer. Minors block age-restricted brands; out-of-stock and out-of-budget brands drop out. Every exclusion is shown with its reason | `auction/domain/rules.py`, `auction/domain/clearing.py` |
| Who wins? | The basket with the highest total value. Ties are broken deterministically (fewer brands first) | `clearing.py` |
| What does each brand pay? | Second-price logic (VCG): each brand pays the value it displaced from the others, then raised just enough that no losing basket would have paid more (the "core"), so the screen isn't undersold. A brand never pays more than its bid | `clearing.py` (`_vcg`, core adjustment); `tests/unit/auction/test_bundle_properties.py` (price ≤ bid, payment ≤ value) |
| So zero attention really costs zero? | Yes. The charge is the cleared price per attention-second × the attention measured during the play. No looks gives a recorded $0 charge | `app/modules/delivery/domain/charge.py` |
| Can a brand overspend? | No. To enter, a brand's budget must cover the worst case of a play; the amount is reserved while it airs, and charges are clipped to what's left | `auction/domain/rules.py`, `campaigns/application/live_book.py`, `campaigns/domain/budget.py` |
| Why is the basket better than one brand? | The console shows the same auction run with single brands only, next to the basket, and the share of viewers the basket reaches ("coverage") | `clearing.py` (solo counterfactual, coverage) |
| Who gets credit for a sale? | Every brand in the basket, for each QR redemption (one per phone, up to 6 phones per code, valid 10 minutes) | `app/modules/delivery/domain/redemption.py`, `delivery/application/redemptions.py` |

**Don't say:**
- "Bidding your true value is optimal." The core adjustment makes it only *approximately* truthful (`docs/auction-plan.md`). Say "second-price style, so overbidding doesn't pay".
- "Basket revenue is always higher." It isn't, per moment: in the baseline the lift is +5 % (`evals/baseline.json`). Lead with coverage, as ADR 0008 D5 says.
- That coverage is exact. It's an upper-bound estimate: segments overlap.

**If an economist pushes:** the core point is a heuristic (equal split, at most 64
rounds), not a unique optimum; daypart, speech and held-product boosts raise ranking
value without raising what a brand can be charged; the price per attention-second is
fixed when the auction clears, and both brands bill the same attention. All are known
and documented in `docs/auction-plan.md`.

## 4. "Isn't this creepy? Is it legal?"

Answer from [`privacy.md`](privacy.md) §4. The short version:

- **Not facial recognition.** No face model, no embeddings, and track ids are per-session counters.
- **What the AI sees:** only the people and products the detector found, with every head blacked out. Everything else is black. The backend verifies that before anything goes to Claude.
- **Nothing is stored:** no frames, no audio, no transcripts. There's a test that blocks every file write on the GPU service.
- **You can see it's on:** "Cámara activa" and "Micrófono activo" on the TV.
- **Minors:** only protected (they block alcohol and junk food), never targeted.
- **Still to do before a real venue:** signage, a legal basis and a privacy impact assessment under Ley 21.719 (privacy.md G7).

## 5. Business questions (have an answer ready)

| Question | Honest answer |
|---|---|
| Does the QR prove a sale? | It proves intent: a phone opened that basket's offer within 10 minutes. Real proof needs the store's checkout to scan the code, which is the first integration we'd build with a chain |
| Three strangers in a queue aren't a "group" | Right. The basket serves *different* needs side by side; the group-size price is a bonus when they are together |
| Unit economics per screen? | Work this out before the pitch: revenue per screen-hour against GPU, model calls and hardware. Claude tags at most one keyframe every 1.5 s, and only when the camera's own detections aren't enough (`app/modules/audience/domain/policies/keyframe_gate.py`) |
| Why would Coca-Cola let an auction pair it? | It already funds "buy both" deals through co-op budgets, and it only pairs with partners it approved. We make those deals per moment and measured |
| Are these brands your customers? | No. Real Chilean brands are used for realism, with no affiliation (see `logos/SOURCES.md`) |
