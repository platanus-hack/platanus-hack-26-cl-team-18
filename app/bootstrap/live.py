"""A live market with independent per-screen audience, auction and creative state."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Literal

from app.bootstrap.demo import (
    DEMO_SCREEN,
    DemoControls,
    LiveMarket,
    _slug,
    register_demo,
    seed_market,
)
from app.bootstrap.demo_catalog import catalog_named
from app.contract.messages import (
    AdvertiserBidMultiplier,
    AdvertiserBudgetTopUp,
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    ConsoleBook,
    LiveStatus,
    PlaybackReport,
    PlayerDisplayed,
    PlayerVoice,
    StockSet,
)
from app.modules.campaigns.application.live_book import MultiplierOutcome, TopUpOutcome
from app.modules.campaigns.application.ports.registry import CampaignRegistry
from app.modules.creative.application.campaigns import CampaignGenerator
from app.modules.creative.application.loops import bundle_key
from app.realtime.hub import Sink
from app.realtime.router import Inbound, InboundRouter, Received
from app.realtime.warm_worker import GatedWarm

if TYPE_CHECKING:
    from app.bootstrap.container import Container

logger = logging.getLogger(__name__)

# The wire's screen id (app/contract/messages.py ScreenId): a short slug.
SCREEN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
Admission = Literal["ok", "unauthorized", "full", "invalid"]


@dataclass(frozen=True, slots=True)
class ScreenRuntime:
    router: InboundRouter
    controls: DemoControls


def register_live_market(container: Container) -> DemoControls:
    """Use the approved seed catalog in every environment; simulator controls stay separate."""
    # Imported here to avoid a composition-root cycle.
    from datetime import timedelta

    from app.modules.common.application.id_generator import IdGenerator

    settings = container.settings
    ids = IdGenerator()

    def market_with(registry: CampaignRegistry | None) -> LiveMarket:
        return seed_market(
            ids=ids,
            budget_clp=settings.demo_budget_clp,
            auction_log=container.auction_log,
            ledger=container.ledger,
            redemption_ledger=container.redemption_ledger,
            registry=registry,
            catalog=catalog_named(settings.demo_catalog),
        )

    try:
        market = market_with(container.campaign_registry)
    except Exception:
        # The screen is never blank: a database down at boot means fresh in-memory
        # campaigns for this process (spend and bids from before are not restored).
        logger.exception("Campaign registry unavailable: campaigns start in memory")
        market = market_with(None)
        container.campaign_store = "memory_fallback"  # /health and the preflight show it
    slugs = {kit.brand_id: _slug(kit.name) for kit in market.kits}
    container.campaign_generator = CampaignGenerator(
        kits=market.kits,
        bidders=market.bidders,
        offers=market.offers,
        slugs=slugs,
        media=market.campaign_media,
        clock=container.clock,
    )
    container.loop_keys = tuple(
        sorted(
            {bundle_key([slug]) for slug in slugs.values()}
            | {bundle_key([slugs[brand] for brand in basket]) for basket in market.offers.baskets()}
        )
    )
    screens: dict[str, ScreenRuntime] = {}
    registry_lock = RLock()
    capacity = settings.max_screen_runtimes
    refused = 0

    def create(screen_id: str) -> ScreenRuntime | None:
        """A new runtime, or None past the cap (a live runtime is never evicted)."""
        nonlocal refused
        if not SCREEN_ID.fullmatch(screen_id):
            return None
        if len(screens) >= capacity and screen_id != DEMO_SCREEN:
            refused += 1
            if refused == 1 or refused % 1000 == 0:
                logger.warning(
                    "screen runtimes at capacity (%d): refused a new screen (%d so far)",
                    capacity,
                    refused,
                )
            return None
        router = InboundRouter()
        controls = register_demo(
            wire_screen_id=screen_id,
            market=market,
            router=router,
            hub=container.hub,
            clock=container.clock,
            ids=ids,
            warm=GatedWarm(
                container.warm,
                lambda: container.live.ai_generation(screen_id),
            ),
            voiceovers=container.voiceovers,
            narrator_voice_id=settings.elevenlabs_narrator_voice_id,
            speech_screens=frozenset(settings.speech_mode_screens),
            speech_ttl=timedelta(seconds=settings.speech_topic_ttl_s),
            swap_holds=settings.swap_holds_enabled,
            scheduler=container.scheduler,
            tagger=container.tagger,
            topic_extractor=container.topic_extractor,
            public_base_url=settings.public_base_url,
            media_dir=settings.media_dir,
            dev_keyframe_preview=settings.dev_keyframe_preview_enabled,
            copywriter=container.copywriter,
            copy_timeout_s=settings.creative_copy_timeout_s,
            hourly_budget_usd=settings.creative_hourly_budget_usd,
            startup=container.startup if settings.voiceover_prewarm and not screens else None,
        )
        runtime = screens[screen_id] = ScreenRuntime(router, controls)
        return runtime

    def screen(screen_id: str) -> ScreenRuntime | None:
        """The screen's runtime, created on first use while under the cap."""
        with registry_lock:
            return screens.get(screen_id) or create(screen_id)

    def admit(screen_id: str, may_create: bool) -> Admission:
        """May a player socket attach to `screen_id`? Creates the runtime when allowed."""
        with registry_lock:
            if screen_id in screens:
                return "ok"
            if not SCREEN_ID.fullmatch(screen_id):
                return "invalid"
            if not may_create:
                return "unauthorized"
            return "ok" if create(screen_id) is not None else "full"

    def runtimes() -> tuple[ScreenRuntime, ...]:
        with registry_lock:
            return tuple(screens.values())

    def dispatch(message: Inbound, received: Received) -> None:
        runtime = screen(message.screen_id)
        if runtime is not None:  # past the cap: no runtime, the reading goes nowhere
            runtime.router.dispatch_sync(message, received)

    def player_connected(screen_id: str, sink: Sink) -> None:
        runtime = screen(screen_id)
        if runtime is not None:
            runtime.router.player_connected_sync(screen_id, sink)

    def console_connected(sink: Sink) -> None:
        for runtime in runtimes():
            runtime.router.console_connected_sync(sink)

    def live_changed(before: LiveStatus, after: LiveStatus) -> None:
        # Sensing off: the screen airs what an empty room gets. Mic off while still
        # live: spoken topics are forgotten at once instead of riding out their TTL.
        runtime = screen(after.screen_id)
        if runtime is None:
            return
        controls = runtime.controls
        if before.sensing and not after.sensing:
            controls.standby()
        elif before.listening and not after.listening:
            controls.forget_speech()

    def set_multiplier(message: AdvertiserBidMultiplier) -> MultiplierOutcome:
        # Each runtime must reconsider its own moment, including duplicate retries
        # of the shared update. The per-screen callback below handles that trigger.
        outcome = default.controls.set_multiplier(message)
        if outcome.value == "applied":
            for runtime in runtimes():
                if runtime is not default:
                    runtime.controls.reauction_bids()
        return outcome

    def top_up(message: AdvertiserBudgetTopUp) -> TopUpOutcome:
        # One shared book: every other screen reconsiders its moment too.
        if default.controls.top_up is None:
            raise RuntimeError("the default screen has no budget control")
        outcome = default.controls.top_up(message)
        if outcome is TopUpOutcome.APPLIED:
            for runtime in runtimes():
                if runtime is not default:
                    runtime.controls.reauction_bids()
        return outcome

    def set_stock(message: StockSet) -> bool:
        runtime = screen(message.screen_id)
        return False if runtime is None else runtime.controls.set_stock(message)

    def book_for(screen_id: str) -> ConsoleBook:
        runtime = screen(screen_id) or default
        return runtime.controls.book_view()

    first = screen(DEMO_SCREEN)  # the default screen is always admitted
    if first is None:
        raise RuntimeError("the default screen could not start")
    default = first
    container.admit_screen = admit
    for message_type in (
        AudienceSnapshot,
        AudienceKeyframe,
        AudienceUtterance,
        PlaybackReport,
        PlayerDisplayed,
        PlayerVoice,
    ):
        container.router.on(message_type, dispatch)
    container.router.on_player_connected(player_connected)
    container.router.on_console_connected(console_connected)
    container.live.on_change(live_changed)
    return DemoControls(
        book_view=default.controls.book_view,
        set_multiplier=set_multiplier,
        set_stock=set_stock,
        offer_for=default.controls.offer_for,
        redeem=default.controls.redeem,
        reauction_bids=default.controls.reauction_bids,
        # One shared book: auto-bid moves apply to every screen; the default screen's
        # auctions and plays are the observations the rules learn from.
        set_autobid=default.controls.set_autobid,
        book_for=book_for,
        profiles=default.controls.profiles,
        top_up=top_up,
        # Each screen learns its own hooks (its room, its light); the API shows them all.
        hook_results=lambda: [
            result for runtime in runtimes() for result in runtime.controls.hook_results()
        ],
    )
