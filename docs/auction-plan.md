# Plan: the bidding auction module

> **Doc status: PARTLY IMPLEMENTED (2026-11-21).** S1–S3, the desk and the
> minimum-ad-seconds part of S4, and S5 are in code; see §10 for what landed
> and what is still open. This is the build plan
> for `app/modules/auction` and the parts of `campaigns` and `screens` it
> reads. It refines [`architecture.md` §6](architecture.md#6-the-bundle-auction-precise-enough-to-implement);
> when the two disagree, update `architecture.md` in the same PR that lands
> the change. Vocabulary is in [`../CONTEXT.md`](../CONTEXT.md).

## 1. Goal

One module that takes three inputs and returns one decision:

```
 BIDDERS (with money)          LIVE CONTEXT (what is happening now)
 campaigns: segment bids,      audience moment: people, looking, segments,
 budget left, live multiplier, minor_likely, held products, speech intent
 partner allow-list, category  screen: layouts, stock flags, reserve
             │                 conditions: daypart, weather
             │                              │
             └──────────► AdOpportunity ◄───┘
                               │
                    AuctionEngine (port)          ◄── adapter seam
                    └─ InProcessBundleEngine (VCG bundle + core-adjusted prices)
                               │
                        AuctionDecision
           winners + layout + clearing price per AS, exclusions with
           reasons, single-brand counterfactual, coverage, cleared_at
                               │
                AuctionCleared / AuctionNoFill  →  creative, delivery, console
```

* **Bidders** are campaigns with money: what each will pay per
  attention-second for which segments, how much budget is left, and who it
  will share a basket with.
* **Live context** is the real-time picture the auction prices against: the
  current audience moment plus the screen's state (stock, layouts) and
  conditions (daypart, weather).
* **The engine adapter** is a port (`AuctionEngine`) with one in-process
  implementation now. The application layer depends on the port, never on the
  algorithm, so we can swap pricing rules, run the solo counterfactual through
  the same seam, or later put a remote engine behind a timeout and a local
  fallback.

A note on terms: the request said "auctioneers with money". In our glossary the
**auctioneer** is the engine, and the parties with money are **bidders**
(campaigns of an advertiser). This plan uses `Bidder` for the auction's
read-only view of one campaign (§4.1) and adds that term to `CONTEXT.md`.

## 2. What exists today (commit `e880589`)

| Piece | File | State |
|---|---|---|
| Bundle clearing | `auction/domain/bundle.py` (since replaced, see §10) | Pure, deterministic. Top-8 enumeration, layouts solo/duo, mutual opt-in, competitor-by-category, minor-safety block, VCG-style prices, solo counterfactual value, 3 exclusion reasons. |
| Bid input | [`campaigns/domain/book.py`](../app/modules/campaigns/domain/book.py) | `CampaignBid`: **one** segment, one `bid_per_as`, `co_brand_with`. No budget, schedule, status or multiplier. |
| Moment input | [`audience/.../moment_snapshot.py`](../app/modules/audience/domain/value_objects/moment_snapshot.py) | `AudienceMomentSnapshot` with segments, counts, `minor_likely`. No held products. |
| Wiring | [`bootstrap/demo.py`](../app/bootstrap/demo.py) | Simulator calls `clear_bundle` inline inside the WS handler; seeds 4 brands. No application layer, events, or repository. |
| Console | [`bootstrap/demo_console.py`](../app/bootstrap/demo_console.py) | Projects the result onto `console.auction`. Coverage = share of *segments* covered. |
| Tests | [`tests/unit/auction/test_bundle.py`](../tests/unit/auction/test_bundle.py) | 3 cases: co-brand pricing, competitor exclusion, no-fill. |

### 2.1 Gaps found (these drive the requirements)

1. **A bundle can only win through a bonus nobody pays, and then it earns
   less.** Both results were verified by running the current engine on the
   test fixture:
   - Slot values are `v_c × share`, and shares sum to 1, so
     `V(duo) = 0.5·v_a + 0.5·v_b ≤ max(v_a, v_b) = V(best solo)`. With
     `pair_affinity = 0`, the duo ties the solo at 100 and loses the
     tie-break. **Under this model the basket thesis can never win on its
     own merits.**
   - With `pair_affinity = 20` (a platform-wide bonus that no bidder pays),
     the duo clears at value 120 with prices 30 + 30 = **60**, while a
     solo-only run of the same moment earns **100**. The unpaid bonus inflates
     V(B\*) and so *lowers* each winner's VCG price.
   - Even with a sound value model, VCG for complements can earn less than
     the solo auction. In solo, the two brands compete for one slot. In a
     basket, they stop competing.
   The console's "revenue vs single-brand" KPI and the pitch ("raises
   coverage and revenue per screen") both depend on this. See R-P1 to R-P4
   and open question Q1.
2. **Units are mixed up.** `v_c = bid × viewer_count × confidence` is money per
   *screen*-second (summed over viewers), but it is reported as
   `price_per_as`, and billing multiplies by attention-seconds, which already
   count viewers. Billing would count viewers twice. See R-P5.
3. **One segment per campaign.** The spec sums over segments (`Σ_s`).
4. **Missing eligibility:** status/schedule, budget, stock, live multiplier,
   context multiplier, held-product boost, speech-intent cap, and an approved
   basket offer/loop for the pairing.
5. **Exclusions are incomplete.** There is no `not_partner`, `budget`,
   `out_of_stock` or `no_basket_offer`, and losers that were simply outbid get
   no reason. The console chips (✗ competitor · ✗ minor-safe · ✗ not a
   partner) need all of them.
6. **No application layer.** There is no command, no re-auction triggers
   (stock, bid change, context), no coalescing for slider spam, no events, and
   no auction record.
7. **Coverage** in code is "segments covered ÷ segments present", but the
   glossary defines it as "share of *looking viewers* matched".

## 3. Scope

**In:** the auction domain (valuation, eligibility, compatibility, clearing,
pricing, exclusions, counterfactual), the engine port and adapter, the
per-screen auction desk (application), the auction-facing read model of
campaigns, the live-multiplier command (bidding war), stock and context inputs
to the auction, events, an in-memory auction log, contract additions, and the
console projection.

**Out (other modules or later):** charges and the ledger (`delivery`), creative
composition (`creative`), VLM held-product extraction (`audience`, warm path),
advertiser self-serve, multi-screen budget pacing beyond a simple reservation,
and Postgres persistence of auctions (slice S9, *Should*).

## 4. Requirements

IDs are stable so issues and tests can reference them. **M** = must for the
demo, **S** = should, **C** = could.

### 4.1 Bidders (campaigns with money)

| ID | Pri | Requirement |
|---|---|---|
| R-B1 | M | A `Bidder` is an immutable auction-facing view of one campaign: `campaign_id`, `brand_id`, `organization_id`, `category`, `age_restricted`, `segment_bids: Mapping[SegmentId, Money]` (max price per AS per segment), `partners: frozenset[BrandId]` (partner allow-list), `basket_bonus: Mapping[BrandId, Money]` (optional, opted-in, see R-P2), `live_multiplier: BidMultiplier`, `status`, `schedule: UtcDateRange \| None`, `budget_remaining: Money`. It replaces `CampaignBid`. |
| R-B2 | M | All money is `Money` in the screen's currency (CLP in the demo). A bidder whose currency differs from the opportunity's is excluded with reason `invalid_bid`. It is never converted silently. |
| R-B3 | M | `BidMultiplier` is a value object bounded to [0.2, 5.0] with default 1.0 (the `/advertiser` slider). |
| R-B4 | M | The campaigns context publishes a `CampaignBook` query (port in auction, in-memory adapter in campaigns) that returns a **versioned, immutable snapshot** of all bidders. Reads are O(1) and never touch Postgres (decision < 30 ms). |
| R-B5 | M | `SetLiveMultiplier(campaign_id, multiplier, seq)` is a campaigns command. It is idempotent by `(campaign_id, seq)`, a lower `seq` is ignored, and the same `seq` with a different value fails. It emits `LiveMultiplierChanged`, which triggers a re-auction (R-D2). |
| R-B6 | S | Budget eligibility (`BudgetCovers`): `budget_remaining ≥ max_effective_bid × looking × max_creative_seconds`. This is the worst-case exposure of one play, because price per AS never exceeds the bid (R-P6). |
| R-B7 | S | On clear, the book **reserves** each winner's worst-case exposure and releases it on `ChargeRecorded` (actual amount). This prevents overspend when a re-auction overlaps a play that is still running. A single demo screen can skip it. |
| R-B8 | C | Retailer house bids are ordinary bidders owned by the venue's organization. No special code path. |

### 4.2 Live context (what is happening now)

| ID | Pri | Requirement |
|---|---|---|
| R-C1 | M | `AdOpportunity` is the single input to the engine: `moment: AudienceMomentSnapshot`, `bidders: tuple[Bidder, ...]`, `screen: ScreenFacts` (layouts, `in_stock: frozenset[BrandId]` or a stock map, `reserve: Money`, currency), `conditions: MarketConditions` (daypart, weather), `approved_baskets: frozenset[frozenset[BrandId]]` (pairings with an approved basket offer **and** loop), `book_version`, `context_version`. |
| R-C2 | M | The engine reads **only** `AdOpportunity`. It never reads a clock, a repository or a global. Same opportunity gives the same decision (property test: shuffle bidder order → identical result). |
| R-C3 | M | A per-screen **live context store** in the auction application layer holds the latest moment, screen facts and conditions in process memory. Each input type has its own monotonic version, and a write with an older version is dropped. |
| R-C4 | M | Stock is per screen per brand, set by `stock.set` from `/clerk`. Out of stock gives exclusion `out_of_stock` (spec `InStockAtScreen`). |
| R-C5 | S | `MarketConditions` (daypart × weather) feeds a pure `ContextMultiplier` table per category, set from the console (`context.set`). |
| R-C6 | S | The moment snapshot gains `held_products: tuple[HeldProduct, ...]` (`brand_id`, `category`, `confidence`). This is an audience change, filled by the warm VLM path. A held product boosts its brand's approved partners (`complete the basket`). |
| R-C7 | S | Speech-derived segments add a bounded intent boost: `boost = 1 + α · speech_intent` with `α ≤ 0.5`, and the segment's contribution capped so one segment can't exceed `cap × looking` viewers. One loud person can't outweigh the group. |
| R-C8 | M | `minor_likely` only **blocks** age-restricted bidders (`NoAgeRestrictedCategoryWhenMinorLikely`, a `Policy`). It never raises or lowers any other bid. |

### 4.3 Engine port and adapter

| ID | Pri | Requirement |
|---|---|---|
| R-E1 | M | Port `AuctionEngine` in `auction/application/ports/engine.py`: `decide(opportunity: AdOpportunity) -> AuctionDecision`. It is synchronous and must not do IO. |
| R-E2 | M | Adapter `InProcessBundleEngine` in `auction/infrastructure/engines/in_process.py` wraps the pure domain functions (`valuate` → `eligible` → `enumerate_bundles` → `select` → `price`). It is configured with `PricingRule` (`vcg` \| `core_adjusted`, default `core_adjusted`, see R-P3) and `top_k` (default 8). |
| R-E3 | M | The **single-brand counterfactual** runs through the same engine with layouts restricted to `solo`. It returns both `single_brand_value` and `single_brand_revenue`, so the console's lift is like-for-like (R-O3). |
| R-E4 | C | A remote or alternative engine may implement the port only behind a `FallbackEngine(primary, fallback, timeout_ms=10)` that always falls back to the in-process engine. Hot-path code never awaits the network (ADR 0001). |
| R-E5 | M | The composition root (`app/bootstrap/`) is the only place that picks the engine. |

### 4.4 Valuation, eligibility, compatibility (domain, pure)

**Valuation (money per screen-second):**

```
v_c = Σ_s  segment_bid_c(s) · relevant_viewers(s) · confidence(s)
          · speech_boost(s) · context_mult(category_c, conditions)
          · live_mult_c
      + held_partner_boost_c
```

`relevant_viewers(s)` is the segment's `viewer_count`, capped per R-C7. All
math is `Decimal`. Values stay unrounded until pricing (R-P7).

| ID | Pri | Requirement |
|---|---|---|
| R-V1 | M | `valuate(bidder, opportunity) -> Valuation` is pure and returns the total plus a per-segment breakdown, because the console shows *why* the bid moved. |
| R-V2 | M | A bidder with no segment present, or with `v_c = 0`, is **not shown as excluded**. It simply has no bid for this moment. |
| R-L1 | M | Per-bidder eligibility runs in this order and records the **first** failing reason: `invalid_bid` → `inactive` (status/schedule) → `safety` (minor_likely + age_restricted) → `out_of_stock` → `budget`. Each check is a pure `Specification`/`Policy` that takes no repository. |
| R-L2 | M | Bundle compatibility is a composed `Specification[Bundle]`: `MutualPartners` (every pair is on **both** allow-lists) ∧ `NoSharedCategory` (competitor exclusion) ∧ `DistinctBrands` ∧ `ApprovedBasket` (the pairing has an approved offer and loop; solo always passes) ∧ `LayoutFits`. |
| R-L3 | M | Candidates: the top K eligible bidders by `v_c` (tie-break by `campaign_id`), all subsets of size ≤ the largest layout's slot count, kept only if compatible. Each subset is paired with every layout of the same slot count. With K = 8 and 3 layouts that is at most 92 subsets, well within the 30 ms budget. |

### 4.5 Selection and pricing

| ID | Pri | Requirement |
|---|---|---|
| R-P1 | M | Bundle value `V(B, L) = Σ_{c∈B} v_c · efficiency(L, slot_c) + Σ_{c∈B} Σ_{d∈B, d≠c} basket_bonus_c(d)`. **Slot efficiency** replaces raw screen share in valuation: it is the fraction of a brand's solo value that its slot still delivers to *its own* relevant viewers (a coffee lover still notices a half-screen coffee offer). Efficiencies per layout sum to more than 1 (proposed seed: solo 1.0; duo 0.70/0.60; trio 0.55/0.45/0.40), and they are a tunable table, not code. Screen `share` remains a creative/layout property. A basket then wins exactly when it reaches viewers the solo would miss: `e₂·v_b > (1−e₁)·v_a`. Slots go to bidders in descending `v_c`. |
| R-P2 | M | **Remove the platform-wide `pair_affinity`.** The affinity bonus becomes a bidder-owned, opted-in `basket_bonus` (money a brand adds when a named partner shares the screen). It is real money attributed to the bidder that offers it, so it never subsidizes the basket from nowhere. It is **not** a revenue lever: VCG credits the extra welfare to the partner whose presence creates it, so the partner's price drops (verified in `test_basket_bonus_is_attributed_to_the_bidder_that_offers_it`). |
| R-P3 | M | Winner: `B* = argmax V` with a deterministic tie-break (higher V, then fewer slots, then lexicographic campaign ids). Below the reserve, the decision is **no-fill** (house ad). **Prices:** start from VCG, `P_c = V*(without c) − (V(B*) − value_c(B*))`. With `core_adjusted` (default), raise payments to the minimal core point: for every candidate `B'`, require `Σ_{c∈B*∖B'} P_c ≥ V(B') − Σ_{c∈B*∩B'} value_c(B*)`. Spread each violated deficit equally over `B*∖B'` (in descending-deficit order), capped at each winner's own value. This guarantees that no losing coalition (including any solo competitor outside the basket) would have paid more than the basket. It does **not** guarantee bundle revenue ≥ the single-brand counterfactual: when two non-competing brands would have fought over a solo slot, the basket removes that competition. The console therefore shows both numbers honestly (R-O3), and the seeded demo scenes are checked by a test to show the lift we pitch. |
| R-P4 | M | Each winner's price is floored at `reserve · share` (the reserve per slot), never above its own value (individual rationality). |
| R-P5 | M | **Units.** `P_c` is money per screen-second. Clearing price per AS: `price_per_as_c = P_c / looking_at_clear`. Delivery charges `price_per_as_c × measured_AS`. Expected charge equals `P_c` per second of play, and zero attention gives a zero charge. `looking_at_clear = 0` means no auction (no-fill, reason `no_audience`). |
| R-P6 | M | Invariant (test): `price_per_as_c ≤ max_effective_bid_c` for every winner. |
| R-P7 | M | Bids, values and clearing prices are **rates**, not charges. They use a `RatePerAttentionSecond` value object (Decimal, 4 dp, currency), because CLP `Money` has 0 decimals and rejects 4.3. Only the ledger charge (`rate × measured_AS`) becomes `Money`, rounded once with `ROUND_HALF_EVEN` (D4). |

### 4.6 Outputs, exclusions, events

| ID | Pri | Requirement |
|---|---|---|
| R-O1 | M | `AuctionDecision` (frozen): `auction_id`, `screen_id`, `moment_id`, `trigger`, `layout \| None`, `winners: (campaign_id, brand_id, slot, share, value, price_per_as)`, `bids: (campaign_id, value, breakdown)`, `exclusions`, `bundle_value`, `bundle_revenue`, `single_brand_value`, `single_brand_revenue`, `coverage`, `book_version`, `context_version`, plus trace instants `moment_changed_at` and `cleared_at`. |
| R-O2 | M | Every bidder with a positive value that did not win gets exactly one reason: eligibility reasons (R-L1), or `competitor` (shares a category with a winner), `not_partner` (it would have fit by value but is not on a winner's allow-list, or vice versa), `no_basket_offer`, `outbid`, `below_reserve` (no-fill). Each has a short `detail` for the chip ("Rayo: same category as Voltio"). |
| R-O3 | M | Lift is computed from like-for-like numbers: `co_brand_lift = (bundle_revenue − single_brand_revenue) / single_brand_revenue`. The console also shows the value (welfare) lift. |
| R-O4 | M | Coverage follows the glossary: the share of looking viewers matched by at least one winner. Segment counts overlap, so until audience attributes segments per tracklet, report the bounds `max_s viewer_count(s) / looking ≤ coverage ≤ min(1, Σ_s viewer_count(s) / looking)` over covered segments, and show the upper bound labelled "est.". (Open question Q3.) |
| R-O5 | M | Events: `AuctionCleared(decision)` and `AuctionNoFill(decision, reason)`, both `DomainEvent`s. Creative subscribes to both. Delivery reads cleared prices by `auction_id`. |
| R-O6 | M | Every decision is appended to an in-memory `AuctionLog` (ring buffer per screen, last 200) behind an `AuctionRepository` port. Postgres append-only `auctions` table is S9 (register it in `APPEND_ONLY_TABLES`). Persisting never blocks the push to creative (write-behind via the warm worker). |

### 4.7 The auction desk (application, per screen)

| ID | Pri | Requirement |
|---|---|---|
| R-D1 | M | `RunAuction(screen_id, trigger)` command handler: read the live context (R-C3) and the book snapshot (R-B4) → build the `AdOpportunity` → `engine.decide` → append to the log → publish the event. No IO on this path except the in-memory log. |
| R-D2 | M | Triggers: `moment_changed`, `stock_changed`, `bid_changed`, `conditions_changed`, `held_products_changed`. Each re-auctions the **current moment**. Passers-by are already debounced upstream by audience. |
| R-D3 | M | **Coalescing:** at most one auction in flight and one pending per screen, latest-wins (reuse `realtime/mailbox.py`). Slider spam from two phones gives at most about 1 auction per 100 ms per screen, with the latest inputs. |
| R-D4 | M | **Staleness:** each decision carries `(moment_id, book_version, context_version)`. Creative applies it only if it is still the newest for that screen. |
| R-D5 | M | **No flicker:** if the new decision has the same layout and winner set as the current one, emit `AuctionCleared` with `bundle_unchanged=true`. Creative keeps the playing creative and the console updates prices. Billing for the playing creative uses the auction it was composed from. |
| R-D7 | M | **Minimum ad seconds** (D5): a pure `MinAdTimePolicy(trigger, current_displayed_at, now) -> swap_not_before` stamps each decision. The desk holds at most one pending decision per screen (latest wins), releases it at `swap_not_before` with a `loop.call_later` timer in the realtime layer, and re-checks staleness (R-D4) on release. Auctions keep clearing during the hold, so the console stays live. |
| R-D6 | M | A failing engine call (exception) produces `AuctionNoFill(reason=engine_error)`, logged with the trigger. The house ad plays and the console shows a degradation flag. The screen is never blank. |

### 4.8 Latency and determinism

| ID | Pri | Requirement |
|---|---|---|
| R-T1 | M | `engine.decide` p95 < 5 ms for 12 bidders × 3 layouts on the demo laptop. `RunAuction` end to end < 30 ms. Measured by `scripts/bench_auction.py`, not a CI timing assertion (flaky). |
| R-T2 | M | `cleared_at` is stamped by the handler from the injected `Clock` and copied into `LatencyTrace`. |
| R-T3 | M | The domain uses no `datetime.now()` or `uuid4()`. `auction_id` comes from `IdGenerator` in the handler. |

### 4.9 Contract changes (`app/contract/messages.py`, then `make contract`)

All are additive (optional fields or new enum values):

- `ExclusionReason` += `not_partner`, `out_of_stock`, `inactive`,
  `no_basket_offer`, `outbid`, `invalid_bid`.
- `BidLine` += `value_per_s: MoneyDTO` (real money) and `multiplier: str`. The
  existing float `value` stays a relevance score.
- `ConsoleAuction` += `bundle_revenue`, `single_brand_revenue`, `trigger`,
  `bundle_unchanged`.
- New inbound messages: `advertiser.bid_multiplier` (`campaign_id`,
  `multiplier`, `seq`), `stock.set` (`screen_id`, `brand_id`, `in_stock`),
  `context.set` (`daypart`, `weather`), as already listed in
  [`architecture.md` §10](architecture.md#10-message-contract).

## 5. Module layout

```
app/modules/auction/
  CONTEXT.md
  domain/
    opportunity.py        # AdOpportunity, ScreenFacts, MarketConditions, Layout
    bidder.py             # Bidder, BidMultiplier, BidderStatus
    valuation.py          # valuate(), Valuation breakdown, speech/context/held boosts
    eligibility.py        # Specifications/Policies per R-L1 (pure)
    compatibility.py      # MutualPartners, NoSharedCategory, ApprovedBasket, ...
    clearing.py           # enumerate_bundles(), select(), price_vcg(), price_core_adjusted()
    decision.py           # AuctionDecision, Winner, Exclusion, ExclusionReason, Trigger
    events.py             # AuctionCleared, AuctionNoFill
  application/
    ports/engine.py       # AuctionEngine (R-E1)
    ports/campaign_book.py# CampaignBook query (R-B4)
    ports/auction_log.py  # AuctionRepository (R-O6)
    live_context.py       # per-screen store, versions (R-C3)
    commands/run_auction/ # command.py, handler.py (R-D1..D6)
    providers.py
  infrastructure/
    engines/in_process.py # InProcessBundleEngine (R-E2), FallbackEngine (R-E4, later)
    in_memory_log.py
app/modules/campaigns/
  domain/...              # Campaign aggregate (version, live multiplier transition)
  application/commands/set_live_multiplier/
  infrastructure/in_memory_book.py   # implements auction's CampaignBook
```

`bundle.py` is split into the files above. `clear_bundle` stays as a thin
compatibility function until `bootstrap/demo.py` is moved onto the desk (S4),
then it is deleted.

## 6. Implementation slices (vertical, in order)

Each slice keeps `make check` green and the simulator scenes in
[`simulator.md`](simulator.md) working.

| # | Slice | Delivers | Req |
|---|---|---|---|
| S1 | **Units + pricing fix** | Slot-efficiency table; remove `pair_affinity`; add `basket_bonus`; core-adjusted prices; `price_per_as = P_c / looking`; revenue + counterfactual revenue in the result. Update `test_bundle.py` numbers. | P1–P7, E3 |
| S2 | **Bidder with money** | `Bidder` (multi-segment bids, multiplier, budget, status), eligibility specs with ordered reasons, full exclusion reasons. | B1–B3, B6, L1–L3, V1–V2, O2 |
| S3 | **Engine port + adapter** | `AuctionEngine`, `InProcessBundleEngine`, `AuctionDecision`, wiring in bootstrap. | E1–E3, E5, O1 |
| S4 | **Auction desk** | Live context store, `RunAuction`, coalescing, staleness, no-flicker, events, in-memory log. `demo.py` calls the desk instead of `clear_bundle`. | C1–C3, D1–D6, O5–O6, T2–T3 |
| S5 | **Contract + console** | §4.9 additions, `make contract`, `demo_console.py` shows reasons/details, revenue lift and coverage. | O3–O4 |
| S6 | **Bidding war** | `SetLiveMultiplier` + `advertiser.bid_multiplier` socket/route → re-auction. Demo beat 4. | B4–B5, D2–D3 |
| S7 | **Stock + context** | `stock.set` (`/clerk`), `context.set`, `ContextMultiplier` table. Backup beats. | C4–C5 |
| S8 | **Held products + speech** | `held_products` on the moment, partner boost, speech-intent cap. Demo beat 3. Depends on audience's warm path. | C6–C7 |
| S9 | **Persistence + reservation** | Postgres `auctions` (append-only, write-behind), budget reservation. | O6, B7 |

S1–S4 is the minimum for "the live loop works end to end" with correct money.
S5–S6 makes the console and the bidding-war beat land.

## 7. Test plan (table-driven, `tests/unit/auction/`)

These are the demo's credibility, so they never go red.

| Case | Expectation |
|---|---|
| Co-brand duo, distinct segments, no bonus | duo wins on slot efficiency alone; value lift > 0 |
| Co-brand demo scenes (seeded book) | bundle revenue ≥ single-brand revenue (the pitched lift is real on stage) |
| Same segment, two non-competing brands | solo wins (no new viewers reached) |
| Competitor pair, both mutual partners | never share a basket; loser gets `competitor` with detail |
| Partner on one side only | no duo; loser gets `not_partner` |
| Pairing without approved basket | no duo; `no_basket_offer` |
| `minor_likely` + age-restricted top bid | excluded `safety`; other bids unchanged (R-C8) |
| Stock-out of a winner | re-auction excludes it `out_of_stock`; basket re-forms |
| Budget below worst-case exposure | `budget` |
| All bundles below reserve | no-fill; candidates get `below_reserve` |
| `looking = 0` | no-fill `no_audience`, no prices |
| Multiplier 0.2 → 5.0 on a challenger | winner flips at the predicted threshold (bidding war) |
| Price invariants (property test, random books) | `reserve·share ≤ price_per_as·looking ≤ value_c`; `price_per_as ≤ bid`; Σ prices ≥ best losing coalition (core) |
| Determinism (property test) | shuffled bidder order and layout order → identical decision |
| Same winners after re-auction | `bundle_unchanged = true` |
| Stale decision | creative ignores a decision older than the current versions |
| Currency mismatch | `invalid_bid`, no crash |
| Min ad seconds: bid flip 1 s after display | decision held until 3.0 s, then swapped; console shows countdown |
| Min ad seconds: new group 0.5 s after display | held until the 1.5 s floor, then swapped |
| Min ad seconds: `minor_likely` while alcohol plays | swapped immediately (0 s), never held |
| Min ad seconds: winner goes out of stock | swapped immediately |
| Pending decision superseded during hold | only the latest is released; stale one dropped |
| Look shorter than 1.0 s | 0 billable AS, explicit zero charge |
| Golden demo scenes (one per beat) | exact winners, rates, lift and exclusions match `tests/fixtures/demo_book.json` |

Handler tests use a fake `Clock`, a deterministic `IdGenerator`, the
in-memory book and log. No Postgres.

## 8. Open questions (decide before S1)

1. **Slot value model and the revenue claim.** This is the most important
   decision. Under today's share-weighted values a basket can't beat a solo
   without an unpaid bonus (§2.1).
   - (a) Adopt **slot efficiency** (R-P1) so baskets win on coverage. This is
     needed whatever we pick for pricing.
   - (b) Pricing: plain VCG is truthful, which matches the current judge answer
     ("bidding your true value is optimal"), but it can earn less than solo.
     Core-adjusted VCG makes sure no losing basket or solo brand would have
     paid more, and it is only approximately truthful.
   - (c) Pitch: lead with **coverage and value lift**, which the auction
     guarantees, and show revenue lift only where it is real. Seed the demo
     with in-category competition (Voltio vs Rayo, Brasa vs Toro) so
     competitors set the prices. That is where basket revenue rises.

   **Recommendation:** (a) + `core_adjusted` by default with `vcg`
   selectable through the engine adapter + (c). Update the judge answer to
   *"each brand pays what it took to beat the best alternative basket"*.
2. **Shares and billing.** This plan bills `price_per_as × measured_AS` with
   the slot share already inside the price. The alternative is to credit each
   brand `share × AS` and price without the share. Both give the same totals;
   the plan's version keeps `delivery` simpler. Confirm.
3. **Coverage.** Is an estimated upper bound acceptable for the demo, or should
   audience attribute segments per tracklet (more work in `audience`) so
   coverage is exact?
4. **Bidding-war stickiness.** Should a challenger need to beat the incumbent
   by a small margin (hysteresis, e.g. 2 %) to avoid winner flapping when the
   two sliders are close? It makes the screen calmer but the flip less
   immediate.

## 9. Demo-grade decisions (proposed)

These close §8 and fix the numbers that make the five beats in
[`demo.md`](demo.md) reproducible. Each one is a default in settings or the
seed book, so it can be tuned during rehearsal without code changes. Mark one
**Accepted** when it is agreed, and record the stack-level ones (D4, D5) in
`architecture.md`.

### Money and pricing

| # | Decision | Value | Why it matters on stage |
|---|---|---|---|
| D1 | **Slot efficiency, not screen share, drives value** (Q1a) | solo 1.00 · duo 0.70/0.60 · trio 0.55/0.45/0.40 | Without it a basket can never beat a solo (§2.1), and beat 2 doesn't happen. |
| D2 | **Pricing rule** (Q1b) | `core_adjusted` by default; `vcg` as a console toggle | No losing brand would have paid more. Lets us answer "why not plain VCG?" live. |
| D3 | **Units** (Q2) | value and `P_c` per screen-second; `rate_c = P_c / looking`; charge = `rate_c × measured_AS` | One formula on the receipt; zero attention is visibly $0. |
| D4 | **Rates are not Money** | `RatePerAttentionSecond` (4 dp); `Money` only for charges, rounded half-even to whole CLP at the ledger | CLP `Money` rejects 4.3 (verified). Whole-peso rates would distort every price. |
| D4b | **Seed rates in CLP** | bids 3–12 CLP/AS; reserve 1.5 CLP/AS; solo-slot reserve × share | Receipts read like *"2 looked · 7.4 AS · $41"*. Realistic for a counter screen. |
| D6 | **Pitch numbers** (Q1c) | headline = coverage + value lift; revenue lift shown only when positive; seed in-category rivals (Voltio/Rayo, Brasa/Toro, Cola Sur/Pacífica) | The console never contradicts the pitch, and rivals set real prices so revenue lift is real in the seeded scenes. |

### Minimum ad seconds (screen time)

One question decides whether the demo looks alive or twitchy: when may a
re-auction replace what is on screen? The auction always clears; the hold only
delays the **swap**.

| # | Trigger | Earliest swap after the current creative's `displayed_at` | Rationale |
|---|---|---|---|
| D5.1 | **Safety** (`minor_likely` and an age-restricted winner on screen) | **0 s**, immediate | Invariant 2. Never held. |
| D5.2 | **Stock-out** of a winner | **0 s**, immediate | Never advertise what the store can't sell. |
| D5.3 | **New group** (`moment_changed` with a different winner set) | **1.5 s floor** | Keeps the "~1 s" reaction of beat 2 while no ad flashes for less than a crossfade plus a read. |
| D5.4 | **Bid / conditions / held product** on the same moment | **3.0 s** (one full teaser stage) | The bidding war flips cleanly instead of strobing. The console shows *"Rayo wins · swapping in 1.8 s"*, which adds suspense instead of hiding it. |
| D5.5 | **Audience leaves** (`looking = 0`) | keep the creative **3.0 s**, then house ad | People glance away; don't punish a blink. Zero attention during the grace is billed $0. |
| D5.6 | **Max hold** | a pending decision older than **8 s** is dropped and re-run on fresh inputs | A held decision never plays against an audience that has changed. |

Supporting decisions:

| # | Decision | Value |
|---|---|---|
| D7 | **Minimum billable look.** A tracklet's attention counts only after **1.0 s** of continuous looking, then counts from the start of the look. Shorter glances are 0 AS. | 1.0 s |
| D8 | **Hysteresis** (Q4) on same-moment triggers: a challenger must beat the incumbent bundle's value by **2 %**. A new group has no hysteresis. | 2 % |
| D9 | **Trace:** add `swap_not_before` (and the trigger) to `LatencyTrace`. The console reports glass-to-glass **excluding the intentional hold**, and shows the hold separately, so p50/p95 stay honest. | new field |
| D10 | **Prices for the playing creative** are fixed by the auction it was composed from. Re-clears during the hold or with `bundle_unchanged` don't reprice an ad already on screen. | rule |

### Determinism and rehearsal

| # | Decision | Value |
|---|---|---|
| D11 | **Golden demo book**: one versioned `tests/fixtures/demo_book.json` (≈ 10 brands from `demo.md`, partners, rivals, efficiencies, offers per group-size band) used by the seed, the simulator **and** the tests. | one file |
| D12 | **Golden scene tests**: one per beat and backup moment (basket, complete-the-basket, bidding war flip threshold, stock-out, minor-safe, empty → $0), asserting exact winners, rates, exclusions and lift. A red test means the demo changed. | CI |
| D13 | **Break-even multiplier on the console** (S): for the top loser, show *"Rayo needs ×1.42 to win"*, computed by bisecting the engine off the hot path (≤ 10 runs × < 5 ms). It makes beat 4 legible to the room. | S6 |
| D14 | **Coverage** (Q3): show the upper-bound estimate labelled "est." for the demo; exact per-tracklet attribution is backlog. | est. |
| D15 | **Fail-safe order**: engine error → last good creative → house ad, with a `console.degradation` flag. The auction desk never raises into the WS handler. | rule |

**Upstream assumption (audience, not this module):** a moment is published
only after the new audience is stable for ~600 ms, and tracklets under 1 s of
dwell are ignored. With capture (~33 ms) + GPU YOLO (~80 ms) + 600 ms debounce
+ auction (< 30 ms) + compose (< 50 ms) + preload/decode + a 250 ms
crossfade, glass-to-glass is ≈ 1.1–1.3 s. That fits p50 < 1.5 s, and the
D5.3 floor of 1.5 s on screen sits on top of it without slowing the first
reaction.

## 10. Implementation status

| Area | Status | Where |
|---|---|---|
| §2.1 gaps 1–5, 7 (value model, units, multi-segment bids, eligibility, exclusions, coverage) | **Done** | [`auction/domain/`](../app/modules/auction/domain/) `clearing.py`, `valuation.py`, `rules.py`, `opportunity.py`, `result.py` |
| D1 slot efficiency | Done, with a correction: a member whose segments overlap another slot's falls back to its screen share, so two brands on the same viewers never beat the solo | `clearing._candidates` |
| D2 pricing | Done: `core_adjusted` default, `vcg` selectable on the engine | `clearing._core_adjust`, `InProcessBundleEngine(pricing=…)` |
| D3/D4 units and rates | Done: `MoneyRate` (4 dp) in the kernel; `Money` only via `MoneyRate.total_for` | [`money_rate.py`](../app/modules/common/domain/value_objects/money_rate.py) |
| D4b/D6 seed | Done: CLP 8 / 5 / 12 / 6.5 per AS, reserve 1.5, Rayo+Café basket approved so Rayo prices the energy slot | [`bootstrap/demo.py`](../app/bootstrap/demo.py) |
| R-E1–E3, E5 engine port + adapter + counterfactual | Done | `application/ports/engine.py`, `infrastructure/engines/in_process.py` |
| D5 minimum ad seconds, D8 hysteresis, R-D4–D7 | Done: `MinAdTimePolicy` (pure) + `AuctionDesk` (latest-wins hold, release token); the simulator releases holds with a timer. Holds are off when `app_env=test` (`auction_swap_holds` setting) | `domain/swap.py`, `application/desk.py` |
| D9 trace | Done: `LatencyTrace.swap_not_before`; the console excludes the hold from glass-to-glass and server time | `contract/messages.py`, `web/.../metrics.ts` |
| §4.9 contract, console | Done: new exclusion reasons with `detail`, revenue fields, `pricing`/`trigger`/`swap` on `console.auction` | `demo_console.py`, `ConsolePage.tsx` |
| Held-product boost, speech-intent boost, context multipliers | Done in valuation; inputs not wired yet (audience `held_products`, `context.set`) | `valuation.py`, `AdOpportunity` |
| R-B5 `SetLiveMultiplier` + `/advertiser` socket (S6) | **Open**: `Bidder.with_multiplier` and the `BID_CHANGED` trigger exist | — |
| R-C4 `stock.set` from `/clerk` (S7) | **Open**: `AdOpportunity.out_of_stock` + `STOCK_CHANGED` exist | — |
| R-B7 budget reservation, R-O6 auction log/Postgres (S9) | **Open** | — |
| D7 minimum billable look | **Open**: belongs to `delivery`, which isn't built | — |
| D13 break-even multiplier on the console | **Open** | — |

Verified facts from the seeded co-brand moment: basket value 8.6 vs solo 8
(+7.5 %), revenue 6.95 vs 6.5 (+6.9 %), clearing prices Voltio 2.275 and
Café Patio 1.2 CLP/AS. Without the Rayo+Café basket, plain VCG earns 5.9 and
core-adjusted earns 6.5. Without in-category rivals, a trio still earns
less than the solo auction; the console shows revenue lift only when it is
positive.

### 10.1 Audit (2026-11-21)

An independent review ran about 8,000 randomized auctions. It confirmed core
termination, price bounds, determinism and one-reason-per-loser, and found four
issues. All four are fixed, each with a regression test:

1. **Invariant 2:** a beer ad was held for up to 3 s when a minor appeared and
   the beer segment was gone, or nobody was looking. The swap policy inferred
   safety from exclusions, and those are only recorded for bidders that still
   match a segment. It now reads `minor_likely`, stock and the book directly
   for the on-air brands (`MinAdTimePolicy._unsafe_on_air`).
2. **Bid ceiling:** speech, context and held-product boosts could charge above
   a brand's stated bid. **Rule:** a segment bid is a contractual maximum per
   attention-second. Boosts raise *value* (ranking) only. The ceiling is the
   highest matched bid × the advertiser's own live multiplier, plus its basket
   bonuses for partners on screen (`valuation.price_ceiling`). Core pricing
   respects it, and a slot that can't clear its reserve under the ceiling is
   not a candidate.
3. **Honest reasons:** `not_partner`/`no_basket_offer` are given only when a
   bigger layout could have hosted the loser. A compatible partner whose slot
   would miss its floor is `below_reserve`, not `outbid`.
4. **Engine failure:** the moment is no longer recorded when the engine fails,
   so the next identical snapshot is re-auctioned instead of being deduped.

End-to-end coverage: `tests/unit/api/test_auction_loop_e2e.py` drives the real
ASGI app with holds **on**, using a fixed clock and a manual scheduler. It
covers the house ad → first paid ad, the 1.5 s new-group floor, an immediate
safety swap, a superseded hold never reaching the screen, the 3 s walk-away
grace, billing-unit consistency on the console, the full demo script in order,
and engine-failure retry. It is also verified on a live uvicorn server with
`scripts.replay_audience`.
