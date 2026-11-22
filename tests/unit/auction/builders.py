"""Small builders so auction tests read as tables of facts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.modules.auction.domain.affinity import SegmentAffinity
from app.modules.auction.domain.opportunity import EXACT_ONLY, AdOpportunity, Layout
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.domain.value_objects.ids import MomentId
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.campaigns.domain.book import (
    BasketBonus,
    Bidder,
    BidMultiplier,
    BrandId,
    CampaignId,
    SegmentBid,
)
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.screens.domain.value_objects.ids import ScreenId

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
FITNESS = SegmentId.create("DEMO_FITNESS")
COFFEE = SegmentId.create("DEMO_COFFEE")
BEER = SegmentId.create("DEMO_BEER")


def uuid(namespace: int, index: int) -> UUID:
    return UUID(f"018f0000-0000-7000-8000-{namespace:04d}{index:08d}")


def brand(index: int) -> BrandId:
    return BrandId.create(uuid(1, index))


def campaign(index: int) -> CampaignId:
    return CampaignId.create(uuid(2, index))


def rate(amount: str | int, currency: str = "CLP") -> MoneyRate:
    return MoneyRate.create(currency=currency, amount=amount)


def segment(
    segment_id: SegmentId,
    viewers: int = 1,
    confidence: int | str = 100,
    *,
    speech_intent: int | None = None,
) -> SegmentEstimate:
    sources = {SegmentSource.VISION}
    if speech_intent is not None:
        sources.add(SegmentSource.SPEECH)
    return SegmentEstimate.create(
        segment_id=segment_id,
        viewer_count=viewers,
        confidence=Percentage.create(confidence),
        sources=frozenset(sources),
        speech_intent=None if speech_intent is None else Percentage.create(speech_intent),
    )


def moment(
    *segments: SegmentEstimate,
    looking: int | None = None,
    people: int | None = None,
    minor_likely: bool = False,
) -> AudienceMomentSnapshot:
    seen = max((item.viewer_count for item in segments), default=0)
    looking = seen if looking is None else looking
    instant = UtcTimestamp.create(T0)
    return AudienceMomentSnapshot.create(
        screen_id=ScreenId.create(uuid(3, 0)),
        moment_id=MomentId.create(uuid(4, 0)),
        measured_at=instant,
        received_at=instant,
        moment_changed_at=instant,
        people=looking if people is None else people,
        looking=looking,
        segments=segments,
        minor_likely=minor_likely,
    )


def bidder(
    index: int,
    category: str,
    bids: dict[SegmentId, str | int],
    *,
    partners: tuple[int, ...] = (),
    bonuses: dict[int, str] | None = None,
    age_restricted: bool = False,
    multiplier: str = "1",
    active: bool = True,
    budget: int | None = None,
    currency: str = "CLP",
    broad_match: bool = True,
) -> Bidder:
    return Bidder(
        campaign_id=campaign(index),
        brand_id=brand(index),
        category=category,
        age_restricted=age_restricted,
        segment_bids=tuple(
            SegmentBid(segment_id=segment_id, rate=rate(amount, currency))
            for segment_id, amount in bids.items()
        ),
        partners=frozenset(brand(partner) for partner in partners),
        basket_bonuses=tuple(
            BasketBonus(partner=brand(partner), rate=rate(amount, currency))
            for partner, amount in (bonuses or {}).items()
        ),
        live_multiplier=BidMultiplier.create(Decimal(multiplier)),
        active=active,
        budget_remaining=None if budget is None else Money.create(currency=currency, amount=budget),
        broad_match=broad_match,
    )


def basket(*indexes: int) -> frozenset[BrandId]:
    return frozenset(brand(index) for index in indexes)


def opportunity(
    audience: AudienceMomentSnapshot,
    *bidders: Bidder,
    reserve: str = "1.5",
    baskets: tuple[frozenset[BrandId], ...] = (),
    layouts: tuple[Layout, ...] = (Layout.solo(), Layout.duo()),
    out_of_stock: tuple[int, ...] = (),
    affinity: SegmentAffinity = EXACT_ONLY,
) -> AdOpportunity:
    return AdOpportunity(
        moment=audience,
        bidders=bidders,
        layouts=layouts,
        reserve=rate(reserve),
        approved_baskets=frozenset(baskets),
        out_of_stock=frozenset(brand(index) for index in out_of_stock),
        affinity=affinity,
    )


# The seeded demo book (app/bootstrap/demo.py): Voltio=0, Café Patio=1, Malta Sur=2, Rayo=3.
VOLTIO, CAFE, MALTA, RAYO = 0, 1, 2, 3


def demo_bidders(*, rayo_multiplier: str = "1") -> tuple[Bidder, ...]:
    return (
        bidder(VOLTIO, "sports_goods", {FITNESS: "8"}, partners=(CAFE,)),
        bidder(CAFE, "coffee", {COFFEE: "5"}, partners=(VOLTIO, RAYO)),
        bidder(MALTA, "alcohol", {BEER: "12"}, age_restricted=True),
        bidder(
            RAYO, "sports_goods", {FITNESS: "6.5"}, partners=(CAFE,), multiplier=rayo_multiplier
        ),
    )


DEMO_BASKETS = (basket(VOLTIO, CAFE), basket(RAYO, CAFE))
