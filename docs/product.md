# Product: basket ads for physical screens

> **Doc status: LIVE.** This is the product source of truth. Read it before
> building a feature. Runtime design is in [`architecture.md`](architecture.md),
> the stack in [`stack.md`](stack.md), the demo plan in [`demo.md`](demo.md),
> and the glossary in [`../CONTEXT.md`](../CONTEXT.md).

## 1. One-liner

**A screen that sees the group in front of it, auctions the moment among
brands whose products go together, and shows them a joint "basket" offer in
under two seconds. Brands pay only if people actually looked, and the QR
proves the sale.**

Short form: *"We don't sell screen slots. We sell moments."*

Framing lines (use consistently):
- *"Every store screen becomes a shelf you can rent by the second."* Retail
  people think in shelf space and trade promotions, so this ties us to the
  money brands already spend in stores, not only ad budgets.
- *"Online ads learned to see the customer 20 years ago. Store screens are
  still blind."*

Spanish one-liner (voting page): *"Pantallas que ven al grupo frente a ellas,
subastan el momento entre marcas que se complementan y muestran una oferta
conjunta en menos de 2 segundos. Las marcas pagan solo si las miraron, y el QR
prueba la venta."*

## 2. The problem

> **A physical screen is watched by groups, but sold like an online ad: to one
> brand, for one imagined viewer.**

**The scene (open with this, not a statistic):** *"You're in line at a gas
station with two friends. The screen above the register is showing a
car-insurance ad. Three hungry people, a coffee machine two meters away, and
the screen has no idea."*

**Three stakeholders, three pains, one loop:**

| Stakeholder | Pain today |
|---|---|
| **Venue** | In-store screens are a cost, not revenue. Many show house content because nobody can sell the inventory, so fill is low. |
| **Brand** | In-store spend is the least measurable part of its marketing. It pays for placement and never learns whether anyone looked or bought. |
| **Shopper** | Irrelevant noise at the exact moment they're deciding what to buy. |

Digital out-of-home (DOOH) screens are everywhere: convenience stores, gas
stations, food courts, malls, metro stations. Underneath those pains, their
model is broken in ways online advertising fixed long ago:

1. **A screen is seen by N people, but sold to 1 brand.** This is the core
   insight. Online, one impression goes to one person. Physically, a group of
   2 to 5 people looks at the same screen with *different* needs. A single-brand
   ad wastes most of that attention.
2. **Brands pay for guesses.** Inventory is priced from traffic estimates.
   Audience-measurement cameras exist, but they only *report*; nobody prices,
   targets or bills on what was actually seen.
3. **One ad for everyone, for weeks.** The same loop plays to a family, a gym
   group or an office worker.
4. **No proof of sale.** The screen can't tell a brand whether its ad sold
   anything.
5. **Creative production is the bottleneck.** Nobody can produce an ad for
   every audience and product combination by hand.

Measurement alone is table stakes. **What's new is the loop:** measure →
auction among several brands → generate the joint offer → prove the sale.

## 3. The thesis: basket ads

> **Physical attention is a shared, multi-person resource. Sell it like one,
> with products that go together.**

We don't put two random logos side by side. A **basket ad** is a joint offer
for **complementary products**: coffee + pastry, burger + soda, energy drink +
gaming, sunscreen + water. That's exactly what consumer brands already fund
through **co-op / trade-marketing budgets** ("buy both, save 20 %"), so the
money exists. We make it per moment, targeted and measured.

Example: the group in front of the screen is {just out of the gym, teen
gamer, hungry office worker}. A single energy-drink ad is relevant to 1 or 2 of
them. The **"post-gym combo"** (energy drink + burger) is relevant to all three.
Each brand pays only for the viewers relevant to it, so the screen earns more
per second while each brand pays less per relevant viewer. We call this
**audience coverage**, and it's the metric the demo sells (§8).

