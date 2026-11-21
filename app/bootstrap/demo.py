"""Compose one screen's live snapshot-to-creative flow and the shared seed market."""

from base64 import b64encode
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from threading import RLock
from uuid import UUID

from app.bootstrap.demo_console import decision_messages
from app.contract.messages import (
    AdvertiserBidMultiplier,
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    BookLine,
    ChargeLine,
    ConsoleBook,
    ConsoleDegradation,
    ConsoleLatency,
    ConsoleReceipt,
    ConsoleRedemption,
    CopySourceKind,
    CreativeReady,
    CreativeSpec,
    CreativeUpgraded,
    DegradationFlag,
    LatencyTrace,
    MoneyDTO,
    PlaybackReport,
    PlayerDisplayed,
    ScreenAttention,
    StockSet,
    UpgradeKind,
)
from app.modules.auction.application.desk import AuctionDesk
from app.modules.auction.application.ports.auction_log import AuctionLog, AuctionRecord
from app.modules.auction.domain.opportunity import (
    AdOpportunity,
    HeldProduct as BidHeldProduct,
    Layout,
)
from app.modules.auction.domain.result import AuctionResult, NoFillReason
from app.modules.auction.domain.swap import Trigger
from app.modules.auction.infrastructure.auction_log import InMemoryAuctionLog
from app.modules.auction.infrastructure.engines.in_process import InProcessBundleEngine
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.application.classifier import AudienceClassifier, DegradedSignal
from app.modules.audience.application.ports import AudienceTagger
from app.modules.audience.application.ports.topics import TopicExtractor
from app.modules.audience.domain.catalog import CueRule
from app.modules.audience.domain.speech_evidence import (
    SpeechEvidence,
    SpeechTopic,
    merge_speech,
    topics_from_keyterms,
)
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.campaigns.application.live_book import (
    LiveCampaignBook,
    MultiplierOutcome,
    UnknownCampaign,
)
from app.modules.campaigns.application.stock import StockBoard
from app.modules.campaigns.domain.book import (
    Bidder,
    BidMultiplier,
    BrandId,
    BrandKit,
    CampaignId,
    SegmentBid,
)
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.rounding_policy import RoundingPolicy
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.creative.application.briefing import CreativeCatalog, build_brief
from app.modules.creative.application.loops import BundleLoopLibrary
from app.modules.creative.application.ports.copywriter import CopyWriter
from app.modules.creative.application.rendering import render_copy_upgrade, render_spec
from app.modules.creative.application.speech_text import offer_line, spoken_line_for
from app.modules.creative.application.studio import CreativeStudio, StudioConfig
from app.modules.creative.application.template import compose_template
from app.modules.creative.application.voiceover import InvalidSpokenLine, VoiceoverService
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.cost import GenerationBudget, GenerationKind, UsdCost
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.offer import BasketOffer, OfferBook
from app.modules.delivery.application.billing import DeliveryDesk, PlayRecord, Receipt
from app.modules.delivery.application.ports.ledger import ChargeLedger
from app.modules.delivery.application.redemptions import RedemptionDesk, RedemptionLedger
from app.modules.delivery.domain.charge import BilledSlot
from app.modules.delivery.domain.redemption import (
    BasketOffer as RedeemableOffer,
)
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger
from app.modules.delivery.infrastructure.qr import qr_data_url
from app.modules.delivery.infrastructure.redemptions import InMemoryRedemptionLedger
from app.modules.screens.domain.value_objects.ids import ScreenId
from app.realtime.hub import ConnectionHub, ConsoleMessage, Sink
from app.realtime.router import InboundRouter, Received
from app.realtime.scheduler import Scheduler, thread_timer
from app.realtime.warm_worker import WarmWorker

DEMO_SCREEN = "demo-01"
MAX_MEASUREMENT_SKEW = timedelta(seconds=10)
# Worst-case play length held against a winner's budget while it is on air (R-B6/R-B7).
MAX_PLAY_SECONDS = 15


class UnknownBrand(Exception):
    """A stock toggle named a brand that isn't in the demo book."""


@dataclass(frozen=True, slots=True)
class DemoControls:
    """What `/advertiser` and `/clerk` may do (thin HTTP routes call these)."""

    book_view: Callable[[], ConsoleBook]
    set_multiplier: Callable[[AdvertiserBidMultiplier], MultiplierOutcome]
    set_stock: Callable[[StockSet], bool]
    offer_for: Callable[[str], RedeemableOffer | None]
    redeem: Callable[[str, str], tuple[ConsoleRedemption, bool]]
    reauction_bids: Callable[[], None]
    book_for: Callable[[str], ConsoleBook] | None = None


def _clp_per_as(amount: str) -> MoneyRate:
    return MoneyRate.create(currency="CLP", amount=amount)


@dataclass(slots=True)
class LiveMarket:
    """Campaign identities, budgets and receipts shared by all screens in this market."""

    kits: tuple[BrandKit, ...]
    offers: OfferBook
    bidders: tuple[Bidder, ...]
    book: LiveCampaignBook
    stock: StockBoard
    auctions: AuctionLog
    delivery: DeliveryDesk
    redemptions: RedemptionDesk
    lock: RLock


