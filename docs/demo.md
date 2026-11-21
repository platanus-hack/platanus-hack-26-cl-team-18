# Demo plan and pitch

> **Doc status: LIVE.** The demo is the product this weekend. Every feature
> should make one of the moments below land better. If a task doesn't serve a
> moment, it's backlog. Product context: [`product.md`](product.md).

## 1. Setup

- **The "screen":** a large TV on a stand, with the webcam on top, running the
  `/screen` route full-screen in Chromium kiosk mode. Next to it, a small
  monitor looping **"what a normal screen shows"** (one static ad) for the
  side-by-side contrast.
- **The console:** a second monitor or the projector, showing `/console`.
  Judges watch the brain while the screen does its job.
- **The "counter":** a table set up as a convenience-store counter with:
  - **Props:** cups and cans printed with the fictional brands (Grano Café
    cup, Voltio can, Rayo can), a gym bottle and a laptop. Labels are **big,
    high-contrast and color-coded per brand** so the VLM reads them reliably.
  - **Clerk tablet** on `/clerk` (stock toggles).
  - **"Checkout":** a USB thermal printer (if we have one) that prints the
    moment receipt on redemption.
- **Two phones for judges** on `/advertiser`, joined by a QR on the console.
- **Backend on the laptop** (localhost) and a phone hotspot. Render hosts the
  public URL.
- **Lighting check** at the venue: webcam exposure matters more than model
  choice.

## 2. Console layout (the "money shot")