**Why brands accept it:** brands only ever pair with **partners they approved
in advance**. Each bundle's video loop is generated ahead of time, so **both
brands approve every pairing and its creative before it runs**. Only the copy
is live, and it stays inside each brand's guardrails (approved claims, no
competitor names, real logos only).

## 4. What makes a basket ad different

| Feature | What it means | Why only a physical screen can do it |
|---|---|---|
| **Group-size-aware offers** | "3 of you? Trío for $X", or "for 2: 2×1 coffee". The offer scales with the people looking | Online ads never see the group |
| **Pay only for attention** | Brands are billed per measured attention-second. **If nobody looks, the brand pays $0** | The camera sees who looked |
| **Closed loop to sales** | Each basket ad carries a QR / promo code. A redemption credits **both** brands and shows live in the console | Ties a physical moment to a purchase |
| **Ad that evolves while you stay** | Dwell stages: **teaser** (0–3 s, big visuals) → **offer** (3–8 s, the joint deal + group size) → **close** (8 s+, QR + "2 min away") | Rewards attention; the ad visibly reacts to you |
| **Moments, not slots** | Inventory is the *moment*: a measured audience, a cleared price and a receipt (proof of play + attention + redemptions) | Slots are a schedule; moments are what actually happened |
| **Moment receipts** | Every moment produces a receipt: *"3 people · 2 looked · 7.4 attention-seconds · $0.21 · 1 redemption"*. Brands get proof, the venue gets its revenue-share line | Online receipts are per click; ours are per real-world moment |

### 4.1 Convenience-store features (the vertical)

| Feature | What it does | Notes |
|---|---|---|
| **Complete the basket** | Recognizes the product a person is *holding* (never the person) and offers its complement: holding a Café Patio cup → *"Tu Café Patio pide una Miga. A 2 minutos, $1.990."* "Frequently bought together", for the physical world | Demo props carry ArUco markers read in the hot path in about 7 ms (ADR 0008 D2); the VLM reads labels on the face-blurred keyframe in parallel (`held_products[]`). A held product boosts its approved partners |
| **Stock-aware baskets** | Only advertise what's in stock and on promo in *this* store. A stock-out makes a campaign ineligible instantly and the basket re-forms | A per-screen stock flag set from a clerk tablet (`/clerk`); in production, the store's inventory feed |
| **Context-aware bids** | Time of day, weather and day of week multiply bids: morning → coffee + pastry, hot afternoon → cold drinks + ice cream, night → snacks | Pure policy: a context multiplier table per category |
| **Queue mode** | Detects people lined up (aligned tracklets, low movement) and fits the message to the wait: *"Fila de 2 minutos: pide tu Café Patio, listo al pagar."* | Queue length is an aggregate signal in the moment |
| **Retailer house bids** | The store's private label or high-margin items bid too | Guarantees fill and serves the retailer's own goals |
| **Expiring offers** | The QR code is valid for 10 minutes | Urgency plus clean attribution: redemption follows the moment |
| **Conquest offers** (optional) | A brand bids extra when a competitor's product is held ("switch" deal) | A real practice, but some audiences find it aggressive; off by default |
| **Gas-pump screens** (production) | Vehicle class (truck, motorbike, family car) tunes the basket: truckers → energy drink + hot dog | Vehicle class only; **license plates are never read** |

## 5. How it works (the loop)

```
 SEE  ──►  UNDERSTAND  ──►  AUCTION  ──►  COMPOSE  ──►  PLAY  ──►  MEASURE ──►  PROVE ─┐
 camera     group size,      compatible     basket ad:     <100 ms   attention-   QR    │
 + mic      looking, IAB     brands win     group offer,   swap,     seconds per  redeem│
            interests,       together,      dwell stages,  never     brand →      → both│
            speech topics    VCG prices     real logos     blank     billing      brands│
   ▲                                                                                     │
   └──────────────────────────── next moment (audience changes) ◄──────────────────────┘
```