def seed_market(
    *,
    ids: IdGenerator,
    budget_clp: int,
    auction_log: AuctionLog | None = None,
    ledger: ChargeLedger | None = None,
    redemption_ledger: RedemptionLedger | None = None,
) -> LiveMarket:
    # Reuse the existing project mark for these fictional fixture brands.
    logo = Path(__file__).resolve().parents[2].joinpath("project-logo.png").read_bytes()
    logo_url = "data:image/png;base64," + b64encode(logo).decode("ascii")

    # Real IAB Audience Taxonomy 1.1 IDs (source in the audience catalog JSON).
    fitness = SegmentId.create("408")  # Interest | Healthy Living | Fitness and Exercise
    coffee = SegmentId.create("977")  # Purchase Intent | CPG | Edible | Beverages | Coffee
    beer = SegmentId.create("369")  # Interest | Food & Drink | Alcoholic Beverages
    food = SegmentId.create("1404")  # Purchase Intent | Food and Beverage Services | Fast Food
    kits = tuple(
        BrandKit(
            brand_id=BrandId.create(ids.new_uuid7()),
            name=name,
            logo_url=logo_url,
            stock_copy=copy,
            accent_color=color,
        )
        for name, copy, color in (
            ("Voltio", "Voltio · Energía para tu próximo entrenamiento.", "#c6f26b"),
            ("Café Patio", "Café Patio · Tu pausa después de entrenar.", "#f5bb84"),
            ("Malta Sur", "Malta Sur · Cerveza artesanal.", "#f6d15a"),
            ("Rayo", "Rayo · Energía para tu día.", "#b9adff"),
        )
    )
    voltio, patio, rayo = kits[0].brand_id, kits[1].brand_id, kits[3].brand_id
    # Every mutual-partner pair gets an approved basket (docs/demo.md), so Rayo competes
    # for the energy slot inside a basket and prices it. Offers are sized to the group
    # looking (architecture §6.1); the price is the brands', never the copywriter's.
    offer_book = OfferBook.of(
        [
            *_tiers(
                frozenset({voltio, patio}),
                "Voltio + Café Patio",
                (
                    (1, "Combo post-entreno", "", 3490),
                    (2, "Dúo post-entreno", " x2", 5990),
                    (3, "Trío post-entreno", " x3", 8490),
                    (4, "Pack x4 post-entreno", " x4", 10990),
                ),
            ),
            *_tiers(
                frozenset({rayo, patio}),
                "Rayo + Café Patio",
                ((1, "Combo", "", 3290), (2, "Dúo", " x2", 5790), (3, "Trío", " x3", 7990)),
            ),
        ]
    )
    co_brand_partners: tuple[frozenset[BrandId], ...] = (
        frozenset({kits[1].brand_id}),
        frozenset({kits[0].brand_id, kits[3].brand_id}),
        frozenset(),
        frozenset({kits[1].brand_id}),
    )
    # Rates are CLP per attention-second (docs/auction-plan.md D4b). Rayo is
    # Voltio's in-category rival, so the energy slot has a real price (D6).
    bidders = tuple(
        Bidder(
            campaign_id=CampaignId.create(ids.new_uuid7()),
            brand_id=kit.brand_id,
            category=category,
            age_restricted=age_restricted,
            segment_bids=tuple(
                SegmentBid(segment_id=segment, rate=_clp_per_as(rate)) for segment in segments
            ),
            partners=partners,
            budget_remaining=Money.create(currency="CLP", amount=budget_clp),
        )
        for kit, (category, age_restricted, segments, rate), partners in zip(
            kits,
            (
                ("sports_goods", False, (fitness,), "8"),
                # Café Patio also sells something to eat: "qué hambre" reaches it.
                ("coffee", False, (coffee, food), "5"),
                ("alcohol", True, (beer,), "12"),
                ("sports_goods", False, (fitness,), "6.5"),
            ),
            co_brand_partners,
            strict=True,
        )
    )
    book = LiveCampaignBook(bidders)
    return LiveMarket(
        kits=kits,
        offers=offer_book,
        bidders=bidders,
        book=book,
        stock=StockBoard(),
        auctions=auction_log or InMemoryAuctionLog(),
        delivery=DeliveryDesk(ledger=ledger or InMemoryChargeLedger(), ids=ids, budgets=book),
        redemptions=RedemptionDesk(ids=ids, ledger=redemption_ledger or InMemoryRedemptionLedger()),
        lock=RLock(),
    )


