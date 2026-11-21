"""Compose the local simulator's snapshot-to-creative flow."""

from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

from app.bootstrap.demo_console import decision_messages
from app.contract.messages import (
    AudienceSnapshot,
    AudienceUtterance,
    ConsoleDegradation,
    CreativeReady,
    CreativeSpec,
    CreativeUpgraded,
    DegradationFlag,
    LatencyTrace,
    UpgradeKind,
)
from app.modules.auction.application.desk import AuctionDesk
from app.modules.auction.domain.opportunity import AdOpportunity, Layout
from app.modules.auction.domain.result import AuctionResult
from app.modules.auction.domain.swap import Trigger
from app.modules.auction.infrastructure.engines.in_process import InProcessBundleEngine
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.application.fixture_tagging import classify_snapshot
from app.modules.audience.domain.speech_evidence import (
    SpeechEvidence,
    merge_speech,
    topics_from_keyterms,
)
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import (
    Bidder,
    BrandId,
    BrandKit,
    CampaignId,
    SegmentBid,
)
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.creative.application.template import compose_template
from app.modules.creative.application.voiceover import InvalidSpokenLine, VoiceoverService
from app.modules.screens.domain.value_objects.ids import ScreenId
from app.realtime.hub import ConnectionHub, ConsoleMessage, Sink
from app.realtime.router import InboundRouter, Received
from app.realtime.scheduler import Scheduler, thread_timer
from app.realtime.warm_worker import WarmWorker

DEMO_SCREEN = "demo-01"
MAX_MEASUREMENT_SKEW = timedelta(seconds=10)


def _clp_per_as(amount: str) -> MoneyRate:
    return MoneyRate.create(currency="CLP", amount=amount)


