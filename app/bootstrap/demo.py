"""Compose one screen's live snapshot-to-creative flow and the shared seed market."""

import logging
from base64 import b64encode
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from threading import Lock, RLock
from uuid import UUID
from zoneinfo import ZoneInfo

from app.bootstrap import demo_catalog, dev_stream
from app.bootstrap.demo_catalog import wordmark
from app.bootstrap.demo_console import decision_messages
from app.contract.messages import (
    AdvertiserAutoBid,
    AdvertiserBidMultiplier,
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    BookLine,
    ChargeLine,
    ConsoleBook,
    ConsoleDegradation,
    ConsoleLatency,
    ConsoleNext,
    ConsoleReceipt,
    ConsoleRedemption,
    CopySourceKind,
    CreativeReady,
    CreativeSpec,
    CreativeUpgraded,
    DegradationFlag,
    DevCreative,
    DevKeyframe,
    DevPlayer,
    DevSpeech,
    DistanceBand,
    FilteredAttention,
    LatencyTrace,
    MinorLikely,
    MoneyDTO,
    PlaybackReport,
    PlayerDisplayed,
    PlayerVoice,
    ScreenAttention,
    StockSet,
    Tracklet,
    UpgradeKind,
)
from app.contract.speech_matching import DEFAULT_KEYTERMS
from app.modules.auction.application.desk import AuctionDesk
from app.modules.auction.application.ports.auction_log import AuctionLog, AuctionRecord
from app.modules.auction.domain.affinity import RelatedSegments, SegmentAffinity
from app.modules.auction.domain.opportunity import (
    AdOpportunity,
    CategoryMultiplier,
    HeldProduct as BidHeldProduct,
    Layout,
)
from app.modules.auction.domain.result import AuctionResult, ExclusionReason, NoFillReason
from app.modules.auction.domain.swap import Trigger
from app.modules.auction.infrastructure.auction_log import InMemoryAuctionLog
from app.modules.auction.infrastructure.engines.in_process import InProcessBundleEngine
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.application.classifier import (
    AudienceClassifier,
    DegradedSignal,
    TagReport,
)
from app.modules.audience.application.playback_echo import PlaybackEcho
from app.modules.audience.application.ports import AudienceTagger
from app.modules.audience.application.ports.tagger import WarmScheduler
from app.modules.audience.application.ports.topics import TopicExtractor, TopicOutcome
from app.modules.audience.domain.catalog import CueRule, ProductRef
from app.modules.audience.domain.policies.group_default import GroupDefault
from app.modules.audience.domain.speech_evidence import (
    SpeechEvidence,
    SpeechTopic,
    merge_speech,
    topics_from_keyterms,
)
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.campaigns.application.autobidder import AutoBidder
from app.modules.campaigns.application.live_book import (
    RESERVATION_TTL,
    LiveCampaignBook,
    MultiplierOutcome,
    UnknownCampaign,
)
from app.modules.campaigns.application.ports.registry import CampaignRegistry
from app.modules.campaigns.application.stock import StockBoard
from app.modules.campaigns.domain.autobid import AutoBidGoal
from app.modules.campaigns.domain.book import (
    Bidder,
    BidMultiplier,
    BrandId,
    BrandKit,
    CampaignId,
    SegmentBid,
)
from app.modules.campaigns.infrastructure.registry import InMemoryCampaignRegistry
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.rounding_policy import RoundingPolicy
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.creative.application.briefing import CreativeCatalog, MomentContext, build_brief
from app.modules.creative.application.campaigns import (
    CampaignMedia,
    apply_campaign_media,
    category_key,
    media_for,
)
from app.modules.creative.application.hook_lab import HookLab, HookResult
from app.modules.creative.application.loops import BundleLoopLibrary
from app.modules.creative.application.ports.copywriter import CopyWriter
from app.modules.creative.application.rendering import render_copy_upgrade, render_spec
from app.modules.creative.application.speech_text import offer_line, spoken_line_for
from app.modules.creative.application.studio import Composed, CreativeStudio, StudioConfig
from app.modules.creative.application.template import compose_template
from app.modules.creative.application.voiceover import InvalidSpokenLine, VoiceoverService
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief, daypart_for_hour
from app.modules.creative.domain.cost import GenerationBudget, GenerationKind, UsdCost
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.offer import BasketOffer, OfferBook
from app.modules.creative.domain.party import GROUP_WINDOW, LookingSample, addressed_party
from app.modules.creative.infrastructure.campaign_assets import (
    CAMPAIGN_ASSETS,
    load_campaign_media,
)
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

DEMO_SCREEN = "demo-01"
MAX_MEASUREMENT_SKEW = timedelta(seconds=10)
# Worst-case play length held against a winner's budget while it is on air (R-B6/R-B7).
MAX_PLAY_SECONDS = 15
logger = logging.getLogger(__name__)


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
    set_autobid: Callable[[AdvertiserAutoBid], bool] | None = None
    book_for: Callable[[str], ConsoleBook] | None = None
    # The live switch (app/realtime/live_switch.py): standby airs what an empty room
    # gets; forget_speech drops spoken topics when the mic goes off mid-moment.
    standby: Callable[[], None] = lambda: None
    forget_speech: Callable[[], None] = lambda: None
    # Hook testing (HookLab): every approved hook option with its plays, attention, scans.
    hook_results: Callable[[], list[HookResult]] = lambda: []


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
    campaign_media: dict[str, CampaignMedia]
    house_logo_url: str
    # One learner for the whole market: every screen's auctions and plays feed it.
    autobidder: AutoBidder
    autobid_lock: Lock = field(default_factory=Lock)
    # Which brand catalog this market runs (DEMO_CATALOG): fictional or chile.
    catalog: demo_catalog.Catalog = demo_catalog.FICTIONAL