```
┌───────────────────────────┬──────────────────────────────┬─────────────────────────┐
│ WHAT THE SCREEN SEES      │ LIVE AUCTION                 │ NOW PLAYING             │
│ camera, faces blurred,    │ bids per brand (bars move)   │ mirrored creative       │
│ anonymous boxes + tags    │ chips: ✗ competitor ·        │ dwell stage: teaser →   │
│ "3 people · 2 looking"    │   ✗ minor-safe · ✗ not a     │   offer → close         │
│ group size · dwell        │   partner                    │ basket offer "Trío $X"  │
│ segments w/ confidence    │ winning basket + VCG prices  │ "why this ad" chips     │
├───────────────────────────┴──────────────────────────────┴─────────────────────────┤
│ HELD: Grano Café cup → boosts Miga · STOCK: Miga ✓ · CONTEXT: weekday morning     │
│ KPIs: coverage 83% (solo 41%) · revenue/h $X vs single-brand $Y (+Z%)              │
│       attention-seconds · charged $0 when nobody looked · redemptions 3 (both      │
│       brands credited) · glass-to-glass p50 1.2 s / p95 1.9 s                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Competitor exclusion, minor safety and privacy appear as **console chips**
during the beats, not as separate beats. The last **moment receipt** is always
visible in a corner: *"3 people · 2 looked · 7.4 AS · $0.21 · 1 redemption"*.

## 3. The 3-minute script (five beats, no more)

Setting: a **convenience-store counter** (our first market). Brands are
complementary products sold at that counter.

| t | Beat | What happens | What judges should feel |
|---|---|---|---|
| 0:00 | **1. Scene hook** | *"You're in line at a gas station with two friends. The screen above the register shows a car-insurance ad. Three hungry people, a coffee machine two meters away, and the screen has no idea."* Point at the static screen, then: *"Store screens are still blind. Watch ours."* | Recognition |
| 0:20 | **2. Basket ad** | Three people walk up (a gym bottle, a laptop, empty hands). In about 1 s: **"Trío post-gym: Voltio + Brasa Burger, $X"**. Teaser, then offer as they keep looking. Coverage 40 % → 90 %; chip: top burger bid ✗ competitor. | The core insight: N people, several brands, one offer sized to the group |
| 0:55 | **3. Complete the basket** | A judge picks up the **Grano Café cup**. About 1.5 s later: *"Your Grano needs a Miga. 2 min, $X."* Console: `HELD: Grano Café → boosts Miga`. | "It sees what I'm holding, not who I am" |
| 1:25 | **4. Bidding war** | Two judges on phones: one is **Voltio**, the other is its competitor **Rayo**. They push their bid sliders for the moment in front of the screen; the room watches the winner flip live. Competitor rule: they never share a basket. | "It's a real market, and I'm in it" |
| 2:00 | **5. Close → receipt → $0** | The ad reaches **close**: QR + "valid 10 min". A judge scans and redeems, the **receipt prints**: *"Voltio credited $0.12 · Brasa $0.09 · 7.4 AS"*. Everyone walks away; console: **"charged $0: nobody looked"**. Closing line: *"We don't sell screen slots, we sell moments. Brands pay only when people look, and the receipt proves the sale. We start with convenience-store counters."* | Proof, fairness, investable |

**Backup moments** (swap one in if a beat fails, or use if time allows; never
add a sixth beat):

| Moment | What happens |
|---|---|
| **Stock-out re-basket** | The "clerk" taps "Miga out of stock" on `/clerk`; the basket re-forms instantly without Miga |
| **"It heard me"** (speech mode) | Someone says "uf, qué hambre"; the burger bid jumps, with a voice-over |
| **Context switch** | The console toggles "hot afternoon"; cold-drink bids rise and the basket changes |
| **Safety** | A judge holds a toy stroller; the beer brand's top bid is blocked (✗ minor-safe chip) |
| **Queue mode** | Three people line up; the copy switches to *"Line's 2 min: grab a Grano Café, ready when you pay"* |
| **Conquest** (optional, edgy) | A judge holds a Rayo can; Voltio shows a "switch" deal |

**Sponsor angle:** add sponsors and the hackathon itself as advertisers in the
seed data (with mock creatives clearly labeled as mock). A judge seeing their
own company win a basket slot is memorable.

**Brands:** to avoid trademark issues, use fictional brands in complementary
convenience-store categories, with competitor pairs among them. For example:
energy drinks "Voltio" vs "Rayo", burgers "Brasa Burger" vs "Toro Burger",
coffee "Grano Café", pastry "Miga", colas "Cola Sur" vs "Pacífica", snack
"Crunchi", beer "Lúpulo Sur" (age-restricted), water "Agua Cumbre". Search
each name before the demo to make sure it isn't a real brand. Real logos only
with permission or clearly marked as a mock.

**Held products in the seed data:** Grano Café cup, Voltio can, Rayo can,
Agua Cumbre bottle. Each maps to a brand id the VLM must return in
`held_products[]`.

**Partner allow-lists in the seed data:** Voltio ↔ Brasa Burger, Voltio ↔
Crunchi, Grano Café ↔ Miga, Cola Sur ↔ Brasa Burger, Agua Cumbre ↔ Crunchi.
Every allowed pair gets an approved bundle loop.

**Group-size offers in the seed data:** each basket defines offers for 1, 2
and 3+ people ("Combo", "2×1", "Trío").

**Video note:** every approved demo basket has a pre-generated AI video loop,
approved by both brands, so the creative is full-motion from the first frame.
If a judge asks "is that generated?", say yes: generated for this brand
pairing and approved before the demo, then composed live for this group (offer,
copy, voice, QR).

## 4. Demo insurance (build these, they save demos)

The [audience replay runbook](simulator.md) runs the local WebSocket-to-player
slice with co-brand, minor-safety and no-fill scenes.

1. **Simulator mode:** console buttons inject scripted audience snapshots
   ("gym duo", "family with kid", "office lunch group", "teen gamers"). Same
   backend path as the camera, so it is a real test of the loop.
2. **Replay mode:** a recorded webcam video file as the camera source
   (a video file fed into the perception agent instead of the webcam), for
   when the room is too dark or too crowded.
3. **Warm cache:** pre-generate creatives for every scripted scenario before
   the demo. The live run then mostly hits the cache (fast), and live
   generation is the bonus.
4. **Freeze-proof screen:** never blank; a house ad if anything fails.
5. **One-command start:** `make demo` boots Postgres, backend, web, and
   seeds data. Rehearse at least 3 full runs on the demo hardware.
6. **Offline fallback for AI:** if the provider is down, use YOLO tags and
   stock copy, and the demo still runs (slightly less magic). If the GPU
   service is slow, the local detector takes over automatically (ADR 0007).
7. **Speech in a noisy room:** use a directional or lapel mic for the
   presenter, and keyterm-prompt the exact demo phrases. The console has a
   "simulate utterance" button that sends the same `audience.utterance`.
8. **QR redemption fallback:** the offer page is served by the backend on the
   laptop's hotspot IP, and the console has a "simulate redemption" button in
   case the judge's phone can't reach it.
9. **Held product not recognized:** the console has a "simulate held product"
   button per prop. Labels are color-coded so a brand can also be inferred from
   color.
10. **Bidding war phones:** `/advertiser` is served on the hotspot IP and
    joined by QR from the console. If a phone fails, the console has the same
    sliders.
11. **No printer:** the receipt also shows full-screen on the console for
    5 s.
12. **Network:** bring a phone hotspot. Scribe, TTS, VLM and video all need
   internet, and everything they produce for the scripted demo is
   pre-generated.

## 5. Pitch narrative

The framing: **"A physical screen is watched by groups, but sold like an
online ad: to one brand, for one imagined viewer."** Keep it throughout.

Open with **the scene** (beat 1), not a statistic.

Lines that work:
- "Every store screen becomes a shelf you can rent by the second."
- "Online ads learned to see the customer 20 years ago. Store screens are
  still blind."
- "We don't sell screen slots. We sell moments."
- "One screen, three people, three needs. Why sell it to one brand?"
- "If nobody looks, the brand pays zero."
- "Basket ads: products that go together, offered to people who are together."
- "We don't know *who* you are. We know *what this moment* needs."

Vocabulary: say **"presence and attention sensing"**. Never say "facial
recognition" or "we track people".

Numbers: market-size and DOOH stats in the pitch must be **verified with a
source before use**. Don't put a number on a slide that nobody checked.

## 6. Likely judge questions and answers

| Question | Answer |
|---|---|
| "Isn't this creepy / legal?" | No identity, no face recognition, nothing stored, faces blurred before anything goes to a third party, no sensitive or emotion inference, minors only protected and never targeted, and redemptions are anonymous. Designed for Ley 21.719, the GDPR and the EU AI Act. |
| "Would brands accept an AI pairing them with another brand?" | Only pre-approved partners. Every basket's video is generated ahead of time and approved by both brands; only the copy is live, inside each brand's guardrails. Logos are never generated. |
| "Why would brands share a screen?" | They already fund joint "buy both" offers through co-op budgets. We make those per moment and measured, and each brand pays only for the viewers relevant to it. |
| "How do you price a basket fairly?" | VCG: each brand pays the value it displaced from others, per attention-second. Bidding your true value is optimal, and zero attention costs zero. |
| "Does it sell anything?" | Each basket has its own QR / promo code. Redemptions are credited to both brands; you just saw one. In a store, the code is redeemed at the checkout a few meters away. |
| "Latency?" | YOLO on a GPU (local fallback), an in-memory auction under 30 ms, a pre-approved creative under 300 ms, AI upgrades in place. Live p50/p95 on the console. |
| "Go-to-market?" | Convenience stores and gas-station shops first: queues give dwell, purchases are complementary impulse buys, checkout gives attribution, and one chain means hundreds of identical locations. Take rate per auction plus a per-redemption fee. |
| "Unit economics?" | A tagging call costs a fraction of a cent. Basket videos are generated once and reused across thousands of plays. |
| "What about generative video?" | Baskets are a small, pre-approved set, so we pre-generate one loop per pairing, and turbo models (a few seconds per clip) fill gaps in the background. |
| "How do you recognize what I'm holding?" | Objects only: the vision model reads the product label in a face-blurred frame. It never identifies the person. |
| "Why would a store want this?" | Its screens turn from a cost into revenue, they always fill (the store's own products can bid too), and it only advertises what's in stock. |
| "You're listening to people?" | Only in opt-in speech mode, with signage and a visible indicator. Audio and transcripts are never stored, only topics with a short TTL. |

## 7. Demo idea backlog (candidates, not committed)

Ideas that could make the demo better. **None of them is in scope yet.**
Promote one into §1–4 (and into `product.md` §13) only when the five beats
already run reliably, and swap it in rather than stacking it on. Rough value
order within each group.

### 7.1 Strongest candidates

| Idea | What it is | Why it could be good | Cost |
|---|---|---|---|
| **Coverage lighting** | On the console's camera view, each person's outline lights up in the color of the brand covering them: 1 of 3 lit with a solo ad, all 3 with the basket | Makes the core thesis visible in one second, without ad knowledge | Low (console overlay from the coverage computation) |
| **Physical "AI off" button** | A USB arcade button on the counter toggles the screen between a dumb static loop and the live system | A judge feels the difference; stronger than a second monitor | Low (a key-press mapped to a WS message) |
| **Front-row group moment** | "Everyone in the first row, stand up": 8 people, and the screen shows *"¿Junta de 8? Promo junta: 8 Voltio + 2 pizzas."* | Proves crowd scale and group-sized offers | Very low (a `3+` / `8+` offer band in seed data) |
| **The pitch counts its own results** | Close on live session numbers: *"During this pitch: 14 moments, 9 baskets, 3 redemptions, $4.20 earned; the static screen earned $0.60."* | Real, from this room, nothing made up | Low (a session KPI view) |
| **60-second table version + reset button** | A short version for roaming judges (basket ad → complete the basket → receipt) and a console "reset demo" that clears session KPIs, receipts and budgets | Judges will see it many times; every round starts clean | Low |

### 7.2 Visuals

| Idea | What it is | Cost |
|---|---|---|
| **X-ray mode** | The blurred camera feed with pose skeletons, a "looking" line from each head and floating tags, on the projector | Low–medium |
| **Animated auction** | Brands as cards flying into a basket; losers fade with stamps (✗ COMPETITOR, ✗ MINOR-SAFE, ✗ OUT OF STOCK) | Medium |
| **Money jars** | Two jars on the console, "static screen" vs "us", filling per attention-second | Low |
| **Giant latency timer** | Every swap flashes "1.18 s" big on the projector for a second | Very low |
| **Audit any moment** | Click a receipt to replay its exact auction (bids, exclusions, prices) | Low (the auction record already stores it) |

### 7.3 Staging

- **Floor marks:** tape an X at the camera's sweet spot (about 1.5–3 m).
- **Set dressing:** a mini shelf with the fictional products, price tags, a
  counter cloth and a fake register, so it reads as a store.
- **Fixed team roles:** narrator (never touches anything), operator
  (console, resets, simulate buttons), clerk (`/clerk`, props), shopper
  (walks in on cue).
- **Audio setup:** voice-over volume set for a noisy hall, speakerphone aimed
  at the shoppers, no music under the narration.

### 7.4 Story

- **Same people, different hour:** the same three shoppers with "morning",
  then "night" toggled: coffee + pastry becomes snacks + soda.
- **The pitch video made by the product:** a 10-second in-store clip
  generated with our own video pipeline, presented as such.
- **Map beats to judging criteria:** innovation (basket thesis, coverage
  lighting), execution (latency timer, unplug test), impact and business
  (receipts, the "$0" charge, the pitch's own numbers), Physical World track
  (real sensors, printer and button in the loop).

### 7.5 Credibility under pressure

- **Unplug test (Q&A):** pull the network cable or kill the GPU service live.
  The local detector takes over and the screen is never blank (ADR 0007).
- **One architecture slide, 10 seconds, only if asked:** camera → GPU →
  auction → screen, with the latency per step.

### 7.6 Demo-day operations

- **`make preflight`:** green/red board on the console for camera fps, GPU
  round trip, each provider, cache warm, printer, phones joined, volume.
- **A recorded perfect run** as the last resort, narrated live if everything
  fails.
- **Kit:** two hotspots, power strip, HDMI and USB-C adapters, gaffer tape,
  spare printer paper, spare printed props.
- **Five full rehearsals**, one with deliberate failures (camera unplugged,
  GPU killed, a phone that won't join) to practice recovering.