def register_demo(
    *,
    router: InboundRouter,
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
) -> None:
    screen_id = ScreenId.create(ids.new_uuid7())
    # Reuse the existing project mark for these fictional fixture brands.
    logo = Path(__file__).resolve().parents[2].joinpath("project-logo.png").read_bytes()
    logo_url = "data:image/png;base64," + b64encode(logo).decode("ascii")

    fitness = SegmentId.create("DEMO_FITNESS")
    coffee = SegmentId.create("DEMO_COFFEE")
    beer = SegmentId.create("DEMO_BEER")
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
    kits_by_id = {kit.brand_id: kit for kit in kits}
    approved_offers = {
        frozenset(
            {kits[0].brand_id, kits[1].brand_id}
        ): "Combo post-entreno · Voltio + Café Patio por $3.490",
        # Every mutual-partner pair gets an approved basket (docs/demo.md), so Rayo
        # competes for the energy slot inside a basket and prices it.
        frozenset({kits[3].brand_id, kits[1].brand_id}): "Combo · Rayo + Café Patio por $3.290",
    }
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
            segment_bids=(SegmentBid(segment_id=segment, rate=_clp_per_as(rate)),),
            partners=partners,
            budget_remaining=Money.create(currency="CLP", amount=50_000),
        )
        for kit, (category, age_restricted, segment, rate), partners in zip(
            kits,
            (
                ("sports_goods", False, fitness, "8"),
                ("coffee", False, coffee, "5"),
                ("alcohol", True, beer, "12"),
                ("sports_goods", False, fitness, "6.5"),
            ),
            co_brand_partners,
            strict=True,
        )
    )
    layouts = (Layout.solo(), Layout.duo())
    reserve = _clp_per_as("1.5")
    desk = AuctionDesk(InProcessBundleEngine(), holds_enabled=swap_holds)
    vision_catalog = {"sports ball": fitness, "tap handle fixture": beer}
    sound_catalog = {"coffee service fixture": coffee}
    segment_labels = {fitness: "Sports & fitness", coffee: "Coffee", beer: "Beer"}
    # Canonical keyterms the agent reports (edge/agent/speech/keyterms.py) -> segments.
    keyterm_catalog = {
        "gym": fitness,
        "energía": fitness,
        "café": coffee,
        "hambre": coffee,
        "comida": coffee,
        "cerveza": beer,
    }
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
        screen_id=DEMO_SCREEN,
        creative_id=str(ids.new_uuid7()),
        spec=house_spec,
        trace=LatencyTrace(ready_at=clock.now_utc()),
    )
    lock = Lock()
    current_moment: AudienceMomentSnapshot | None = None
    latest_observed_at: UtcTimestamp | None = None
    latest_console: tuple[ConsoleMessage, ...] = ()
    # Classified speech topics only; utterance text is never kept (ADR 0005).
    speech = SpeechEvidence()
    last_snapshot: tuple[AudienceSnapshot, Received] | None = None
    voice_degraded = False

    def receive_snapshot(snapshot: AudienceSnapshot, received: Received) -> None:
        decide(snapshot, received, reconsider=False)

    def decide(snapshot: AudienceSnapshot, received: Received, *, reconsider: bool) -> None:
        nonlocal current_creative, current_moment, latest_observed_at, latest_console
        nonlocal last_snapshot
        if snapshot.screen_id != DEMO_SCREEN:
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
            segments = merge_speech(
                classify_snapshot(
                    snapshot,
                    vision_catalog=vision_catalog,
                    sound_catalog=sound_catalog,
                ),
                speech,
                at=UtcTimestamp.create(received.received_at),
                looking=snapshot.looking,
            )
            minor_likely = snapshot.minor_likely.value
            if current_moment is not None and (
                snapshot.people,
                snapshot.looking,
                segments,
                minor_likely,
            ) == (
                current_moment.people,
                current_moment.looking,
                current_moment.segments,
                current_moment.minor_likely,
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
            )
            outcome = desk.run(
                AdOpportunity(
                    moment=moment,
                    bidders=bidders,
                    layouts=layouts,
                    reserve=reserve,
                    approved_baskets=frozenset(approved_offers),
                ),
                Trigger.MOMENT_CHANGED,
                now=clock.now_utc(),
            )
            cleared_at = clock.now_utc()
            if not reconsider:
                latest_observed_at = observed_at
            if outcome.result is None:
                # Engine failed: the last good creative keeps playing, and the moment
                # stays unrecorded so the next identical snapshot re-auctions it.
                return
            current_moment = moment
            auction_id = str(ids.new_uuid7())
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
            )
            needs_voice = None
            if outcome.swap_now:
                needs_voice = publish(decision, swap="swap")
            else:
                swap = "held" if outcome.hold_token is not None else outcome.action.value
                latest_console = console_for(decision, creative_id=None, swap=swap)
                hub.broadcast_console_threadsafe(*latest_console)
                if outcome.hold_token is not None and outcome.not_before is not None:
                    schedule_release(outcome.hold_token, decision, outcome.not_before)
        # Outside the lock: a cache hit upgrades the creative, which takes the lock.
        if needs_voice is not None:
            request_voiceover(needs_voice)

    def console_for(
        decision: _Decision, *, creative_id: str | None, swap: str
    ) -> tuple[ConsoleMessage, ...]:
        return decision_messages(
            snapshot=decision.snapshot,
            moment=decision.moment,
            bidders=bidders,
            result=decision.result,
            kits=kits_by_id,
            segment_labels=segment_labels,
            auction_id=decision.auction_id,
            trace=decision.trace,
            creative_id=creative_id,
            trigger=Trigger.MOMENT_CHANGED.value,
            swap=swap,
        )

    def publish(decision: _Decision, *, swap: str) -> CreativeReady | None:
        """Compose and push the decision's creative. Caller holds the lock."""
        nonlocal current_creative, latest_console
        result = decision.result
        spec = house_spec
        if result.layout is not None:
            winning_kits = tuple(kits_by_id[winner.bidder.brand_id] for winner in result.winners)
            spec = compose_template(
                result.layout.name,
                winning_kits,
                approved_offer=approved_offers.get(frozenset(kit.brand_id for kit in winning_kits)),
            )
        ready_at = clock.now_utc()
        trace = decision.trace.model_copy(update={"ready_at": ready_at})
        current_creative = CreativeReady(
            screen_id=DEMO_SCREEN,
            creative_id=str(ids.new_uuid7()),
            auction_id=decision.auction_id if result.winners else None,
            spec=spec,
            trace=trace,
        )
        # The player shows it after preload; ready_at is our best server-side stand-in.
        desk.aired(result, displayed_at=ready_at)
        latest_console = console_for(
            _Decision(
                snapshot=decision.snapshot,
                moment=decision.moment,
                result=result,
                auction_id=decision.auction_id,
                trace=trace,
            ),
            creative_id=current_creative.creative_id,
            swap=swap,
        )
        hub.broadcast_console_threadsafe(*latest_console)
        hub.send_to_screen_threadsafe(current_creative)
        return current_creative if result.winners and decision.moment.looking > 0 else None

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

    # ------------------------------------------------------------ speech (opt-in screens)

    def receive_utterance(utterance: AudienceUtterance, received: Received) -> None:
        nonlocal speech
        if utterance.screen_id != DEMO_SCREEN or DEMO_SCREEN not in speech_screens:
            return  # speech mode is off for this screen: drop it, unread
        topics = topics_from_keyterms(
            utterance.keyterms_hit,
            keyterm_catalog,
            heard_at=UtcTimestamp.create(received.received_at),
            ttl=speech_ttl,
        )
        if not topics:
            return  # no keyterm: a TopicExtractor (LLM) would refine here, warm path
        with lock:
            speech = speech.heard(topics)
            snapshot = last_snapshot
        if snapshot is not None:
            # Re-run the decision on the latest observation with speech merged in.
            decide(snapshot[0], Received(received_at=received.received_at), reconsider=True)

    # ------------------------------------------------------------ voice-overs (warm)

    def spoken_copy(spec: CreativeSpec) -> str:
        return spec.slots[0].copy_text

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
            url = await voiceovers.voiceover_url(line, voice)
            if url is not None:
                upgrade_voice(None, line, voice, url)
            elif voiceovers.stats.disabled_by is not None and not voice_degraded:
                voice_degraded = True
                hub.broadcast_console_threadsafe(
                    ConsoleDegradation(
                        screen_id=DEMO_SCREEN, flags=[DegradationFlag.VOICE_FALLBACK]
                    )
                )

        warm.submit_threadsafe("elevenlabs", key, job)

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
                screen_id=DEMO_SCREEN,
                creative_id=target.creative_id,
                auction_id=target.auction_id,
                upgrade_kind=UpgradeKind.VOICE,
                spec=spec,
                trace=trace,
            )
        hub.send_to_screen_threadsafe(upgraded)
        hub.broadcast_console_threadsafe(upgraded)

    def player_connected(wire_screen_id: str, sink: Sink) -> None:
        if wire_screen_id == DEMO_SCREEN:
            with lock:
                hub.send_to_screen_threadsafe(current_creative, sink=sink)

    def console_connected(sink: Sink) -> None:
        with lock:
            if latest_console:
                hub.broadcast_console_threadsafe(*latest_console, sink=sink)

    router.on(AudienceSnapshot, receive_snapshot)
    router.on(AudienceUtterance, receive_utterance)
    router.on_player_connected(player_connected)
    router.on_console_connected(console_connected)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Decision:
    """One cleared auction and the facts it was cleared against (simulator wiring only)."""

    snapshot: AudienceSnapshot
    moment: AudienceMomentSnapshot
    result: AuctionResult
    auction_id: str
    trace: LatencyTrace