def seed_market(
    *,
    ids: IdGenerator,
    budget_clp: int,
    auction_log: AuctionLog | None = None,
    ledger: ChargeLedger | None = None,
    redemption_ledger: RedemptionLedger | None = None,
    registry: CampaignRegistry | None = None,
    catalog: demo_catalog.Catalog = demo_catalog.FICTIONAL,
) -> LiveMarket:
    # Keep the project mark on the house ad. The fictional brands have fixed
    # mock marks included with their kits.
    logo = Path(__file__).resolve().parents[2].joinpath("project-logo.png").read_bytes()
    logo_url = "data:image/png;base64," + b64encode(logo).decode("ascii")
    # Brands use their own wordmarks (demo_catalog.wordmark); never AI-drawn logos.

    # One brand catalog (app/bootstrap/demo_catalog.py, docs/demo.md): rivals in each
    # category, pre-approved partner pairs, offers sized to the group (architecture §6.1).
    # Project mark on the house ad; brands with a mock mark in the campaign assets use it,
    # the rest get a generated wordmark. Never an AI-drawn logo.
    logo = Path(__file__).resolve().parents[2].joinpath("project-logo.png").read_bytes()
    logo_url = "data:image/png;base64," + b64encode(logo).decode("ascii")
    # Stable ids, budgets and live bids come from the registry (Postgres in prod), so
    # charges keep pointing at the same campaign and spend survives a restart.
    registry = registry or InMemoryCampaignRegistry(ids)
    # A brand with its own budget opens with it (the Chile catalog's uneven market);
    # the rest share `budget_clp`.
    stored = registry.open(
        [brand.slug for brand in catalog.brands],
        budget=Money.create(currency="CLP", amount=budget_clp),
        budgets={
            brand.slug: Money.create(currency="CLP", amount=brand.budget_clp)
            for brand in catalog.brands
            if brand.budget_clp is not None
        },
    )
    kits = tuple(
        BrandKit(
            brand_id=stored[brand.slug].brand_id,
            name=brand.name,
            logo_url=_brand_logo(brand.slug, brand.name, brand.color),
            stock_copy=brand.stock_copy,
            accent_color=brand.color,
            tagline=brand.tagline,
            voice=brand.voice,
        )
        for brand in catalog.brands
    )
    # Stable brand slugs: prop markers (edge/props/markers.json, ADR 0008 D2), the
    # audience catalog's brand_refs and the bundle loop files all use them.
    brand_by_slug = {
        brand.slug: kit.brand_id for brand, kit in zip(catalog.brands, kits, strict=True)
    }
    # Every mutual-partner pair gets an approved basket (docs/demo.md), so a rival
    # competes for its slot inside a basket and prices it. The price is the brands',
    # never the copywriter's.
    offer_book = OfferBook.of(
        [
            offer
            for basket in catalog.baskets
            for offer in _tiers(
                frozenset(brand_by_slug[slug] for slug in basket.slugs),
                basket.names,
                basket.tiers,
            )
        ]
    )
    # Rates are CLP per attention-second (docs/auction-plan.md D4b).
    bidders = tuple(
        Bidder(
            campaign_id=stored[brand.slug].campaign_id,
            brand_id=brand_by_slug[brand.slug],
            category=brand.category,
            age_restricted=brand.age_restricted,
            # The first segment is the brand's core audience; a secondary rate (when
            # set) prices the rest lower, so rivals win different moments.
            segment_bids=tuple(
                SegmentBid(
                    segment_id=SegmentId.create(segment),
                    rate=_clp_per_as(
                        brand.rate_clp_per_as
                        if index == 0 or brand.secondary_rate_clp_per_as is None
                        else brand.secondary_rate_clp_per_as
                    ),
                )
                for index, segment in enumerate(brand.segments)
            ),
            partners=frozenset(brand_by_slug[slug] for slug in catalog.partners_of(brand.slug)),
            live_multiplier=stored[brand.slug].multiplier,
            budget_remaining=stored[brand.slug].budget,
        )
        for brand in catalog.brands
    )
    book = LiveCampaignBook(
        bidders,
        spent={campaign.campaign_id: campaign.spent for campaign in stored.values()},
        multiplier_seqs={
            campaign.campaign_id: campaign.multiplier_seq for campaign in stored.values()
        },
        on_multiplier=registry.multiplier_changed,
    )
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
        campaign_media={
            key: media
            for key, media in load_campaign_media().items()
            if catalog.category_scenes or not key.startswith(category_key(""))
        },
        house_logo_url=logo_url,
        autobidder=AutoBidder(book),
        catalog=catalog,
    )


