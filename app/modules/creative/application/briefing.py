"""Build a `CreativeBrief` from a cleared auction (pure translation, hot-path safe).

Everything the copy may target is decided here, before any generator runs:
the angle (speech > held > queue > segment), the offer for the party size,
and which brand names the copy must avoid (every known brand outside the basket).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.modules.auction.domain.result import AuctionResult
from app.modules.audience import AudienceMomentSnapshot
from app.modules.audience.domain.value_objects.segment import SegmentEstimate, SegmentId
from app.modules.campaigns.domain.book import BrandId, BrandKit
from app.modules.creative.domain.angle import Angle, choose_angle
from app.modules.creative.domain.brief import BrandBrief, CreativeBrief, Daypart, StageLimits
from app.modules.creative.domain.offer import OfferBook


@dataclass(frozen=True, slots=True, kw_only=True)
class CreativeCatalog:
    """Seed-time facts the brief builder reads (one per venue / demo)."""

    kits: Mapping[BrandId, BrandKit]
    offers: OfferBook
    segment_angles: Mapping[SegmentId, Angle] = field(default_factory=dict)
    limits: StageLimits = field(default_factory=StageLimits)


@dataclass(frozen=True, slots=True, kw_only=True)
class MomentContext:
    """Optional context beyond the moment snapshot (console toggles, held products, queue)."""

    held_brand_ids: frozenset[BrandId] = frozenset()
    queue_length: int = 0
    daypart: Daypart | None = None


def build_brief(
    *,
    moment: AudienceMomentSnapshot,
    result: AuctionResult,
    catalog: CreativeCatalog,
    context: MomentContext = MomentContext(),
) -> CreativeBrief | None:
    """None for a no-fill (the house ad needs no brief)."""
    if result.layout is None or not result.winners:
        return None
    winners = sorted(result.winners, key=lambda w: w.slot)
    return assemble_brief(
        basket=tuple((w.bidder.brand_id, w.bidder.category) for w in winners),
        layout=result.layout.name,
        segments=moment.segments,
        looking=moment.looking,
        people=moment.people,
        minor_likely=moment.minor_likely,
        catalog=catalog,
        context=context,
    )


def assemble_brief(
    *,
    basket: tuple[tuple[BrandId, str], ...],
    layout: str,
    segments: tuple[SegmentEstimate, ...],
    looking: int,
    people: int,
    minor_likely: bool,
    catalog: CreativeCatalog,
    context: MomentContext = MomentContext(),
) -> CreativeBrief:
    """The brief for a basket (brand id, category) in slot order. Evals call this directly."""
    brands = tuple(
        BrandBrief(
            brand_id=brand_id,
            name=catalog.kits[brand_id].name,
            category=category,
            stock_copy=catalog.kits[brand_id].stock_copy,
            tagline=catalog.kits[brand_id].tagline,
            voice=catalog.kits[brand_id].voice,
        )
        for brand_id, category in basket
    )
    basket_ids = frozenset(b.brand_id for b in brands)
    held = next(
        (catalog.kits[b].name for b in sorted(context.held_brand_ids, key=str) if b in basket_ids),
        None,
    )
    angle = choose_angle(
        segments=segments,
        segment_angles=catalog.segment_angles,
        categories=tuple(b.category for b in brands),
        held_in_basket=held is not None,
        queue_length=context.queue_length,
        minor_likely=minor_likely,
    )
    return CreativeBrief(
        brands=brands,
        layout=layout,
        party=looking,
        people=people,
        angle=angle,
        offer=catalog.offers.for_party(basket_ids, looking),
        avoid_names=frozenset(
            kit.name for brand_id, kit in catalog.kits.items() if brand_id not in basket_ids
        ),
        minor_likely=minor_likely,
        held_brand=held,
        daypart=context.daypart,
        limits=catalog.limits,
    )