def register_demo(
    *,
    router: InboundRouter,
    wire_screen_id: str = DEMO_SCREEN,
    market: LiveMarket | None = None,
    hub: ConnectionHub,
    clock: BaseClock,
    ids: IdGenerator,
    warm: WarmWorker | None = None,
    voiceovers: VoiceoverService | None = None,
    narrator_voice_id: str | None = None,
    speech_screens: frozenset[str] = frozenset(),
    speech_ttl: timedelta = timedelta(seconds=60),
    swap_holds: bool = False,
    scheduler: Scheduler = thread_timer,
    tagger: AudienceTagger | None = None,
    auction_log: AuctionLog | None = None,
    ledger: ChargeLedger | None = None,
    copywriter: CopyWriter | None = None,
    copy_timeout_s: float = 2.5,
    hourly_budget_usd: Decimal = Decimal(2),
    budget_clp: int = 50_000,
    startup: list[Callable[[], Awaitable[None]]] | None = None,
    topic_extractor: TopicExtractor | None = None,
    redemption_ledger: RedemptionLedger | None = None,
    public_base_url: str = "http://localhost:5173",
    media_dir: Path | None = None,
) -> DemoControls:
    screen_id = ScreenId.create(ids.new_uuid7())
    market = market or seed_market(
        ids=ids,
        budget_clp=budget_clp,
        auction_log=auction_log,
        ledger=ledger,
        redemption_ledger=redemption_ledger,
    )
    kits, bidders, offer_book = market.kits, market.bidders, market.offers
    kits_by_id = {kit.brand_id: kit for kit in kits}
    logo_url = kits[0].logo_url
    slug_of = {kit.brand_id: _slug(kit.name) for kit in kits}
    brand_by_slug = {slug: brand for brand, slug in slug_of.items()}
    fitness, coffee, beer, food = (SegmentId.create(key) for key in ("408", "977", "369", "1404"))
    layouts = (Layout.solo(), Layout.duo())
    reserve = _clp_per_as("1.5")
    desk = AuctionDesk(InProcessBundleEngine(), holds_enabled=swap_holds)
    book, stock, auctions = market.book, market.stock, market.auctions
    delivery, redemptions = market.delivery, market.redemptions
    loops = BundleLoopLibrary(media_dir) if media_dir is not None else None
    campaign_of = {bidder.campaign_id: bidder for bidder in bidders}
    # Demo cues at full weight replace any real catalog cue with the same label, so
    # scripted scenes and the physical props stay deterministic; every other real
    # catalog cue still applies. Held props ("product:<slug>", ADR 0008 D2) are not
    # cues: the classifier reads them as held products that boost their partners.
    loaded = load_catalog()
    fixture_cues = tuple(
        CueRule(source=source, cue=cue, segment_id=segment, weight=Percentage.create(100))
        for source, cue, segment in (
            # Simulator fixture labels (scripts/replay_audience.py, evals/scenarios).
            (SegmentSource.VISION, "sports ball", fitness),
            (SegmentSource.VISION, "tap handle fixture", beer),
            (SegmentSource.SOUND, "coffee service fixture", coffee),
            # COCO labels the live agent emits for the demo props
            # (edge/agent/vision/snapshot.py); at catalog weight a single cup or
            # bottle stays below the enter threshold and only the house ad plays.
            (SegmentSource.VISION, "bottle", fitness),  # the gym-bottle prop
            (SegmentSource.VISION, "cup", coffee),
            (SegmentSource.VISION, "wine glass", beer),
        )
    )
    fixture_keys = {(rule.source, rule.cue) for rule in fixture_cues}
    audience_catalog = loaded.catalog.with_cues(
        (
            *(rule for rule in loaded.catalog.cues if (rule.source, rule.cue) not in fixture_keys),
            *fixture_cues,
        )
    )
    segment_labels = {segment.segment_id: segment.label for segment in audience_catalog.segments}
    # Canonical keyterms the agent reports (edge/agent/speech/keyterms.py) -> segments.
    keyterm_catalog = {
        "gym": fitness,
        "energía": fitness,
        "café": coffee,
        "hambre": food,
        "comida": food,
        "cerveza": beer,
    }
    # Creative generation (docs/creative-generation.md): targeted stock copy on the hot
    # path, guarded AI copy as a warm upgrade when a copywriter is configured.
    catalog = CreativeCatalog(
        kits=kits_by_id,
        offers=offer_book,
        segment_angles={
            fitness: Angle.POST_WORKOUT,
            coffee: Angle.COFFEE_BREAK,
            beer: Angle.SOCIAL,
            food: Angle.HUNGER,
        },
    )
    studio = CreativeStudio(
        clock=clock,
        writer=copywriter,
        budget=GenerationBudget(hourly_cap=UsdCost.create(hourly_budget_usd)),
        config=StudioConfig(copy_timeout_s=copy_timeout_s),
    )
    house_spec = compose_template(
        "solo",
        (
            BrandKit(
                brand_id=BrandId.create(ids.new_uuid7()),
                name="team18",
                logo_url=logo_url,
                stock_copy="team18 · Acércate a la pantalla.",
                accent_color="#c6f26b",
            ),
        ),
    )
    current_creative = CreativeReady(
        screen_id=wire_screen_id,
        creative_id=str(ids.new_uuid7()),
        spec=house_spec,
        trace=LatencyTrace(ready_at=clock.now_utc()),
    )
    lock = market.lock
    current_moment: AudienceMomentSnapshot | None = None
    current_brief: CreativeBrief | None = None
    latest_observed_at: UtcTimestamp | None = None
    latest_console: tuple[ConsoleMessage, ...] = ()
    # Classified speech topics only; utterance text is never kept (ADR 0005).
    speech = SpeechEvidence()
    last_snapshot: tuple[AudienceSnapshot, Received] | None = None
    voice_degraded = False
    vlm_degraded = False

    def vlm_findings_landed(found_screen_id: str) -> None:
        # Re-run the decision on the latest observation with the VLM findings merged in.
        with lock:
            latest = last_snapshot
        if latest is not None and found_screen_id == wire_screen_id:
            decide(latest[0], Received(received_at=clock.now_utc()), reconsider=True)

    def vlm_degradation(wire_screen_id: str, signal: DegradedSignal, active: bool) -> None:
        nonlocal vlm_degraded
        with lock:
            if vlm_degraded == active:
                return
            vlm_degraded = active
        hub.broadcast_console_threadsafe(
            ConsoleDegradation(
                screen_id=wire_screen_id,
                flags=[DegradationFlag.VLM_UNAVAILABLE] if active else [],
            )
        )

    classifier = AudienceClassifier(
        catalog=audience_catalog,
        reliability=loaded.reliability,
        tagger=tagger,
        warm=warm,
        on_findings=vlm_findings_landed,
        on_degraded=vlm_degradation,
    )
    # The on-air play's exposure lease: (creative_id, result, looking, leases held).
    lease: tuple[str, AuctionResult, int, int] | None = None

    def receive_snapshot(snapshot: AudienceSnapshot, received: Received) -> None:
        decide(snapshot, received, reconsider=False)

    def decide(snapshot: AudienceSnapshot, received: Received, *, reconsider: bool) -> None:
        nonlocal current_creative, current_moment, latest_observed_at, latest_console
        nonlocal last_snapshot
        if snapshot.screen_id != wire_screen_id:
            return
        measured_at = UtcTimestamp.create(snapshot.measured_at.astimezone(UTC))
        if not reconsider and abs(received.received_at - measured_at.value) > MAX_MEASUREMENT_SKEW:
            return

        # Preserve the device instant in the trace and use receipt as the upper
        # bound for ordering so an ahead clock cannot block subsequent samples.
        observed_at = UtcTimestamp.create(min(measured_at.value, received.received_at))
        with lock:
            if (
                not reconsider
                and latest_observed_at is not None
                and observed_at.value <= latest_observed_at.value
            ):
                return
            if not reconsider:
                last_snapshot = (snapshot, received)
                # Every fresh reading feeds attention, even when the moment is unchanged.
                delivery.attention.record(
                    wire_screen_id,
                    measured_at.value,
                    (tracklet.id for tracklet in snapshot.tracklets if tracklet.looking),
                )
                looks = [t.dwell_s for t in snapshot.tracklets if t.looking]
                hub.send_to_screen_threadsafe(
                    ScreenAttention(
                        screen_id=wire_screen_id,
                        creative_id=current_creative.creative_id,
                        looking=len(looks) if snapshot.tracklets else snapshot.looking,
                        dwell_s=max(looks, default=0.0),
                        measured_at=measured_at.value,
                    )
                )
            classification = classifier.classify(snapshot, at=received.received_at)
            segments = merge_speech(
                classification.segments,
                speech,
                at=UtcTimestamp.create(received.received_at),
                looking=snapshot.looking,
            )
            minor_likely = classification.minor_likely
            held = classification.held_products
            if current_moment is not None and (
                snapshot.people,
                snapshot.looking,
                segments,
                minor_likely,
                held,
            ) == (
                current_moment.people,
                current_moment.looking,
                current_moment.segments,
                current_moment.minor_likely,
                current_moment.held_products,
            ):
                if not reconsider:
                    latest_observed_at = observed_at
                return
            moment = AudienceMomentSnapshot.create(
                screen_id=screen_id,
                moment_id=MomentId.create(ids.new_uuid7()),
                measured_at=measured_at,
                received_at=UtcTimestamp.create(received.received_at),
                moment_changed_at=UtcTimestamp.create(max(clock.now_utc(), received.received_at)),
                people=snapshot.people,
                looking=snapshot.looking,
                segments=segments,
                minor_likely=minor_likely,
                held_products=held,
            )
            if not reconsider:
                latest_observed_at = observed_at
            needs_voice = run_auction(snapshot, moment, Trigger.MOMENT_CHANGED)
        # Outside the lock: a cache hit upgrades the creative, which takes the lock.
        if needs_voice is not None:
            request_voiceover(needs_voice)

    def run_auction(
        snapshot: AudienceSnapshot,
        moment: AudienceMomentSnapshot,
        trigger: Trigger,
        *,
        out_of_budget: frozenset[CampaignId] = frozenset(),
    ) -> CreativeReady | None:
        """Clear `moment` against the live book and stock. Caller holds the lock."""
        nonlocal current_moment, latest_console
        # The on-air play's own hold doesn't count against a re-auction it is part of.
        bidders = book.snapshot(excluding_hold=current_creative.creative_id)
        if out_of_budget:
            bidders = tuple(
                replace(b, budget_remaining=Money.zero(currency="CLP"))
                if b.campaign_id in out_of_budget
                else b
                for b in bidders
            )
        outcome = desk.run(
            AdOpportunity(
                moment=moment,
                bidders=bidders,
                layouts=layouts,
                reserve=reserve,
                approved_baskets=offer_book.baskets(),
                out_of_stock=stock.out_of_stock(wire_screen_id),
                max_play_seconds=Decimal(MAX_PLAY_SECONDS),
                # Complete the basket (ADR 0008 D2): a held brand boosts its partners.
                held_products=tuple(
                    BidHeldProduct(
                        brand_id=brand_by_slug[product.brand_ref],
                        confidence=product.confidence,
                    )
                    for product in moment.held_products
                    if product.brand_ref is not None and product.brand_ref in brand_by_slug
                ),
            ),
            trigger,
            now=clock.now_utc(),
        )
        cleared_at = clock.now_utc()
        if outcome.result is None:
            # Engine failed: the last good creative keeps playing, and the moment
            # stays unrecorded so the next identical snapshot re-auctions it.
            return None
        if outcome.result.no_fill is not NoFillReason.ENGINE_ERROR:
            current_moment = moment  # an engine-error fallback retries the same audience
        auction_id = str(ids.new_uuid7())
        auctions.append(
            AuctionRecord(
                auction_id=auction_id,
                screen_id=wire_screen_id,
                moment=moment,
                trigger=trigger,
                result=outcome.result,
                cleared_at=cleared_at,
            )
        )
        trace = LatencyTrace(
            measured_at=moment.measured_at.value,
            received_at=moment.received_at.value,
            moment_changed_at=moment.moment_changed_at.value,
            cleared_at=cleared_at,
            swap_not_before=outcome.not_before,
        )
        decision = _Decision(
            snapshot=snapshot,
            moment=moment,
            result=outcome.result,
            auction_id=auction_id,
            trace=trace,
            trigger=trigger,
        )
        if outcome.swap_now:
            return publish(decision, swap="swap")
        swap = "held" if outcome.hold_token is not None else outcome.action.value
        # Arm the release before anyone hears "held" (the lock keeps it from firing early).
        if outcome.hold_token is not None and outcome.not_before is not None:
            schedule_release(outcome.hold_token, decision, outcome.not_before)
        latest_console = console_for(decision, creative_id=None, swap=swap)
        hub.broadcast_console_threadsafe(*latest_console)
        return None

    def reauction(trigger: Trigger, *, out_of_budget: frozenset[CampaignId] = frozenset()) -> None:
        """Re-clear the moment on screen after a bid, stock or budget change (same audience)."""
        with lock:
            snapshot = last_snapshot
            moment = current_moment
            needs_voice = (
                run_auction(snapshot[0], moment, trigger, out_of_budget=out_of_budget)
                if snapshot is not None and moment is not None
                else None
            )
            view = book_view()
        hub.broadcast_console_threadsafe(view)
        if needs_voice is not None:
            request_voiceover(needs_voice)

    def console_for(
        decision: _Decision, *, creative_id: str | None, swap: str
    ) -> tuple[ConsoleMessage, ...]:
        return decision_messages(
            snapshot=decision.snapshot,
            moment=decision.moment,
            bidders=book.snapshot(),
            result=decision.result,
            kits=kits_by_id,
            segment_labels=segment_labels,
            auction_id=decision.auction_id,
            trace=decision.trace,
            creative_id=creative_id,
            trigger=decision.trigger.value,
            swap=swap,
        )

    def publish(decision: _Decision, *, swap: str) -> CreativeReady | None:
        """Compose and push the decision's creative. Caller holds the lock.

        Hot path: brief → cached AI or targeted stock copy, no network. A warm AI-copy
        upgrade is queued (non-blocking) when this brief has none cached yet.
        """
        nonlocal current_creative, latest_console, current_brief
        result = decision.result
        spec = house_spec
        brief = build_brief(moment=decision.moment, result=result, catalog=catalog)
        if brief is not None:
            composed = studio.compose(brief)
            if composed.score.verdict.passed:
                spec = with_cached_voice(render_spec(brief, composed.draft, kits_by_id))
            else:
                # Even stock copy failed the guard (e.g. a kit line unsafe for this
                # moment): the house ad airs rather than an unsafe line.
                brief = None
        current_brief = brief
        creative_id = str(ids.new_uuid7())
        if result.winners and spec is not house_spec:
            spec = with_basket_extras(
                spec, result, decision.auction_id, creative_id, clock.now_utc()
            )
        # Includes redemption, QR encoding and loop lookup in the decision budget.
        ready_at = clock.now_utc()
        trace = decision.trace.model_copy(update={"ready_at": ready_at})
        current_creative = CreativeReady(
            screen_id=wire_screen_id,
            creative_id=creative_id,
            auction_id=decision.auction_id if result.winners else None,
            spec=spec,
            trace=trace,
        )
        # The player shows it after preload; ready_at is our best server-side stand-in.
        desk.aired(result, displayed_at=ready_at)
        if result.winners:
            # Bill what this creative's own auction cleared, and hold its worst case.
            delivery.aired(
                PlayRecord(
                    creative_id=current_creative.creative_id,
                    auction_id=decision.auction_id,
                    screen_id=wire_screen_id,
                    people=decision.moment.people,
                    looking=decision.moment.looking,
                    aired_at=ready_at,
                    slots=tuple(
                        BilledSlot(
                            campaign_id=winner.campaign_id,
                            brand_id=winner.bidder.brand_id,
                            price_per_as=winner.price_per_as,
                        )
                        for winner in result.winners
                    ),
                )
            )
            worst_case = Decimal(decision.moment.looking * MAX_PLAY_SECONDS)
            book.reserve(
                current_creative.creative_id,
                {
                    winner.campaign_id: Money.create(
                        currency=winner.price_per_as.currency,
                        amount=winner.price_per_as.amount * worst_case,
                        rounded=True,
                        rounding_policy=_CEILING,
                    )
                    for winner in result.winners
                },
                at=ready_at,
            )
            set_lease(current_creative.creative_id, result, decision.moment.looking, 1)
            schedule_lease(current_creative.creative_id, result, decision.moment.looking, 1)
        latest_console = console_for(
            _Decision(
                snapshot=decision.snapshot,
                moment=decision.moment,
                result=result,
                auction_id=decision.auction_id,
                trace=trace,
                trigger=decision.trigger,
            ),
            creative_id=current_creative.creative_id,
            swap=swap,
        )
        hub.broadcast_console_threadsafe(*latest_console)
        hub.send_to_screen_threadsafe(current_creative)
        if brief is not None and studio.needs_upgrade(brief):
            request_copy(
                _CopyJob(
                    brief=brief,
                    value_per_s_clp=result.bundle_value.amount,
                )
            )
        return current_creative if result.winners and decision.moment.looking > 0 else None

    def set_lease(creative_id: str, result: AuctionResult, looking: int, n: int) -> None:
        nonlocal lease
        lease = (creative_id, result, looking, n)

    def lease_holds(result: AuctionResult, looking: int, leases: int) -> dict[CampaignId, Money]:
        exposure = Decimal(looking * MAX_PLAY_SECONDS * leases)
        return {
            winner.campaign_id: Money.create(
                currency=winner.price_per_as.currency,
                amount=winner.price_per_as.amount * exposure,
                rounded=True,
                rounding_policy=_CEILING,
            )
            for winner in result.winners
        }

    def schedule_lease(creative_id: str, result: AuctionResult, looking: int, lease: int) -> None:
        """Every MAX_PLAY_SECONDS on air, the hold must grow to cover the next stretch.

        The player reports a play only when it ends, so this is how a long play stays
        within budget in real time: a brand that can't cover the next lease comes
        down at once (budget is an on-air-unsafe reason), and its charge is capped.
        """

        def renew() -> None:
            with lock:
                if current_creative.creative_id != creative_id:
                    return  # replaced: its charge settles the hold
                short = book.extend_hold(
                    creative_id, lease_holds(result, looking, lease + 1), at=clock.now_utc()
                )
                if not short:
                    set_lease(creative_id, result, looking, lease + 1)
                    schedule_lease(creative_id, result, looking, lease + 1)
            if short:
                reauction(Trigger.BUDGET_CHANGED, out_of_budget=short)

        scheduler(float(MAX_PLAY_SECONDS), renew)

    def schedule_release(token: int, decision: _Decision, not_before: datetime) -> None:
        delay = max(0.0, (not_before - clock.now_utc()).total_seconds())

        def release() -> None:
            with lock:
                if desk.release(token) is None:
                    return  # superseded by a newer decision
                needs_voice = publish(decision, swap="released")
            if needs_voice is not None:
                request_voiceover(needs_voice)

        scheduler(delay, release)

    # ------------------------------------------------------------ book, bidding war, stock

    def book_view() -> ConsoleBook:
        on_air = desk.on_air.campaign_ids if desk.on_air is not None else frozenset()
        out = stock.out_of_stock(wire_screen_id)
        lines = []
        for entry in book.entries():
            bidder, account = entry.bidder, entry.account
            kit = kits_by_id[bidder.brand_id]
            zero = Money.zero(currency="CLP")
            lines.append(
                BookLine(
                    campaign_id=str(bidder.campaign_id),
                    brand_id=str(bidder.brand_id),
                    brand_name=kit.name,
                    category=bidder.category,
                    accent_color=kit.accent_color,
                    multiplier=format(bidder.live_multiplier.value.normalize(), "f"),
                    multiplier_seq=entry.multiplier_seq,
                    in_stock=bidder.brand_id not in out,
                    on_air=bidder.campaign_id in on_air,
                    budget=_money(account.budget if account else zero),
                    spent=_money(account.spent if account else zero),
                    reserved=_money(account.reserved if account else zero),
                    remaining=_money(account.remaining if account else zero),
                )
            )
        return ConsoleBook(screen_id=wire_screen_id, lines=lines)

    def set_multiplier(message: AdvertiserBidMultiplier) -> MultiplierOutcome:
        campaign_id = _campaign_id(message.campaign_id)
        if campaign_id not in campaign_of:
            raise UnknownCampaign()
        outcome = book.set_live_multiplier(
            campaign_id, BidMultiplier.create(message.multiplier), message.seq
        )
        if outcome is MultiplierOutcome.APPLIED:
            reauction(Trigger.BID_CHANGED)
        return outcome

    def set_stock(message: StockSet) -> bool:
        if message.screen_id != wire_screen_id:
            raise UnknownBrand()
        brand_id = _brand_id(message.brand_id)
        if brand_id not in kits_by_id:
            raise UnknownBrand()
        changed = stock.set(wire_screen_id, brand_id, in_stock=message.in_stock)
        if changed:
            reauction(Trigger.STOCK_CHANGED)
        return changed

    # ------------------------------------------------------------ held products, tags

    def extract_topics(utterance: AudienceUtterance, received: Received) -> None:
        if topic_extractor is None or warm is None:
            return
        text, language = utterance.text, utterance.language  # in memory only, never logged
        catalog_text = {segment.value: label for segment, label in segment_labels.items()}
        by_key = {segment.value: segment for segment in segment_labels}

        async def job() -> None:
            nonlocal speech
            found = await topic_extractor.extract(text, language=language, catalog=catalog_text)
            if not found:
                return
            heard = UtcTimestamp.create(received.received_at)
            expires = UtcTimestamp.create(received.received_at + speech_ttl)
            topics = tuple(
                SpeechTopic(
                    segment_id=by_key[topic.segment_key],
                    intent=Percentage.create(topic.intent * 100),
                    heard_at=heard,
                    expires_at=expires,
                )
                for topic in found
            )
            with lock:
                speech = speech.heard(topics)
                snapshot = last_snapshot
            if snapshot is not None:
                decide(snapshot[0], Received(received_at=received.received_at), reconsider=True)

        warm.submit_threadsafe("claude", f"topics:{utterance.screen_id}:{utterance.seq}", job)

    # ------------------------------------------------------------ QR, redemption, video

    def with_basket_extras(
        spec: CreativeSpec,
        result: AuctionResult,
        auction_id: str,
        creative_id: str,
        at: datetime,
    ) -> CreativeSpec:
        """Add the basket's redemption code + QR and its pre-generated loop (if any)."""
        brands = [winner.bidder.brand_id for winner in result.winners]
        offer = redemptions.issue(
            auction_id=auction_id,
            creative_id=creative_id,
            screen_id=wire_screen_id,
            campaign_ids=[winner.campaign_id for winner in result.winners],
            brand_names=[kits_by_id[brand].name for brand in brands],
            offer_text=spec.offer.text if spec.offer is not None else None,
            at=at,
        )
        redeem_url = f"{public_base_url.rstrip('/')}/r/{offer.code}"
        update: dict[str, object] = {
            "redemption_code": offer.code,
            "redeem_url": redeem_url,
            "qr_url": qr_data_url(redeem_url),
        }
        if loops is not None and spec.video_url is None:
            update["video_url"] = loops.url_for([slug_of[brand] for brand in brands])
        return spec.model_copy(update=update)

    def redeem(code: str, nonce: str) -> tuple[ConsoleRedemption, bool]:
        redemption, created = redemptions.redeem(code, nonce, at=clock.now_utc())
        offer = redemptions.offer(code)
        message = ConsoleRedemption(
            screen_id=redemption.screen_id,
            auction_id=redemption.auction_id,
            code=redemption.code,
            brands=list(offer.brand_names) if offer else [],
            offer_text=offer.offer_text if offer else None,
            redeemed_at=redemption.redeemed_at,
        )
        if created:
            hub.broadcast_console_threadsafe(message)
        return message, created

    def receive_displayed(report: PlayerDisplayed, received: Received) -> None:
        """Close glass-to-glass: the player made a creative visible."""
        nonlocal current_creative
        if report.screen_id != wire_screen_id:
            return
        displayed_at = report.displayed_at.astimezone(UTC)
        with lock:
            target = current_creative
            if target.creative_id != report.creative_id:
                return  # a stale report for a creative already replaced
            trace = target.trace.model_copy(update={"displayed_at": displayed_at})
            current_creative = target.model_copy(update={"trace": trace})
            desk.displayed(displayed_at)  # minimum ad seconds count from what people saw
        hub.broadcast_console_threadsafe(
            ConsoleLatency(screen_id=wire_screen_id, creative_id=report.creative_id, trace=trace)
        )

    # ------------------------------------------------------------ delivery (billing)

    def receive_playback(report: PlaybackReport, received: Received) -> None:
        if report.screen_id != wire_screen_id:
            return
        receipt = delivery.record_playback(
            screen_id=report.screen_id,
            creative_id=report.creative_id,
            seq=report.seq,
            started_at=report.started_at.astimezone(UTC),
            ended_at=report.ended_at.astimezone(UTC),
            recorded_at=received.received_at,
        )
        if receipt is None:
            return  # house ad, unknown creative or a retry already billed
        # The charge was booked (and capped) against budgets inside billing.
        hub.broadcast_console_threadsafe(receipt_message(receipt), book_view())
        # That spend may leave the brand on air unable to cover its current lease.
        with lock:
            short = (
                book.extend_hold(
                    lease[0], lease_holds(lease[1], lease[2], lease[3]), at=clock.now_utc()
                )
                if lease is not None and lease[0] == current_creative.creative_id
                else frozenset()
            )
        if short:
            reauction(Trigger.BUDGET_CHANGED, out_of_budget=short)

    def receipt_message(receipt: Receipt) -> ConsoleReceipt:
        return ConsoleReceipt(
            screen_id=receipt.play.screen_id,
            creative_id=receipt.play.creative_id,
            auction_id=receipt.play.auction_id,
            people=receipt.play.people,
            looking=receipt.play.looking,
            played_s=format(receipt.played_s, "f"),
            attention_seconds=format(receipt.attention.attention_seconds, "f"),
            charges=[
                ChargeLine(
                    campaign_id=str(charge.campaign_id),
                    brand_name=kits_by_id[charge.brand_id].name,
                    attention_seconds=format(charge.attention_seconds, "f"),
                    price_per_as=MoneyDTO(
                        amount=format(charge.price_per_as.amount.normalize(), "f"),
                        currency=str(charge.price_per_as.currency),
                    ),
                    amount=_money(charge.amount),
                    reason=charge.reason.value,  # type: ignore[arg-type]
                )
                for charge in receipt.charges
            ],
            total=_money(receipt.total),
        )

    # ------------------------------------------------------------ speech (opt-in screens)

    def receive_utterance(utterance: AudienceUtterance, received: Received) -> None:
        nonlocal speech
        if utterance.screen_id != wire_screen_id or wire_screen_id not in speech_screens:
            return  # speech mode is off for this screen: drop it, unread
        topics = topics_from_keyterms(
            utterance.keyterms_hit,
            keyterm_catalog,
            heard_at=UtcTimestamp.create(received.received_at),
            ttl=speech_ttl,
        )
        if not topics:
            # No keyterm: the LLM topic extractor refines on the warm path (never blocks).
            extract_topics(utterance, received)
            return
        with lock:
            speech = speech.heard(topics)
            snapshot = last_snapshot
        if snapshot is not None:
            # Re-run the decision on the latest observation with speech merged in.
            decide(snapshot[0], Received(received_at=received.received_at), reconsider=True)

    # ------------------------------------------------------------ AI copy (warm)

    def request_copy(job: _CopyJob) -> None:
        """Queue Claude copy → guard → score; never blocks (safe under the lock)."""
        if warm is None:
            return

        async def run() -> None:
            # The worker admits this job only after acquiring the provider slot.
            # Do not spend that slot or tokens on a moment that left while queued.
            with lock:
                if current_brief is None or current_brief.signature() != job.brief.signature():
                    return
            draft = await studio.upgrade_copy(job.brief, value_per_s_clp=job.value_per_s_clp)
            if draft is not None:
                upgrade_copy(job, draft)

        warm.submit_threadsafe("claude", f"copy:{wire_screen_id}:{job.brief.signature()}", run)

    def upgrade_copy(job: _CopyJob, draft: CopyDraft) -> None:
        """Apply to whatever creative airs this brief now; a different brief means stale, drop it.

        Not keyed by creative id: the warm worker dedupes by brief signature, so a new
        creative for the same brief shares this job and must still get the upgrade.
        """
        nonlocal current_creative
        with lock:
            target = current_creative
            brief = current_brief
            # Same signature = same copy deserved (people may shift by one passer-by during
            # the call). A different signature is stale: drop it.
            if brief is None or brief.signature() != job.brief.signature():
                return
            if target.spec.copy_source is CopySourceKind.AI:
                return
            if brief != job.brief:
                composed = studio.compose(brief)  # re-guards the cached draft for this brief
                if composed.draft.source is CopySource.STOCK:
                    return
                draft = composed.draft
            # Keep the basket's QR/redemption and media while replacing only copy.
            spec = with_cached_voice(render_copy_upgrade(target.spec, brief, draft, kits_by_id))
            trace = target.trace.model_copy(update={"upgraded_at": clock.now_utc()})
            current_creative = target.model_copy(update={"spec": spec, "trace": trace})
            upgraded = CreativeUpgraded(
                screen_id=wire_screen_id,
                creative_id=target.creative_id,
                auction_id=target.auction_id,
                upgrade_kind=UpgradeKind.COPY,
                spec=spec,
                trace=trace,
            )
            needs_voice = current_creative if brief.party > 0 else None
        hub.send_to_screen_threadsafe(upgraded)
        hub.broadcast_console_threadsafe(upgraded)
        if needs_voice is not None:
            request_voiceover(needs_voice)

    # ------------------------------------------------------------ voice-overs (warm)

    def spoken_copy(spec: CreativeSpec) -> str:
        # The approved offer (stable per tier, so pre-generated and always cached),
        # normalized for speech; the lead copy only when there is no offer.
        return spoken_line_for(spec)

    def with_cached_voice(spec: CreativeSpec) -> CreativeSpec:
        """Hot path: attach a pre-generated voice-over now (a file check), so it plays
        with the creative instead of arriving later as an upgrade."""
        if voiceovers is None or (voice := voice_for(spec)) is None:
            return spec
        cached = voiceovers.cached_url(spoken_copy(spec), voice)
        return spec if cached is None else spec.model_copy(update={"voiceover_url": cached})

    async def prewarm_voiceovers() -> None:
        """Cold path: synthesize every approved offer line once (budget-guarded, cached on disk)."""
        if voiceovers is None or narrator_voice_id is None:
            return
        for basket in sorted(offer_book.baskets(), key=lambda b: sorted(map(str, b))):
            for tier in offer_book.tiers(basket):
                line = offer_line(tier.label, tier.text, str(tier.price.amount))
                await voiceovers.voiceover_url(line, narrator_voice_id)

    if startup is not None and voiceovers is not None:
        startup.append(prewarm_voiceovers)

    def voice_for(spec: CreativeSpec) -> str | None:
        return narrator_voice_id  # co-brand and solo both use the narrator until kits carry voices

    def request_voiceover(creative: CreativeReady) -> None:
        if voiceovers is None or warm is None or (voice := voice_for(creative.spec)) is None:
            return
        line = spoken_copy(creative.spec)
        cached = voiceovers.cached_url(line, voice)
        if cached is not None:  # hot path: a file check, no network
            upgrade_voice(creative.creative_id, line, voice, cached)
            return
        try:
            key = voiceovers.key_for(line, voice)
        except InvalidSpokenLine:
            return

        async def job() -> None:
            nonlocal voice_degraded
            # A queued line may no longer belong to the current ad. Reuse by line
            # and voice (not creative id) when the same offer returns before admission.
            with lock:
                if (
                    spoken_copy(current_creative.spec) != line
                    or voice_for(current_creative.spec) != voice
                ):
                    return
            characters = voiceovers.stats.characters
            url = await voiceovers.voiceover_url(line, voice)
            if (spent := voiceovers.stats.characters - characters) > 0:
                studio.record_spend(GenerationKind.VOICE, studio.cost_model.voice(spent))
            if url is not None:
                upgrade_voice(None, line, voice, url)
            elif voiceovers.stats.disabled_by is not None and not voice_degraded:
                voice_degraded = True
                hub.broadcast_console_threadsafe(
                    ConsoleDegradation(
                        screen_id=wire_screen_id, flags=[DegradationFlag.VOICE_FALLBACK]
                    )
                )

        warm.submit_threadsafe("elevenlabs", f"voice:{wire_screen_id}:{key}", job)

    def upgrade_voice(creative_id: str | None, line: str, voice: str, url: str) -> None:
        """Apply only while the target creative is still on air (stale results are dropped)."""
        nonlocal current_creative
        with lock:
            target = current_creative
            if creative_id is not None and target.creative_id != creative_id:
                return
            if spoken_copy(target.spec) != line or voice_for(target.spec) != voice:
                return
            if target.spec.voiceover_url == url:
                return
            spec = target.spec.model_copy(update={"voiceover_url": url})
            trace = target.trace.model_copy(update={"upgraded_at": clock.now_utc()})
            current_creative = target.model_copy(update={"spec": spec, "trace": trace})
            upgraded = CreativeUpgraded(
                screen_id=wire_screen_id,
                creative_id=target.creative_id,
                auction_id=target.auction_id,
                upgrade_kind=UpgradeKind.VOICE,
                spec=spec,
                trace=trace,
            )
        hub.send_to_screen_threadsafe(upgraded)
        hub.broadcast_console_threadsafe(upgraded)

    def player_connected(connected_screen_id: str, sink: Sink) -> None:
        if connected_screen_id == wire_screen_id:
            with lock:
                hub.send_to_screen_threadsafe(current_creative, sink=sink)

    def console_connected(sink: Sink) -> None:
        with lock:
            # The latest decision first (consumers read it in order), then the book.
            if latest_console:
                hub.broadcast_console_threadsafe(*latest_console, book_view(), sink=sink)

    def receive_keyframe(keyframe: AudienceKeyframe, received: Received) -> None:
        # Face-blurred JPEG, in memory only (never stored or logged); tagged on the warm path.
        # Recognition-stream crops (session_id set) belong to RecognitionVlm.
        if (
            keyframe.screen_id == wire_screen_id
            and keyframe.session_id is None
            and received.payload is not None
        ):
            classifier.observe_keyframe(
                keyframe, jpeg=received.payload, received_at=received.received_at
            )

    router.on(AudienceSnapshot, receive_snapshot)
    router.on(AudienceKeyframe, receive_keyframe)
    router.on(AudienceUtterance, receive_utterance)
    router.on(PlaybackReport, receive_playback)
    router.on_player_connected(player_connected)
    router.on_console_connected(console_connected)
    router.on(PlayerDisplayed, receive_displayed)
    return DemoControls(
        book_view=book_view,
        set_multiplier=set_multiplier,
        set_stock=set_stock,
        offer_for=redemptions.offer,
        redeem=redeem,
        reauction_bids=lambda: reauction(Trigger.BID_CHANGED),
    )