1. **See and hear.** A perception agent next to the screen sends frames to our
   own GPU service running YOLO (ADR 0007). It detects people and objects
   (coffee cup, gym bottle, laptop, skateboard, stroller), tracks people
   anonymously, and reads pose keypoints to tell who is looking, for how long
   (dwell) and from how far. In opt-in **speech mode**, speech is transcribed in
   real time (ElevenLabs Scribe) and turned into topics: someone saying "qué
   hambre" is the strongest intent signal a screen can get.
2. **Understand.** Every second or two, a face-blurred keyframe goes to a
   vision-language model (Claude), which returns *anonymous* interest tags
   mapped to the **IAB Audience Taxonomy**, each with a confidence score. The
   result is an **audience moment**: group size, looking count, segments,
   safety flags. It is debounced, so a passer-by doesn't trigger a new auction.
3. **Auction.** When the moment changes in a way that matters, the exchange
   runs a **bundle auction**. Each campaign bids per attention-second for the
   segments it wants, and the auction picks the **best compatible basket of 1
   or 2 brands** (trio layouts later, ADR 0008 D4), subject to:
   - **pre-approved partners only** (each brand's co-brand allow-list),
   - **competitor exclusion** (never two burger brands, never two colas),
   - **brand-safety policies** (no alcohol, gambling or junk food when a minor
     is likely present),
   - **budgets and pacing**.
   Prices are VCG: each winner pays the value it displaced, so bidding your
   true value is the best strategy.
4. **Compose.** The winning basket becomes a **creative spec**: a layout, each
   brand's real logo and product assets, the bundle's pre-approved video loop,
   the **basket offer** sized to the group ("Combo post-entreno · Voltio + Café Patio
   por $3.490"), copy for each **dwell stage**, a spoken voice-over, and a **QR / promo
   code** unique to this basket. Fresh AI copy, voice or video can upgrade it
   seconds later. **Logos are never AI-generated.**
5. **Play.** The screen preloads the next creative and swaps with a crossfade,
   then steps through the dwell stages while people keep looking. It is never
   blank.
6. **Measure and bill.** The system counts **attention-seconds** per brand.
   Each brand pays its cleared price × measured attention; zero attention costs
   zero.
7. **Prove.** A QR scan or promo redemption is recorded against the basket and
   credited to every brand in it, and the console shows it live.

## 6. Users and what each one gets

| User | Wants | Gets |
|---|---|---|
| **Brand (consumer goods / QSR)** | Reach the right people; prove sales | Basket ads with approved partners, pay-for-attention, redemptions credited per brand, co-op budget actually measured |
| **Venue / screen owner** (first: convenience stores and gas-station shops) | More revenue per screen | Several brands per moment, higher fill, revenue vs. a single-brand baseline |
| **People in front of the screen** | Not be creeped out; get something useful | A relevant deal for *their group*, no identity tracking, a visible privacy indicator |
| **Retail-media network / agency** | Measurable, shoppable inventory | Moments with audience, price and sales receipts; exposable over OpenRTB DOOH later |

## 7. Privacy and trust are product features

This is the first question every judge and buyer will ask. Answer it by design:

- **Frames go only to our own GPU service** (TLS, processed in memory, never
  stored). No frame or recording is ever stored anywhere.
- **No identity, ever.** No face recognition, no face embeddings, no
  re-identification across screens or days. We track *anonymous tracklets*
  ("person #3, here for 8 s"), never *who* someone is.
- **Faces blurred before anything goes to a third party.** The keyframe sent
  to the VLM is face-blurred first.
- **Forbidden inferences.** Never infer or store race/ethnicity, religion,
  health, sexual orientation, political views, emotion, or precise age. The
  only age-related signal is a coarse **minor-likely** flag, used *only* to
  protect minors (block alcohol, gambling, junk food), never to target them.
- **Aggregate only.** Stored audience data is aggregate segments with counts
  and confidences, not per-person profiles.
- **Speech mode is opt-in.** It is off by default, turned on per venue with
  signage, and shows a visible "listening" indicator. Audio and transcripts
  are never stored; only topics with a short TTL survive. There is no speaker
  identification. "Talk to the screen" (push-to-talk) is consent by action.
- **Redemptions are anonymous.** A QR code identifies the basket and the
  moment, never the person.
- **Objects, not people.** Product recognition ("holding a Café Patio cup")
  describes objects only. Vehicle detection is class only, and **license
  plates are never read**.
- **Transparency mode.** An on-screen indicator, plus a "what this screen
  sees" view of the anonymous tags. In the demo this is a feature.
- **Regulation.** Chile's Ley 21.719 and the GDPR treat biometric data as
  sensitive. The EU AI Act bans inferring sensitive attributes from biometrics
  and restricts emotion recognition. The design above (no identification, no
  sensitive or emotion inference, no storage, aggregation) is the defensible
  position. Get legal review before a real deployment.

Pitch vocabulary: say **"presence and attention sensing"**, never "facial
recognition" or "we track people".

## 8. Metrics that sell it

| Metric | Definition | Why it matters |
|---|---|---|
| **Audience coverage** | Share of looking viewers for whom at least one brand in the basket matches a segment above a confidence threshold | The thesis in one number: single-brand ≈ 30–50 %, basket ≈ 80 %+ in the demo |
| **Attention-seconds (AS)** | Σ over people of seconds facing the screen while a creative plays | The billable unit; zero AS = zero charge |
| **Revenue per screen-hour** | Σ charges / hour | Venue's headline, shown next to a *single-brand counterfactual* |
| **Basket lift** | (basket revenue − best single-brand revenue) / best single-brand revenue | Proves basket ads monetize better |
| **Redemptions per 1,000 AS** | QR scans or promo redemptions per thousand attention-seconds, per basket | Proof of sale; the retail-media headline |
| **Dwell-stage reach** | Share of plays that reached the *offer* and *close* stages | Shows that the ad holds attention |
| **Glass-to-glass latency** | Audience change seen by the camera → new creative visible | Proves "real time"; target p50 < 1.5 s, p95 < 2.5 s |
| **Fill rate** | Moments with a paid creative / all moments | Venue's other headline |

The counterfactual is cheap to compute: the auction already knows the best
single-brand outcome. **Show it live.**

## 9. Go-to-market: one first market

**Beachhead: convenience stores and gas-station shops.**

- **Queues give dwell time** (payment, coffee machine, fuel pump), so the dwell
  stages actually play out.
- **Purchases are impulse buys of complementary products** (coffee + pastry,
  energy drink + snack, water + sunscreen): the natural basket.
- **Many consumer brands** compete for the same counter, with co-op budgets
  to spend.
- **Checkout gives attribution:** the promo code is redeemed at the POS a few
  meters away, so we can prove the sale.
- A chain is one buyer with hundreds of identical locations to roll out to.

**Backup market: food courts** (lots of groups, strong group-size offers), but
tenants compete with each other, so baskets are harder to form.

Later: supermarkets (retail media with in-store inventory), gyms, malls,
metro.

## 10. Business model

- **Take rate** on every cleared auction (e.g. 15–25 % of media spend), split
  with the venue. Pricing unit: **CPAS** (cost per thousand attention-seconds)
  instead of CPM, with zero charge for zero attention.
- **Performance tier:** an extra fee per redemption, for brands that want to
  pay for sales.
- **Hardware/SaaS kit for venues** to retrofit screens they already have
  (camera + speakerphone + an edge box, or a shared GPU per venue).
- **Data product (aggregate only):** venue audience analytics (group size and
  segments by hour), sold to the venue.

## 11. Competitive framing

| Approach | What it does | What it misses |
|---|---|---|
| Classic DOOH | Fixed loops, sold by schedule | No audience, no targeting, no proof |
| Programmatic DOOH (OpenRTB DOOH) | Buys screen-time by daypart and location | Audience is modeled, not observed; one brand per slot |
| Audience-measurement cameras | Count viewers for reporting | Measure but don't act: no auction, no creative, no sale proof |
| Online ad exchanges (Google, Meta, Topsort) | Real-time auction per impression | Assume 1 impression = 1 person |
| **Us** | Measure → basket auction → generate → attention billing → sale proof, per moment | Needs tuning in real venues and a well-handled privacy story |

**Our wedge:** basket auctions on measured, multi-person attention, with
pre-approved creative that renders instantly and a code that proves the sale.

## 12. More product ideas (backlog, in rough value order)

1. **Distance-aware creative.** Far away: big logo, few words. Close up:
   details, QR code, price. Distance comes from pose keypoints.
2. **Live inventory, weather and local-event feeds** replacing the demo's
   clerk tablet and console context toggle (see §4.1).
3. **"Talk to the screen" (opt-in).** Push-to-talk voice Q&A answered by the
   brand's agent (approved claims and offers only) and spoken back by
   ElevenLabs TTS.
4. **Creative optimization loop.** A multi-armed bandit over creative variants
   (layout, copy tone, offer framing) rewarded by attention and redemptions,
   per segment.
5. **Partner marketplace.** The exchange suggests basket partners from audience
   co-occurrence ("energy drinks and burgers share 38 % of your audiences"),
   and brands approve them with one click.
6. **Sequential storytelling** across screens along a path, coordinated at
   aggregate level, never by tracking individuals.
7. **Venue analytics:** group-size and segment heatmaps by hour, for staffing
   and pricing.
8. **OpenRTB DOOH bridge** to carry existing demand from day one.
9. **Edge auction with budget sync** for sub-50 ms decisions without
   connectivity.

## 13. Hackathon scope (what we build in 36 h)

**Must (the demo can't work without it):**
- Perception agent (YOLO on the GPU service + local fallback, tracking, looking,
  dwell) with face-blurred keyframes; browser player and console.
- VLM tagging to IAB segments with confidence.
- Audience-moment debounce and change detection, including **group size**.
- Bundle auction with the partner allow-list, competitor exclusion,
  minor-safety policy and VCG prices.
- Basket creative spec: 3 layouts, real logo assets, **group-size-aware offer**,
  **3 dwell stages** (teaser → offer → close), a **QR code per basket**, a
  crossfade.
- Attention-seconds → charges (`Money`) per brand, **zero charge for zero
  attention**.
- **QR → offer page → redemption** recorded and credited to every brand in the
  basket, shown live in the console, with a **moment receipt**.
- **Complete the basket:** `held_products[]` from the VLM boosts the held
  product's approved partners (props: cups and cans printed with the
  fictional brands).
- **Stock-aware baskets:** a `/clerk` tablet page with stock toggles; a
  stock-out re-forms the basket immediately.
- **Live bidding war:** an `/advertiser` phone page (joined by a QR on the
  console) where a judge controls one brand's bid multiplier in real time.
- Console: live tags, auction book, winning basket, coverage, revenue vs.
  single-brand counterfactual, redemptions, glass-to-glass latency.
- Seed data: about 8 to 12 brands in complementary categories, competitor
  pairs among them, partner allow-lists, offers per group size, and campaigns
  with segment bids.
- Pre-generated video loop per approved demo basket, plus voice-overs per
  dwell stage (cold path, seeded before the demo).
- **Simulator / replay mode** as demo insurance (scripted audiences, no camera).

**Should:** a USB thermal printer at the "checkout" printing the moment
receipt on redemption; context-aware bids with a console toggle; speech mode
(Scribe → topics → re-auction, the "it heard me" moment); expiring offer
codes; queue mode; warm video upgrade with a turbo model; distance-aware
layout. **Could:** "talk to the screen"; conquest offers; retailer house bids.

**Won't (this weekend):** video generation in the hot path, advertiser
self-serve beyond seed data and the approval flag, multi-screen fleet, OpenRTB
bridge, real POS integration (the offer page stands in for checkout), payments.
