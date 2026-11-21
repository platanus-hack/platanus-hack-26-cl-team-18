"""Compose the local simulator's snapshot-to-creative flow."""

from base64 import b64encode
from datetime import UTC, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock

from app.bootstrap.demo_console import decision_messages
from app.contract.messages import AudienceSnapshot, CreativeReady, LatencyTrace
from app.modules.auction.domain.bundle import AdOpportunity, Layout, clear_bundle
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.application.fixture_tagging import classify_snapshot
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import BrandId, BrandKit, CampaignBid, CampaignId
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.creative.application.template import compose_template
from app.modules.screens.domain.value_objects.ids import ScreenId
from app.realtime.hub import ConnectionHub, ConsoleMessage, Sink
from app.realtime.router import InboundRouter, Received

DEMO_SCREEN = "demo-01"
MAX_MEASUREMENT_SKEW = timedelta(seconds=10)


def register_demo(
    *,
    router: InboundRouter,
    hub: ConnectionHub,
    clock: BaseClock,
    ids: IdGenerator,
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
    }
    co_brand_partners: tuple[frozenset[BrandId], ...] = (
        frozenset({kits[1].brand_id}),
        frozenset({kits[0].brand_id, kits[3].brand_id}),
        frozenset(),
        frozenset({kits[1].brand_id}),
    )
    campaigns = tuple(
        CampaignBid(
            campaign_id=CampaignId.create(ids.new_uuid7()),
            brand_id=kit.brand_id,
            category=category,
            age_restricted=age_restricted,
            segment_id=segment,
            bid_per_as=Money.create(currency="CLP", amount=amount),
            co_brand_with=partners,
        )
        for kit, (category, age_restricted, segment, amount), partners in zip(
            kits,
            (
                ("sports_goods", False, fitness, 100),
                ("coffee", False, coffee, 100),
                ("alcohol", True, beer, 150),
                ("sports_goods", False, fitness, 80),
            ),
            co_brand_partners,
            strict=True,
        )
    )
    layouts = (
        Layout(name="solo", shares=(Decimal(1),)),
        Layout(name="duo", shares=(Decimal("0.5"), Decimal("0.5"))),
    )
    reserve = Money.create(currency="CLP", amount=10)
    affinity = Money.create(currency="CLP", amount=20)
    vision_catalog = {"sports ball": fitness, "tap handle fixture": beer}
    sound_catalog = {"coffee service fixture": coffee}
    segment_labels = {fitness: "Sports & fitness", coffee: "Coffee", beer: "Beer"}
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

    def receive_snapshot(snapshot: AudienceSnapshot, received: Received) -> None:
        nonlocal current_creative, current_moment, latest_observed_at, latest_console
        if snapshot.screen_id != DEMO_SCREEN:
            return
        measured_at = UtcTimestamp.create(snapshot.measured_at.astimezone(UTC))
        if abs(received.received_at - measured_at.value) > MAX_MEASUREMENT_SKEW:
            return

        # Preserve the device instant in the trace and use receipt as the upper
        # bound for ordering so an ahead clock cannot block subsequent samples.
        observed_at = UtcTimestamp.create(min(measured_at.value, received.received_at))
        with lock:
            if latest_observed_at is not None and observed_at.value <= latest_observed_at.value:
                return
            segments = classify_snapshot(
                snapshot,
                vision_catalog=vision_catalog,
                sound_catalog=sound_catalog,
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
                latest_observed_at = observed_at
                return
            moment = AudienceMomentSnapshot.create(
                screen_id=screen_id,
                moment_id=MomentId.create(ids.new_uuid7()),
                measured_at=measured_at,
                received_at=UtcTimestamp.create(received.received_at),
                moment_changed_at=UtcTimestamp.create(clock.now_utc()),
                people=snapshot.people,
                looking=snapshot.looking,
                segments=segments,
                minor_likely=minor_likely,
            )
            result = clear_bundle(
                AdOpportunity(
                    moment=moment,
                    campaigns=campaigns,
                    layouts=layouts,
                    reserve=reserve,
                    pair_affinity=affinity,
                )
            )
            cleared_at = clock.now_utc()
            spec = house_spec
            auction_id = str(ids.new_uuid7())
            if result.layout is not None:
                winning_kits = tuple(kits_by_id[winner.bid.brand_id] for winner in result.winners)
                spec = compose_template(
                    result.layout.name,
                    winning_kits,
                    approved_offer=approved_offers.get(
                        frozenset(kit.brand_id for kit in winning_kits)
                    ),
                )
            current_creative = CreativeReady(
                screen_id=DEMO_SCREEN,
                creative_id=str(ids.new_uuid7()),
                auction_id=auction_id if result.winners else None,
                spec=spec,
                trace=LatencyTrace(
                    measured_at=moment.measured_at.value,
                    received_at=moment.received_at.value,
                    moment_changed_at=moment.moment_changed_at.value,
                    cleared_at=cleared_at,
                    ready_at=clock.now_utc(),
                ),
            )
            latest_console = decision_messages(
                snapshot=snapshot,
                moment=moment,
                campaigns=campaigns,
                result=result,
                kits=kits_by_id,
                segment_labels=segment_labels,
                auction_id=auction_id,
                creative=current_creative,
            )
            current_moment = moment
            latest_observed_at = observed_at
            hub.broadcast_console_threadsafe(*latest_console)
            hub.send_to_screen_threadsafe(current_creative)

    def player_connected(wire_screen_id: str, sink: Sink) -> None:
        if wire_screen_id == DEMO_SCREEN:
            with lock:
                hub.send_to_screen_threadsafe(current_creative, sink=sink)

    def console_connected(sink: Sink) -> None:
        with lock:
            if latest_console:
                hub.broadcast_console_threadsafe(*latest_console, sink=sink)

    router.on(AudienceSnapshot, receive_snapshot)
    router.on_player_connected(player_connected)
    router.on_console_connected(console_connected)
