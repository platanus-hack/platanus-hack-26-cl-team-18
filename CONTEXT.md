# Domain glossary

Use these words in code, tests, commits and docs. If a term is contested or
missing, add it here in the same PR. Product: [`docs/product.md`](docs/product.md).
Contexts: [`docs/architecture.md` §5](docs/architecture.md#5-bounded-contexts).

| Term | Meaning | Not to be confused with |
|---|---|---|
| **Screen** | A physical display device with camera (and optionally mic) that runs the player. Aggregate in `screens`. | "Device" in general prose is fine; in code it's `Screen` / `ScreenId`. |
| **Tracklet** | An anonymous, short-lived track of one person within one screen's view (`tracklet_id` local to the device session). Has dwell, looking, distance band. | A person identity. Tracklets are never linked across sessions or screens. |
| **Looking / attention** | A tracklet whose head pose faces the screen within a threshold. | Merely present in frame. |
| **Attention-second (AS)** | One looking person for one second while a creative plays. The billable unit. | Impression (we don't count passers-by). |
| **Audience snapshot** | One raw perception reading sent by a screen (`seq`, `measured_at`, counts, tracklets, objects, sound scene). Append-only fact. | Audience moment. |
| **Audience moment** | The debounced, stable interpretation of who is in front of a screen now: people, looking, segments with confidence, safety flags. Changes trigger auctions. | A snapshot (raw, noisy). |
| **Audience moment snapshot** | Immutable audience facts published for an auction, including aggregate counts, segment estimates, timing and safety. | Audience snapshot, which is a device reading. |
| **Segment** | An interest/audience category from the **IAB Audience Taxonomy** with a viewer count and confidence. Our only targeting vocabulary. | Raw VLM text; it's mapped to IAB ids at the anti-corruption layer. |
| **Segment evidence** | One source's claim (vision, sound or vlm) that N looking viewers match a segment, with a confidence and an expiry. Fused per segment into a segment estimate (noisy-OR across sources). | A segment estimate (the fused result the auction sees). |
| **Cue rule** | A catalog entry mapping a perception label (COCO class, sound-scene class) to an IAB segment with a weight. The only way a free-text label becomes a segment. | A segment; a prop marker (`product:<slug>`, a held product). |
| **Speech mode** | Per-screen opt-in where speech is transcribed (Scribe) to extract topics. Off by default; visible indicator. | Sound scene (always on, no speech content). |
| **Utterance** | One committed transcript segment from speech mode. **Ephemeral**: never persisted or logged. | Speech topic. |
| **Speech topic** | An IAB segment extracted from utterances, with confidence, intent strength and a TTL. Merged into the moment with `source=speech`. | Raw transcript text. |
| **Perception agent** | The local Python process (`edge/`) that runs camera/mic perception and emits snapshots. Infrastructure only: emits facts, makes no decisions. | The player (`web/` `/screen`). |
| **Minor-likely flag** | A coarse safety signal that a child or teen is probably present. Used only to *block* age-restricted categories. | Age targeting (forbidden). |
| **Forbidden inference** | Race/ethnicity, religion, health, sexual orientation, politics, precise age, identity. Never requested from models or stored. | n/a |
| **Advertiser** | The paying organization. Owns brands and campaigns. | Brand. |
| **Brand** | A marketed name with a category, a brand kit and competitor/co-brand rules. | Advertiser (one advertiser can have many brands). |
| **Category** | Brand's product category (IAB product taxonomy-ish, e.g. `energy_drink`, `fast_food_burger`). Competitor exclusion is per category. | Segment (audience side). |
| **Brand kit** | Approved assets: vector logo, product cutouts, colors, fonts, approved claims, stock copy, optional pre-rendered loops. The only visual source for a brand. | AI-generated images (never contain logos). |
| **Campaign** | A brand's buying intent: segment bids (Money per AS), budget, pacing, schedule, co-brand preferences. | Creative. |
| **Bid** | Max price per attention-second for viewers in a segment. | Price (what is actually paid). |
| **Bidder** | The auction's immutable, read-only view of one campaign at clearing time: segment bids, live multiplier, budget left, partner allow-list, category. | Auctioneer (the engine that decides); Advertiser (the organization). |
| **Compatibility rule** | Competitor exclusion, co-brand allow/block lists, affinity bonuses. Pure `Specification`s. | Brand-safety policy. |
| **Brand-safety policy** | Moment-dependent eligibility rule (e.g. no alcohol when minor-likely). Pure `Policy`. | Compatibility rule (pairwise between brands). |
| **Ad opportunity** | The chance to fill a screen for the current moment with a given set of layouts. Input to an auction. | Slot. |
| **Layout** | A creative template with N slots and screen-share weights: `solo` (1), `duo` (2), `trio` (3). | Creative. |
| **Slot** | One brand's position within a layout. | Ad opportunity. |
| **Bundle** | The set of 1–3 compatible brands that win one ad opportunity together. | Campaign. |
| **Basket ad** | The product name for a bundle's creative: a joint offer for complementary products, sized to the group. | "Co-branding" in general (two random logos). |
| **Partner allow-list** | The brands a brand has pre-approved to share a basket with. A pair is eligible only if each is on the other's list. | Competitor exclusion (a block, not an approval). |
| **Basket offer** | The deal for an approved pairing per group-size band (`1`, `2`, `3+`), e.g. "Trío $X". Price and claims come from the brand kits only. | AI copy (phrasing only). |
| **Group-size band** | `1`, `2` or `3+`, from the moment's looking count. Selects the basket offer. | People present (includes not-looking). |
| **Dwell stage** | `teaser` → `offer` → `close`. The creative advances as looking tracklets keep dwelling. | Playback (the whole play). |
| **Held product** | A branded product a person is holding, read from the face-blurred keyframe (`held_products[]`). Boosts its approved partners ("complete the basket"). Describes the object, never the person. | A segment (audience interest). |
| **Complete the basket** | The feature that offers a held product's complement (Café Patio cup → Miga pastry). | Conquest offer (targets a *competitor's* held product; off by default). |
| **Stock flag** | Per-screen in-stock state per brand, set from `/clerk`. Out of stock = ineligible. | Budget exhaustion. |
| **Context multiplier** | Pure bid multiplier by daypart × weather × category. | Live multiplier. |
| **Live multiplier** | Bounded bid multiplier a judge or advertiser sets from `/advertiser` (the bidding war). Recorded as a command. | A bid. |
| **House bid** | A campaign owned by the venue for its own products; competes like any other. | House ad (unpaid fallback). |
| **Queue** | Aggregate signal: people lined up (aligned, low-movement tracklets) with an estimated wait. | Group (people looking together). |
| **Moment receipt** | The per-moment record: people, looking, AS, charge per brand, redemptions. Shown on the console and printed on redemption. | Charge (one ledger row). |
| **Moment (as inventory)** | What we sell: a measured audience moment with its cleared price and receipt (proof of play, attention, redemptions). | A time slot. |
| **Auction** | One run of the bundle auction for an ad opportunity. Immutable once cleared; records bids, exclusions with reasons, winner, VCG prices, single-brand counterfactual. | n/a |
| **Clearing price** | What each winning brand pays per AS (VCG, floored at reserve). | Bid. |
| **Reserve price** | Minimum bundle value to fill; below it, a house ad plays (no-fill). | n/a |
| **Single-brand counterfactual** | The best solo outcome the same auction would have produced. Used for co-brand lift. | n/a |
| **Creative spec** | JSON description of what to render (layout, slots → assets, copy, palette, motion, QR, optional hero image). Data, not pixels. | Brand kit. |
| **Bundle loop** | A pre-generated, loopable AI video clip for one compatible bundle (or solo brand), with no logos or text in it. Stored in the media cache. | The creative (the loop is one layer). |
| **Voice-over** | A short spoken line for a creative (ElevenLabs TTS), cached by (copy hash, voice id, model). Brand voice for solo creatives, neutral narrator for co-brand. | "Talk to the screen" replies. |
| **Media asset** | A cached generated file (voice-over, video loop, hero image) with provenance (`model_version`, prompt hash). | Brand-kit asset (human-approved). |
| **Creative upgrade** | Replacing a playing template creative in place with AI copy/hero image once ready (progressive enhancement). | A new auction. |
| **Creative brief** | Everything a copy generator may know about one moment: basket brands, angle, the offer for the party size, names to avoid, stage limits. No personal data, no utterance text. | The creative spec (the brief is input, the spec is output). |
| **Angle** | The need the copy answers: hunger, energy, coffee break, post-workout, social, queue, complete-the-basket, generic. Decided by a pure policy before any model runs. | Segment (an audience fact; the angle is a creative choice). |
| **Copy guard** | The pure rules a draft must pass to air: approved price only, no competitor names, every basket brand named, party size matches, no restricted words with minors, no claims or inferences. | Prompt instructions (advisory; the guard is enforced). |
| **House ad** | The non-paid fallback creative. The screen is never blank. | n/a |
| **Playback** | Proof of play for one creative on one screen: start/end and measured AS per slot. | Impression. |
| **Charge** | Append-only ledger row: brand, auction, AS, clearing price, amount (`Money`). Zero AS yields an explicit zero charge with reason `no_attention`. | Invoice. |
| **Redemption** | Append-only record that a basket's QR/promo code was redeemed; credited to every brand in the basket. Identifies the auction, never a person. | A scan without redeem (counted separately). |
| **Redemption code** | Short code + QR minted per cleared auction, resolving to the offer page `/r/{code}`. | Promo code reused across moments. |
| **Audience coverage** | Share of looking viewers matched by at least one brand on screen. Headline KPI. | Reach. |
| **Co-brand lift** | (bundle revenue − single-brand counterfactual) / single-brand counterfactual. | n/a |
| **Glass-to-glass latency** | Audience change at the camera → new creative visible. | Server latency only. |
| **Hot / warm / cold path** | Latency tiers (see `docs/adr/0001`). Hot never waits on warm or cold. | n/a |

## Instants (name the one you mean)

`measured_at` (device saw it) · `received_at` (server got it) · `cleared_at`
(auction) · `ready_at` (creative) · `displayed_at` (device showed it) ·
`recorded_at` (row written).