_CEILING = RoundingPolicy.create(money_rounding=ROUND_CEILING)


def _slug(name: str) -> str:
    """'Café Patio' → 'cafe-patio' (matches edge/props/markers.json)."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "-".join(plain.lower().split())


def _money(value: Money) -> MoneyDTO:
    return MoneyDTO(amount=format(value.amount, "f"), currency=str(value.currency))


def _campaign_id(raw: str) -> CampaignId:
    try:
        return CampaignId.create(UUID(raw))
    except ValueError as exc:
        raise UnknownCampaign() from exc


def _brand_id(raw: str) -> BrandId:
    try:
        return BrandId.create(UUID(raw))
    except ValueError as exc:
        raise UnknownBrand() from exc


def _tiers(
    brands: frozenset[BrandId], names: str, rows: tuple[tuple[int, str, str, int], ...]
) -> list[BasketOffer]:
    """Seed one basket's group-size offers: (min party, label, text suffix, CLP price)."""
    return [
        BasketOffer(
            brand_ids=brands,
            min_party=party,
            label=label,
            text=f"{names}{suffix}",
            price=Money.create(currency="CLP", amount=price),
        )
        for party, label, suffix, price in rows
    ]


@dataclass(frozen=True, slots=True, kw_only=True)
class _CopyJob:
    """A warm AI-copy request for the creative that just went on air."""

    brief: CreativeBrief
    value_per_s_clp: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class _Decision:
    """One cleared auction and the facts it was cleared against (simulator wiring only)."""

    snapshot: AudienceSnapshot
    moment: AudienceMomentSnapshot
    result: AuctionResult
    auction_id: str
    trace: LatencyTrace
    trigger: Trigger
