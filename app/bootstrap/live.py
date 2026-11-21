"""A live market with independent per-screen audience, auction and creative state."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from app.bootstrap.demo import DEMO_SCREEN, DemoControls, _slug, register_demo, seed_market
from app.contract.messages import (
    AdvertiserBidMultiplier,
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    PlaybackReport,
    PlayerDisplayed,
    StockSet,
)
from app.modules.campaigns.application.live_book import MultiplierOutcome
from app.modules.creative.application.campaigns import CampaignGenerator
from app.modules.creative.application.loops import bundle_key
from app.realtime.hub import Sink
from app.realtime.router import Inbound, InboundRouter, Received

if TYPE_CHECKING:
    from app.bootstrap.container import Container


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
    market = seed_market(
        ids=ids,
        budget_clp=settings.demo_budget_clp,
        auction_log=container.auction_log,
        ledger=container.ledger,
        redemption_ledger=container.redemption_ledger,
    )
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

    def screen(screen_id: str) -> ScreenRuntime:
        with registry_lock:
            if screen_id not in screens:
                router = InboundRouter()
                controls = register_demo(
                    wire_screen_id=screen_id,
                    market=market,
                    router=router,
                    hub=container.hub,
                    clock=container.clock,
                    ids=ids,
                    warm=container.warm,
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
                    copywriter=container.copywriter,
                    copy_timeout_s=settings.creative_copy_timeout_s,
                    hourly_budget_usd=settings.creative_hourly_budget_usd,
                    startup=container.startup
                    if settings.voiceover_prewarm and not screens
                    else None,
                )
                screens[screen_id] = ScreenRuntime(router, controls)
            return screens[screen_id]

    def runtimes() -> tuple[ScreenRuntime, ...]:
        with registry_lock:
            return tuple(screens.values())

    def dispatch(message: Inbound, received: Received) -> None:
        screen(message.screen_id).router.dispatch_sync(message, received)

    def player_connected(screen_id: str, sink: Sink) -> None:
        screen(screen_id).router.player_connected_sync(screen_id, sink)

    def console_connected(sink: Sink) -> None:
        for runtime in runtimes():
            runtime.router.console_connected_sync(sink)

    def set_multiplier(message: AdvertiserBidMultiplier) -> MultiplierOutcome:
        # Each runtime must reconsider its own moment, including duplicate retries
        # of the shared update. The per-screen callback below handles that trigger.
        outcome = default.controls.set_multiplier(message)
        if outcome.value == "applied":
            for runtime in runtimes():
                if runtime is not default:
                    runtime.controls.reauction_bids()
        return outcome

    def set_stock(message: StockSet) -> bool:
        return screen(message.screen_id).controls.set_stock(message)

    default = screen(DEMO_SCREEN)
    for message_type in (
        AudienceSnapshot,
        AudienceKeyframe,
        AudienceUtterance,
        PlaybackReport,
        PlayerDisplayed,
    ):
        container.router.on(message_type, dispatch)
    container.router.on_player_connected(player_connected)
    container.router.on_console_connected(console_connected)
    return DemoControls(
        book_view=default.controls.book_view,
        set_multiplier=set_multiplier,
        set_stock=set_stock,
        offer_for=default.controls.offer_for,
        redeem=default.controls.redeem,
        reauction_bids=default.controls.reauction_bids,
        book_for=lambda screen_id: screen(screen_id).controls.book_view(),
    )