def register_demo(
    *,
    router: InboundRouter,
    wire_screen_id: str = DEMO_SCREEN,
    market: LiveMarket | None = None,
    hub: ConnectionHub,
    clock: BaseClock,
    ids: IdGenerator,
    warm: WarmScheduler | None = None,
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
    copy_timeout_s: float = 6.0,
    hourly_budget_usd: Decimal = Decimal(2),
    budget_clp: int = 50_000,
    startup: list[Callable[[], Awaitable[None]]] | None = None,
    topic_extractor: TopicExtractor | None = None,
    redemption_ledger: RedemptionLedger | None = None,
    public_base_url: str = "http://localhost:5173",
    media_dir: Path | None = None,
    dev_keyframe_preview: bool = False,
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
    slug_of = {kit.brand_id: _slug(kit.name) for kit in kits}
    brand_by_slug = {slug: brand for brand, slug in slug_of.items()}
    fitness, coffee, beer, food = (SegmentId.create(key) for key in ("408", "977", "369", "1404"))
    # A watching group with no other signal: a low-confidence coffee moment, never a
    # house ad for a whole queue (group_default.py; measured on real footage).
    group_default = GroupDefault(segment_id=coffee)
    layouts = (Layout.solo(), Layout.duo())
    reserve = _clp_per_as("1.5")
    desk = AuctionDesk(InProcessBundleEngine(), holds_enabled=swap_holds)
    book, stock, auctions = market.book, market.stock, market.auctions
    # Rule-based auto-bidding (opt-in per campaign from /advertiser).
    autobidder = market.autobidder
    autobid_running = False
    autobid_lock = market.autobid_lock
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
            # (edge/agent/vision/vocabulary.py); at catalog weight a single cup or
            # bottle stays below the enter threshold and only the house ad plays.
            (SegmentSource.VISION, "bottle", fitness),  # the gym-bottle prop
            (SegmentSource.VISION, "cup", coffee),
            (SegmentSource.VISION, "wine glass", beer),
            # YOLOE-26 store labels (edge/agent/vision/vocabulary.py) COCO lacked.
            (SegmentSource.VISION, "energy drink can", fitness),  # a Voltio/Rayo can
            (SegmentSource.VISION, "beer bottle", beer),
        )
    )
    # The market's catalog may read a demo cue as another segment (demo_catalog.Catalog).
    cue_segments = {
        label: SegmentId.create(segment) for label, segment in market.catalog.cue_segments
    }
    fixture_cues = tuple(
        replace(rule, segment_id=cue_segments.get(rule.cue, rule.segment_id))
        for rule in fixture_cues
    )
    fixture_keys = {(rule.source, rule.cue) for rule in fixture_cues}
    audience_catalog = loaded.catalog.with_cues(
        (
            *(rule for rule in loaded.catalog.cues if (rule.source, rule.cue) not in fixture_keys),
            *fixture_cues,
        )
    ).with_products(
        # The market's own brands, so a held prop ("product:<slug>") has a category.
        ProductRef(category=brand.product, brand_ref=brand.slug)
        for brand in market.catalog.brands
        if brand.product is not None
    )
    segment_labels = {segment.segment_id: segment.label for segment in audience_catalog.segments}
    # Segment matching beyond exact ids (auction affinity.py): the IAB hierarchy from
    # the catalog paths plus its curated same-need pairs. A static in-memory table,
    # so the hot path stays a lookup.
    affinity = SegmentAffinity.build(
        paths={segment.segment_id: segment.path for segment in audience_catalog.segments},
        related=(
            RelatedSegments(left=item.left, right=item.right, weight=item.weight.to_ratio())
            for item in audience_catalog.relations
        ),
    )
    # Canonical keyterms the agent reports (edge/agent/speech/keyterms.py) -> segments.
    keyterm_catalog = {
        "gym": fitness,
        "energía": fitness,
        "café": coffee,
        "hambre": food,
        "comida": food,
        "cerveza": beer,
    }
    keyterm_catalog = {term: cue_segments.get(term, seg) for term, seg in keyterm_catalog.items()}
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
            SegmentId.create(demo_catalog.PASTRY): Angle.HUNGER,
            SegmentId.create(demo_catalog.SOFT_DRINKS): Angle.HUNGER,
            SegmentId.create(demo_catalog.SNACKS): Angle.HUNGER,
            SegmentId.create(demo_catalog.SALTY_SNACKS): Angle.HUNGER,
            SegmentId.create(demo_catalog.WATER): Angle.POST_WORKOUT,
            SegmentId.create(demo_catalog.ENERGY_DRINKS): Angle.ENERGY,
            SegmentId.create(demo_catalog.CANDY): Angle.HUNGER,
        },
    )
    studio = CreativeStudio(
        clock=clock,
        writer=copywriter,
        budget=GenerationBudget(hourly_cap=UsdCost.create(hourly_budget_usd)),
        config=StudioConfig(copy_timeout_s=copy_timeout_s),
    )
    # Which approved stock hook airs for a brief, learned from attention and QR scans.
    hook_lab = HookLab()
    house_spec = compose_template(
        "solo",
        (
            BrandKit(
                brand_id=BrandId.create(ids.new_uuid7()),
                name="team18",
                logo_url=market.house_logo_url,
                stock_copy="team18 · Acércate. Hay una oferta para tu grupo.",
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
    # Photo rotation (CampaignMedia.alternates): a campaign keeps its photo while it
    # stays on air and shows the next one each time it comes back. Per screen, in memory.
    airings: dict[str, int] = {}
    last_media_key: str | None = None
    current_brief: CreativeBrief | None = None
    latest_observed_at: UtcTimestamp | None = None
    latest_console: tuple[ConsoleMessage, ...] = ()
    # Classified speech topics only; utterance text is never kept (ADR 0005).
    speech = SpeechEvidence()
    speech_revision = 0  # order utterances independently of slow warm-provider completion
    speech_request_id: str | None = None
    speech_request_base = SpeechEvidence()
    playback_echo = PlaybackEcho()
    voice_creative_id: str | None = None
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

    # ------------------------------------------------------------ /dev QA stream
    # Debug views only (docs/dev-console-plan.md): every emitter is fire-and-forget
    # and does nothing unless a `/dev` page is connected.

    def dev_tags(report: TagReport) -> None:
        if hub.dev_count():
            hub.broadcast_dev_threadsafe(
                dev_stream.vlm(report, finished_at=clock.now_utc(), labels=segment_labels)
            )

    def dev_creative(step: str, creative: CreativeReady, **fields: object) -> None:
        if not hub.dev_count():
            return
        spec = creative.spec
        hub.broadcast_dev_threadsafe(
            DevCreative.model_validate(
                {
                    "screen_id": wire_screen_id,
                    "at": clock.now_utc(),
                    "step": step,
                    "creative_id": creative.creative_id,
                    "auction_id": creative.auction_id,
                    "voiceover_url": spec.voiceover_url,
                    "video_url": spec.video_url,
                    "redemption_code": spec.redemption_code,
                    **fields,
                }
            )
        )

    def dev_player(event: str, creative_id: str, at: datetime, **fields: object) -> None:
        if hub.dev_count():
            hub.broadcast_dev_threadsafe(
                DevPlayer.model_validate(
                    {
                        "screen_id": wire_screen_id,
                        "creative_id": creative_id,
                        "event": event,
                        "at": at,
                        **fields,
                    }
                )
            )

    classifier = AudienceClassifier(
        catalog=audience_catalog,
        reliability=loaded.reliability,
        tagger=tagger,
        warm=warm,
        on_findings=vlm_findings_landed,
        on_degraded=vlm_degradation,
        on_tags=dev_tags,
    )
    # The on-air play's exposure lease: (creative_id, result, looking, leases held).
    lease: tuple[str, AuctionResult, int, int] | None = None
    # Recent looking counts: the offer addresses the group, not every glance (party.py).
    looking_samples: deque[LookingSample] = deque()
    briefed_party = 0  # the addressed party the current moment was briefed with

    def receive_snapshot(snapshot: AudienceSnapshot, received: Received) -> None:
        decide(snapshot, received, reconsider=False)

    def decide(snapshot: AudienceSnapshot, received: Received, *, reconsider: bool) -> None:
        nonlocal current_creative, current_moment, latest_observed_at, latest_console
        nonlocal last_snapshot, briefed_party
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
                looking_samples.append(
                    LookingSample(measured_at.value, snapshot.looking, snapshot.people)
                )
                while looking_samples and (
                    measured_at.value - looking_samples[0].measured_at > GROUP_WINDOW
                ):
                    looking_samples.popleft()
                delivery.attention.record(
                    wire_screen_id,
                    measured_at.value,
                    (tracklet.id for tracklet in snapshot.tracklets if tracklet.looking),
                    far=(
                        tracklet.id
                        for tracklet in snapshot.tracklets
                        if tracklet.looking and tracklet.distance_band.value == "far"
                    ),
                )
                looks = [t.dwell_s for t in snapshot.tracklets if t.looking]
                hub.send_to_screen_threadsafe(
                    ScreenAttention(
                        screen_id=wire_screen_id,
                        creative_id=current_creative.creative_id,
                        looking=len(looks) if snapshot.tracklets else snapshot.looking,
                        dwell_s=max(looks, default=0.0),
                        distance_band=nearest_band(snapshot.tracklets),
                        measured_at=measured_at.value,
                    )
                )
            classification = classifier.classify(snapshot, at=received.received_at)
            segments = group_default.apply(
                segments=merge_speech(
                    classification.segments,
                    speech,
                    at=UtcTimestamp.create(received.received_at),
                    looking=snapshot.looking,
                ),
                looking=snapshot.looking,
            )
            minor_likely = classification.minor_likely
            held = classification.held_products
            # The group the offer addresses moves only on confirmed counts (party.py), so
            # the second sample of a new count re-briefs even when the facts repeat.
            # Only while someone looks: with nobody looking the window drains 3 → 0, and
            # re-auctioning on each step would re-arm the walk-away grace forever.
            party_now = (
                addressed_party(looking_samples, now=measured_at.value) if snapshot.looking else 0
            )
            if current_moment is not None and (
                snapshot.people,
                snapshot.looking,
                segments,
                minor_likely,
                held,
                party_now,
            ) == (
                current_moment.people,
                current_moment.looking,
                current_moment.segments,
                current_moment.minor_likely,
                current_moment.held_products,
                briefed_party,
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
            handled_moment, handled_party = current_moment, briefed_party
            briefed_party = party_now
            try:
                needs_voice = run_auction(snapshot, moment, Trigger.MOMENT_CHANGED)
            except Exception:
                # Not aired: forget this audience so the next identical reading retries
                # it, instead of being skipped as "unchanged" while the old ad sticks.
                current_moment, briefed_party = handled_moment, handled_party
                raise
        # Outside the lock: a cache hit upgrades the creative, which takes the lock.
        if needs_voice is not None:
            request_voiceover(needs_voice)

    def brief_for(moment: AudienceMomentSnapshot, result: AuctionResult) -> CreativeBrief | None:
        held = (
            frozenset(
                brand_by_slug[product.brand_ref]
                for product in moment.held_products
                if product.brand_ref is not None and product.brand_ref in brand_by_slug
            )
            if len(result.winners) > 1
            else frozenset()
        )
        local_hour = moment.measured_at.value.astimezone(VENUE_TZ).hour
        return build_brief(
            moment=moment,
            result=result,
            catalog=catalog,
            context=MomentContext(
                held_brand_ids=held,
                daypart=daypart_for_hour(local_hour),
                party=addressed_party(looking_samples, now=moment.measured_at.value),
            ),
        )

    def run_auction(
        snapshot: AudienceSnapshot,
        moment: AudienceMomentSnapshot,
        trigger: Trigger,
        *,
        out_of_budget: frozenset[CampaignId] = frozenset(),
        triggered_at: datetime | None = None,
    ) -> CreativeReady | None:
        """Clear `moment` against the live book and stock. Caller holds the lock.

        `triggered_at` is when an operator change (bid, stock, budget) reached us:
        a re-auction is timed from its cause, not from the moment's old receipt.
        """
        nonlocal current_moment, latest_console

        def needs_creative_refresh(result: AuctionResult) -> bool:
            new_brief = brief_for(moment, result)
            return (
                new_brief is not None
                and current_brief is not None
                and (
                    current_brief.offer != new_brief.offer
                    or current_brief.angle.angle != new_brief.angle.angle
                    or (
                        new_brief.offer is not None
                        and current_brief.addressed_party != new_brief.addressed_party
                    )
                    or current_brief.held_brand != new_brief.held_brand
                )
            )

        # The on-air play's own hold doesn't count against a re-auction it is part of.
        bidders = book.snapshot(excluding_hold=current_creative.creative_id)
        if out_of_budget:
            bidders = tuple(
                replace(b, budget_remaining=Money.zero(currency="CLP"))
                if b.campaign_id in out_of_budget
                else b
                for b in bidders
            )
        # Context-aware bids: the catalog's daypart table, at the venue's local hour
        # (empty for the pinned fictional catalog, so its auctions are unchanged).
        daypart = daypart_for_hour(moment.measured_at.value.astimezone(VENUE_TZ).hour)
        boosts = tuple(
            CategoryMultiplier(category=category, multiplier=multiplier)
            for category, multiplier in market.catalog.boosts_for(daypart.value)
        )
        outcome = desk.run(
            AdOpportunity(
                moment=moment,
                bidders=bidders,
                layouts=layouts,
                reserve=reserve,
                category_multipliers=boosts,
                approved_baskets=offer_book.pairings(),
                out_of_stock=stock.out_of_stock(wire_screen_id),
                max_play_seconds=Decimal(MAX_PLAY_SECONDS),
                affinity=affinity,
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
            needs_creative_refresh=needs_creative_refresh,
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
        observe_for_autobid(outcome.result)
        trace = LatencyTrace(
            perception=snapshot.perception,
            candidate_at=snapshot.candidate_at,
            detected_at=snapshot.detected_at,
            published_at=snapshot.published_at,
            measured_at=moment.measured_at.value,
            received_at=triggered_at or moment.received_at.value,
            moment_changed_at=triggered_at or moment.moment_changed_at.value,
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
        if outcome.hold_token is not None and outcome.not_before is not None:
            preview_next(decision, outcome.not_before)  # after its decision, in order
        return None

    def reauction(trigger: Trigger, *, out_of_budget: frozenset[CampaignId] = frozenset()) -> None:
        """Re-clear the moment on screen after a bid, stock or budget change (same audience)."""
        triggered_at = clock.now_utc()
        with lock:
            snapshot = last_snapshot
            moment = current_moment
            needs_voice = (
                run_auction(
                    snapshot[0],
                    moment,
                    trigger,
                    out_of_budget=out_of_budget,
                    triggered_at=triggered_at,
                )
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
            offer=current_creative.spec.offer
            if creative_id is not None or swap == "unchanged"
            else None,
        )

    def publish(decision: _Decision, *, swap: str) -> CreativeReady | None:
        """Compose and push the decision's creative. Caller holds the lock.

        Hot path: brief → cached AI or targeted stock copy, no network. A warm AI-copy
        upgrade is queued (non-blocking) when this brief has none cached yet.
        """
        nonlocal current_creative, latest_console, current_brief, last_media_key
        result = decision.result
        spec = house_spec
        brief = brief_for(decision.moment, result)
        composed: Composed | None = None
        variant = 0
        if brief is not None:
            variant = hook_lab.variant_for(brief.signature(), studio.hook_count(brief))
            composed = studio.compose(brief, variant=variant)
            if composed.score.verdict.passed:
                spec = with_cached_voice(render_spec(brief, composed.draft, kits_by_id))
            else:
                # Even stock copy failed the guard (e.g. a kit line unsafe for this
                # moment): the house ad airs rather than an unsafe line.
                brief = None
        current_brief = brief
        creative_id = str(ids.new_uuid7())
        if brief is not None and composed is not None:
            hook_lab.aired(creative_id, brief.signature(), variant, composed.draft.teaser)
        # Paid only if the winners' own creative airs: a house ad the guard forced is a
        # no-fill on screen, so no brand is billed, held or credited for it.
        paid = bool(result.winners) and spec is not house_spec
        if paid:
            spec = with_basket_extras(
                spec, result, decision.auction_id, creative_id, clock.now_utc(), brief=brief
            )
        else:
            last_media_key = None  # the house ad aired: the next campaign is a new airing
        # Includes redemption, QR encoding and loop lookup in the decision budget.
        ready_at = clock.now_utc()
        trace = decision.trace.model_copy(update={"ready_at": ready_at})
        current_creative = CreativeReady(
            screen_id=wire_screen_id,
            creative_id=creative_id,
            auction_id=decision.auction_id if paid else None,
            spec=spec,
            trace=trace,
        )
        # The player shows it after preload; ready_at is our best server-side stand-in.
        desk.aired(result, displayed_at=ready_at)
        # The play it replaces stops billing here, whatever its report later claims.
        delivery.off_air(wire_screen_id, at=ready_at)
        if paid:
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
        # The station console mirrors what airs (its "Al aire" preview).
        hub.broadcast_station_threadsafe(current_creative)
        hub.send_to_screen_threadsafe(current_creative)
        if hub.dev_count():
            note = None
            if not result.winners:
                note = f"no fill: {result.no_fill.value}" if result.no_fill else "no fill"
            elif composed is not None and brief is None:
                note = "even stock copy failed the guard: house ad"
            dev_creative(
                "composed" if brief is not None else "house",
                current_creative,
                moment_id=str(decision.moment.moment_id),
                brief=dev_stream.brief(composed.brief) if composed is not None else None,
                copy_draft=dev_stream.copy(composed.draft, composed.score)
                if composed is not None
                else None,
                compose_ms=composed.compose_ms if composed is not None else None,
                note=note,
            )
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

        def expire_later() -> None:
            # The play's report settles its hold; if that report never comes (player
            # closed, lost), the hold expires after the TTL instead of leaking.
            scheduler(RESERVATION_TTL.total_seconds() + 1, lambda: book.expire(at=clock.now_utc()))

        def renew() -> None:
            with lock:
                if current_creative.creative_id != creative_id:
                    expire_later()  # replaced
                    return
                short = book.extend_hold(
                    creative_id, lease_holds(result, looking, lease + 1), at=clock.now_utc()
                )
                if not short:
                    set_lease(creative_id, result, looking, lease + 1)
                    schedule_lease(creative_id, result, looking, lease + 1)
            if short:
                expire_later()  # its last lease: the play comes down now
                reauction(Trigger.BUDGET_CHANGED, out_of_budget=short)

        scheduler(float(MAX_PLAY_SECONDS), renew)

    def preview_next(decision: _Decision, not_before: datetime) -> None:
        """Station consoles see the held creative before it airs. Caller holds the lock.

        Composed like publish() would (memoized, no network) but without its side
        effects: no redemption code, QR, voice or budget hold until it really airs.
        """
        if not hub.has_station_consoles():
            return
        result = decision.result
        brief = brief_for(decision.moment, result) if result.winners else None
        spec = house_spec
        if brief is not None:
            variant = hook_lab.variant_for(brief.signature(), studio.hook_count(brief))
            composed = studio.compose(brief, variant=variant)
            if composed.score.verdict.passed:
                spec = render_spec(brief, composed.draft, kits_by_id)
        hub.broadcast_station_threadsafe(
            ConsoleNext(
                screen_id=wire_screen_id,
                auction_id=decision.auction_id
                if result.winners and spec is not house_spec
                else None,
                spec=spec,
                not_before=not_before,
            )
        )

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
                    autobid=autobidder.enabled(bidder.campaign_id),
                    autobid_rule=(
                        d.rule.value
                        if (d := autobidder.last_decision(bidder.campaign_id))
                        else None
                    ),
                    autobid_reason=d.reason if d else None,
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
            autobidder.disable(campaign_id)  # a human took the slider: the human wins
            reauction(Trigger.BID_CHANGED)
        return outcome

    # ------------------------------------------------------------ auto-bidding (rules)

    def observe_for_autobid(result: AuctionResult) -> None:
        values = {bid.campaign_id: bid.value_per_s.amount for bid in result.bids}
        rival = max((w.value_per_s.amount for w in result.winners), default=Decimal(0))
        for campaign_id, value in values.items():
            exclusion = result.exclusion_for(campaign_id)
            outbid = exclusion is not None and exclusion.reason in (
                ExclusionReason.OUTBID,
                ExclusionReason.COMPETITOR,
            )
            autobidder.observe_auction(
                campaign_id,
                won=campaign_id in result.winner_ids,
                outbid=outbid,
                multiplier_needed=(rival / value) if outbid and value > 0 else None,
            )

    def set_autobid(message: AdvertiserAutoBid) -> bool:
        nonlocal autobid_running
        campaign_id = _campaign_id(message.campaign_id)
        if campaign_id not in campaign_of:
            raise UnknownCampaign()
        if not message.enabled:
            autobidder.disable(campaign_id)
            return False
        target = message.target_cost_per_redemption
        autobidder.enable(
            campaign_id,
            AutoBidGoal(
                target_cost_per_redemption=None
                if target is None
                else Money.create(currency=target.currency, amount=target.amount)
            ),
            at=clock.now_utc(),
        )
        with autobid_lock:  # one loop, however many concurrent enables
            start = not autobid_running
            autobid_running = True
        if start:
            scheduler(AUTOBID_TICK_S, autobid_tick)
        return True

    def autobid_tick() -> None:
        try:
            changed = autobidder.tick(at=clock.now_utc())
            if changed:
                reauction(Trigger.BID_CHANGED)
            else:
                hub.broadcast_console_threadsafe(book_view())
        except Exception:  # a bad tick must never stop auto-bidding for good
            logger.exception("auto-bid tick failed")
        finally:
            scheduler(AUTOBID_TICK_S, autobid_tick)

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

    def extract_topics(utterance: AudienceUtterance, received: Received, revision: int) -> None:
        if topic_extractor is None or warm is None:
            return
        text, language = utterance.text, utterance.language  # in memory only, never logged
        catalog_text = {segment.value: label for segment, label in segment_labels.items()}
        by_key = {segment.value: segment for segment in segment_labels}

        async def job() -> None:
            nonlocal speech
            with lock:
                if revision != speech_revision:
                    return
                prior_topics = tuple(
                    topic.segment_id.value
                    for topic in speech_request_base.active(
                        UtcTimestamp.create(received.received_at)
                    ).topics
                )
            asked_at = clock.now_utc()
            extraction = await topic_extractor.extract(
                text, language=language, catalog=catalog_text, prior_topics=prior_topics
            )

            def dev_extraction(found_topics: tuple[SpeechTopic, ...] = ()) -> None:
                if hub.dev_count():
                    hub.broadcast_dev_threadsafe(
                        DevSpeech(
                            screen_id=wire_screen_id,
                            at=clock.now_utc(),
                            stage="extraction",
                            rejected=[key[:32] for key in extraction.rejected][:32],
                            topics=dev_stream.speech_topics(found_topics, segment_labels),
                            outcome=extraction.outcome.value,
                            model_version=extraction.model_version[:128],
                            latency_ms=max((clock.now_utc() - asked_at).total_seconds() * 1000, 0),
                        )
                    )

            if extraction.outcome is TopicOutcome.FAILED:
                dev_extraction()
                return
            found = tuple(t for t in extraction.topics if t.segment_key not in extraction.rejected)
            # A model cannot reintroduce a topic the shopper explicitly rejected.
            denied = {
                keyterm_catalog[term].value
                for term in DEFAULT_KEYTERMS.understand(text).rejected
                if term in keyterm_catalog
            }
            found = tuple(
                topic
                for topic in found
                if topic.segment_key in by_key and topic.segment_key not in denied
            )
            heard = UtcTimestamp.create(received.received_at)
            expires = UtcTimestamp.create(received.received_at + speech_ttl)
            topics = tuple(
                SpeechTopic(
                    segment_id=by_key[topic.segment_key],
                    intent=Percentage.create(topic.intent * 100),
                    heard_at=heard,
                    expires_at=expires,
                    model_version=extraction.model_version,
                    confidence=Percentage.create(
                        min(topic.confidence, extraction.confidence) * 100
                    ),
                    reasoning_trace=extraction.outcome.value,
                )
                for topic in found
            )
            dev_extraction(topics)
            with lock:
                if revision != speech_revision or clock.now_utc() >= expires.value:
                    return
                snapshot = last_snapshot
                base = speech_request_base
                rejected = frozenset(
                    by_key[k] for k in (*extraction.rejected, *denied) if k in by_key
                )
                # Successful semantics supersede this request's provisional hits.
                # Failure retains them. No raw utterance survives classification.
                speech = base.without(rejected)
                solo = snapshot is not None and snapshot[0].looking <= 1
                if extraction.outcome is TopicOutcome.CANCEL:
                    speech = SpeechEvidence() if solo else speech
                elif extraction.outcome is TopicOutcome.REQUEST:
                    if topics:
                        speech = speech.heard(topics, replace_previous=solo)
                    elif solo:
                        speech = SpeechEvidence()  # understood need, unavailable inventory
            if snapshot is not None:
                decide(snapshot[0], Received(received_at=clock.now_utc()), reconsider=True)

        # Agent sequence numbers restart on reconnect; the runtime revision does not.
        warm.submit_threadsafe("claude", f"topics:{utterance.screen_id}:{revision}", job)

    # ------------------------------------------------------------ QR, redemption, video

    # Photo rotation (CampaignMedia.alternates) for airing_of, declared with the screen
    # state publish() updates.
    def airing_of(found: tuple[str, CampaignMedia] | None) -> int:
        nonlocal last_media_key
        if found is None:
            return 0
        key = found[0]
        with lock:
            if key != last_media_key:
                airings[key] = airings.get(key, -1) + 1
                last_media_key = key
            return airings[key]

    def with_basket_extras(
        spec: CreativeSpec,
        result: AuctionResult,
        auction_id: str,
        creative_id: str,
        at: datetime,
        *,
        brief: CreativeBrief | None,
    ) -> CreativeSpec:
        """Add the basket's redemption code + QR and its pre-generated loop (if any)."""
        brands = [winner.bidder.brand_id for winner in result.winners]
        found = media_for(
            market.campaign_media,
            tuple(
                (slug_of[winner.bidder.brand_id], winner.bidder.category)
                for winner in result.winners
            ),
        )
        spec = apply_campaign_media(spec, found[1] if found else None, airing=airing_of(found))
        if brief is None or brief.offer is None:
            # No approved offer, nothing to redeem: never show a QR that sells nothing.
            if loops is not None:
                spec = spec.model_copy(
                    update={
                        "video_url": loops.url_for([slug_of[brand] for brand in brands])
                        or spec.video_url
                    }
                )
            return spec
        offer = redemptions.issue(
            auction_id=auction_id,
            creative_id=creative_id,
            screen_id=wire_screen_id,
            campaign_ids=[winner.campaign_id for winner in result.winners],
            brand_names=[kits_by_id[brand].name for brand in brands],
            offer_text=(
                brief.offer.headline if brief is not None and brief.offer is not None else None
            ),
            at=at,
        )
        redeem_url = f"{public_base_url.rstrip('/')}/r/{offer.code}"
        update: dict[str, object] = {
            "redemption_code": offer.code,
            "redeem_url": redeem_url,
            "qr_url": qr_data_url(redeem_url),
        }
        if loops is not None:
            update["video_url"] = (
                loops.url_for([slug_of[brand] for brand in brands]) or spec.video_url
            )
        return spec.model_copy(update=update)

    def redeem(code: str, nonce: str) -> tuple[ConsoleRedemption, bool]:
        redemption, created = redemptions.redeem(code, nonce, at=clock.now_utc())
        if created:
            for campaign in redemption.campaign_ids:
                autobidder.observe_redemption(campaign)
        offer = redemptions.offer(code)
        if created and offer is not None:
            hook_lab.observe_scan(offer.creative_id)
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
            if target.trace.displayed_at is not None:
                return  # reconnects and retries cannot restart a play's display hold
            displayed_at = min(displayed_at, received.received_at)
            if target.trace.ready_at is not None:
                displayed_at = max(displayed_at, target.trace.ready_at)
            trace = target.trace.model_copy(update={"displayed_at": displayed_at})
            current_creative = target.model_copy(update={"trace": trace})
            desk.displayed(displayed_at)  # minimum ad seconds count from what people saw
        hub.broadcast_console_threadsafe(
            ConsoleLatency(screen_id=wire_screen_id, creative_id=report.creative_id, trace=trace)
        )
        dev_player("displayed", report.creative_id, displayed_at)

    # ------------------------------------------------------------ delivery (billing)

    def receive_playback(report: PlaybackReport, received: Received) -> None:
        if report.screen_id != wire_screen_id:
            return
        dev_player(
            "playback",
            report.creative_id,
            report.ended_at,
            played_s=(report.ended_at - report.started_at).total_seconds(),
        )
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
        hook_lab.observe_play(report.creative_id, receipt.attention.attention_seconds)
        # The charge was booked (and capped) against budgets inside billing.
        for charge in receipt.charges:
            autobidder.observe_play(
                charge.campaign_id,
                charge=charge.amount,
                attention_seconds=charge.attention_seconds,
            )
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
            filtered=[
                FilteredAttention(reason=reason.value, seconds=format(seconds, "f"))
                for reason, seconds in receipt.attention.invalid_seconds.items()
                if seconds > 0
            ],
        )

    # ------------------------------------------------------------ speech (opt-in screens)

    def receive_voice(report: PlayerVoice, received: Received) -> None:
        nonlocal voice_creative_id
        if report.screen_id == wire_screen_id:
            dev_player(
                "voice", report.creative_id, received.received_at, voice_playing=report.playing
            )
        with lock:
            if not report.playing and report.creative_id == voice_creative_id:
                playback_echo.stop(received.received_at)
                voice_creative_id = None
                return
            if report.screen_id != wire_screen_id or current_creative is None:
                return
            if report.creative_id != current_creative.creative_id:
                return
            if report.playing:
                voice_creative_id = report.creative_id
                playback_echo.start(
                    spoken_copy(current_creative.spec),
                    received.received_at,
                    timedelta(milliseconds=report.duration_ms),
                )

    def receive_utterance(utterance: AudienceUtterance, received: Received) -> None:
        nonlocal speech, speech_revision, speech_request_id, speech_request_base
        if utterance.screen_id != wire_screen_id or wire_screen_id not in speech_screens:
            return  # speech mode is off for this screen: drop it, unread
        with lock:
            if playback_echo.matches(utterance.text, received.received_at):
                if hub.dev_count():
                    hub.broadcast_dev_threadsafe(
                        DevSpeech(
                            screen_id=wire_screen_id,
                            at=received.received_at,
                            stage="echo",
                            is_final=utterance.is_final,
                        )
                    )
                return
            speech_revision += 1
            revision = speech_revision
            correcting = (
                utterance.utterance_id is not None and utterance.utterance_id == speech_request_id
            )
            if correcting:
                # Reinterpret a partial from the state before this utterance. A
                # corrected partial may remove a topic, even in a multi-person group.
                speech = speech_request_base
            else:
                speech_request_id = utterance.utterance_id
                speech_request_base = speech
        meaning = DEFAULT_KEYTERMS.understand(utterance.text)
        # Canonical hits support old devices and replay. Explicit rejection in the
        # ephemeral text always overrides a stale or incorrect edge hit.
        wanted = tuple(dict.fromkeys((*utterance.keyterms_hit, *meaning.wanted)))
        wanted = tuple(term for term in wanted if term not in (*meaning.rejected, *meaning.ignored))
        topics = topics_from_keyterms(
            wanted,
            keyterm_catalog,
            heard_at=UtcTimestamp.create(received.received_at),
            ttl=speech_ttl,
        )
        with lock:
            snapshot = last_snapshot
            # Rejections retract speech evidence, never visual observations. We
            # cannot identify speakers: this is an anonymous group request.
            rejected = frozenset(
                keyterm_catalog[t] for t in meaning.rejected if t in keyterm_catalog
            )
            speech = speech.without(rejected)
            if topics:
                speech = speech.heard(
                    topics, replace_previous=snapshot is not None and snapshot[0].looking <= 1
                )
            elif wanted and snapshot is not None and snapshot[0].looking <= 1:
                # Known shopping need with no available campaign (e.g. water)
                # supersedes the previous spoken need without inventing inventory.
                speech = SpeechEvidence()
        if hub.dev_count():
            # Canonical keyterms and topics only: the utterance text never leaves here.
            hub.broadcast_dev_threadsafe(
                DevSpeech(
                    screen_id=wire_screen_id,
                    at=received.received_at,
                    stage="keyterms",
                    is_final=utterance.is_final,
                    wanted=[term[:32] for term in wanted][:32],
                    rejected=[term[:32] for term in meaning.rejected][:32],
                    topics=dev_stream.speech_topics(topics, segment_labels),
                )
            )
        if snapshot is not None and (topics or meaning.rejected or wanted or correcting):
            decide(snapshot[0], Received(received_at=received.received_at), reconsider=True)
        # Refine the whole final request even when one known keyword matched.
        # Partial text stays entirely on the instant path to cap provider usage.
        if utterance.is_final and utterance.text.strip():
            extract_topics(utterance, received, revision)

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
            if hub.dev_count():
                record = dev_stream.last_record(studio.stats.records, job.brief.signature())
                with lock:
                    on_air = current_creative
                dev_creative(
                    "copy_attempt",
                    on_air,
                    brief=dev_stream.brief(job.brief),
                    copy_draft=dev_stream.copy(draft) if draft is not None else None,
                    **(dev_stream.record_fields(record) if record is not None else {}),
                )
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
            on_air = current_creative
        hub.send_to_screen_threadsafe(upgraded)
        hub.broadcast_console_threadsafe(upgraded)
        dev_creative("copy_upgraded", on_air, copy_draft=dev_stream.copy(draft))
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
        # Demo baskets only: solo offer lines are synthesized on first use, then cached,
        # so a restart never spends voice credits on lines no moment has asked for.
        for basket in sorted(offer_book.pairings(), key=lambda b: sorted(map(str, b))):
            for tier in offer_book.tiers(basket):
                line = offer_line(tier.text, str(tier.price.amount))
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

        def deliver_voice(url: str | None) -> None:
            nonlocal voice_degraded
            if url is not None:
                upgrade_voice(None, line, voice, url)
            elif voiceovers.stats.disabled_by is not None and not voice_degraded:
                voice_degraded = True
                hub.broadcast_console_threadsafe(
                    ConsoleDegradation(
                        screen_id=wire_screen_id, flags=[DegradationFlag.VOICE_FALLBACK]
                    )
                )

        async def job() -> None:
            # A queued line may no longer belong to the current ad. Reuse by line
            # and voice (not creative id) when the same offer returns before admission.
            with lock:
                if (
                    spoken_copy(current_creative.spec) != line
                    or voice_for(current_creative.spec) != voice
                ):
                    return
            if voiceovers.subscribe_pending(
                line, voice, consumer=wire_screen_id, on_ready=deliver_voice
            ):
                return  # Shared synthesis owns the provider slot; don't reserve another.
            characters = voiceovers.stats.characters
            url = await voiceovers.voiceover_url(line, voice)
            if (spent := voiceovers.stats.characters - characters) > 0:
                studio.record_spend(GenerationKind.VOICE, studio.cost_model.voice(spent))
            deliver_voice(url)

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
            on_air = current_creative
        hub.send_to_screen_threadsafe(upgraded)
        hub.broadcast_console_threadsafe(upgraded)
        dev_creative("voice_upgraded", on_air, note="cached" if creative_id else "synthesized")

    def player_connected(connected_screen_id: str, sink: Sink) -> None:
        if connected_screen_id == wire_screen_id:
            with lock:
                hub.send_to_screen_threadsafe(current_creative, sink=sink)

    def console_connected(sink: Sink) -> None:
        with lock:
            # The latest decision first (consumers read it in order), then the book.
            if latest_console:
                hub.broadcast_console_threadsafe(*latest_console, book_view(), sink=sink)
            if hub.is_station_console(sink):
                # What airs now, and the book even before the first decision: a station
                # in standby still shows its brands, bids and the house ad.
                extras = (current_creative,) if latest_console else (current_creative, book_view())
                hub.broadcast_station_threadsafe(*extras, sink=sink)

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
            if dev_keyframe_preview and hub.dev_count():
                # Opt-in, local-only: the same face-blurred JPEG the VLM gets, in memory.
                hub.broadcast_dev_threadsafe(
                    DevKeyframe(
                        screen_id=wire_screen_id,
                        measured_at=keyframe.measured_at,
                        jpeg_base64=b64encode(received.payload).decode("ascii"),
                    )
                )

    def reset_speech() -> tuple[AudienceSnapshot, Received] | None:
        """Forget every spoken topic; a later warm extraction for them is discarded."""
        nonlocal speech, speech_revision, speech_request_id, speech_request_base
        with lock:
            speech = SpeechEvidence()
            speech_revision += 1
            speech_request_id = None
            speech_request_base = SpeechEvidence()
            return last_snapshot

    def forget_speech() -> None:
        if (latest := reset_speech()) is not None:
            decide(latest[0], Received(received_at=clock.now_utc()), reconsider=True)

    def standby() -> None:
        """The live switch went off: nobody is observed, so air what an empty room gets.

        The normal decision path does the work (house ad, the play's attention ends,
        swap holds still apply), so standby can't charge or skip a receipt.
        """
        latest = reset_speech()
        with lock:
            floor = latest_observed_at
        now = clock.now_utc()
        if floor is not None and now <= floor.value:
            # Decisions only move forward: standby must land after the last reading.
            now = floor.value + timedelta(microseconds=1)
        empty = AudienceSnapshot(
            screen_id=wire_screen_id,
            seq=latest[0].seq if latest is not None else 0,
            measured_at=now,
            people=0,
            looking=0,
            minor_likely=MinorLikely(value=False, confidence=0.0),
        )
        decide(empty, Received(received_at=now), reconsider=False)

    router.on(AudienceSnapshot, receive_snapshot)
    router.on(AudienceKeyframe, receive_keyframe)
    router.on(AudienceUtterance, receive_utterance)
    router.on(PlaybackReport, receive_playback)
    router.on_player_connected(player_connected)
    router.on_console_connected(console_connected)
    router.on(PlayerDisplayed, receive_displayed)
    router.on(PlayerVoice, receive_voice)
    return DemoControls(
        book_view=book_view,
        set_multiplier=set_multiplier,
        set_stock=set_stock,
        offer_for=redemptions.offer,
        redeem=redeem,
        reauction_bids=lambda: reauction(Trigger.BID_CHANGED),
        standby=standby,
        forget_speech=forget_speech,
        hook_results=hook_lab.results,
        set_autobid=set_autobid,
    )


AUTOBID_TICK_S = 5.0  # how often the auto-bidding rules re-evaluate
_CEILING = RoundingPolicy.create(money_rounding=ROUND_CEILING)
# The first market is Chilean convenience stores (ADR 0008): hooks follow Santiago's clock.
VENUE_TZ = ZoneInfo("America/Santiago")


_NEAREST_FIRST = (DistanceBand.NEAR, DistanceBand.MID, DistanceBand.FAR)


def nearest_band(tracklets: Sequence[Tracklet]) -> DistanceBand | None:
    """The nearest looking viewer's distance band; the layout serves whoever can read it."""
    bands = {t.distance_band for t in tracklets if t.looking}
    return next((band for band in _NEAREST_FIRST if band in bands), None)


def _brand_logo(slug: str, name: str, color: str) -> str:
    # An official file (press kit or Wikimedia) wins; else a plain wordmark. Never AI.
    for ext in ("svg", "png", "webp"):
        if (CAMPAIGN_ASSETS / "logos" / f"{slug}.{ext}").is_file():
            return f"/campaign-assets/logos/{slug}.{ext}"
    return wordmark(name, color)


def _slug(name: str) -> str:
    """'Café Patio' → 'cafe-patio', "Lay's" → 'lays' (matches edge/props/markers.json)."""
    import re
    import unicodedata

    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    plain = plain.lower().replace("'", "")
    return "-".join(re.findall(r"[a-z0-9]+", plain))


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
