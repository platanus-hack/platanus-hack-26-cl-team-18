"""Deterministic bundle clearing over supplied campaign and layout facts."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from itertools import combinations

from app.modules.audience import AudienceMomentSnapshot
from app.modules.campaigns.domain.book import CampaignBid, CampaignId
from app.modules.common.domain.primitives.specification import Specification
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.rounding_policy import RoundingPolicy

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    name: str
    shares: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.shares) <= 3 or any(
            not share.is_finite() or share <= ZERO for share in self.shares
        ):
            raise ValueError("A layout requires one to three positive slot shares")
        if sum(self.shares, ZERO) != 1:
            raise ValueError("Layout slot shares must add up to one")
        if tuple(sorted(self.shares, reverse=True)) != self.shares:
            raise ValueError("Layout slot shares must be in descending order")


@dataclass(frozen=True, slots=True, kw_only=True)
class AdOpportunity:
    moment: AudienceMomentSnapshot
    campaigns: tuple[CampaignBid, ...]
    layouts: tuple[Layout, ...]
    reserve: Money
    pair_affinity: Money


@dataclass(frozen=True, slots=True, kw_only=True)
class Winner:
    bid: CampaignBid
    price_per_as: Money


class ExclusionReason(StrEnum):
    COMPETITOR = "competitor"
    SAFETY = "safety"
    BELOW_RESERVE = "below_reserve"


@dataclass(frozen=True, slots=True, kw_only=True)
class Exclusion:
    campaign_id: CampaignId
    reason: ExclusionReason


@dataclass(frozen=True, slots=True, kw_only=True)
class AuctionResult:
    layout: Layout | None
    winners: tuple[Winner, ...]
    bundle_value: Money
    single_brand_value: Money
    exclusions: tuple[Exclusion, ...]


class CompetitorExclusion(Specification[tuple[CampaignBid, ...]]):
    def is_satisfied_by(self, bundle: tuple[CampaignBid, ...]) -> bool:
        return len({bid.category for bid in bundle}) == len(bundle) and len(
            {bid.brand_id for bid in bundle}
        ) == len(bundle)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Bundle:
    layout: Layout
    bids: tuple[CampaignBid, ...]
    slot_values: tuple[Decimal, ...]
    value: Decimal

    def excludes(self, campaign_id: CampaignId) -> bool:
        return all(bid.campaign_id != campaign_id for bid in self.bids)


def _compatible(bids: tuple[CampaignBid, ...]) -> bool:
    return CompetitorExclusion().is_satisfied_by(bids) and all(
        left.brand_id in right.co_brand_with and right.brand_id in left.co_brand_with
        for left, right in combinations(bids, 2)
    )


def clear_bundle(opportunity: AdOpportunity) -> AuctionResult:
    """Enumerate supported bundles of the top eight eligible campaigns."""
    moment = opportunity.moment
    reserve = opportunity.reserve
    amounts = (opportunity.pair_affinity, *(bid.bid_per_as for bid in opportunity.campaigns))
    if any(money.currency != reserve.currency or money.amount < ZERO for money in amounts):
        raise ValueError("Auction bids and affinity must be nonnegative in the reserve currency")
    if reserve.amount < ZERO:
        raise ValueError("Auction reserve must be nonnegative")

    segments = {segment.segment_id: segment for segment in moment.segments}
    ranked: list[tuple[CampaignBid, Decimal]] = []
    exclusions: list[Exclusion] = []
    for bid in sorted(opportunity.campaigns, key=lambda campaign: campaign.campaign_id.value):
        segment = segments.get(bid.segment_id)
        if segment is None:
            continue
        if moment.minor_likely and bid.age_restricted:
            exclusions.append(Exclusion(campaign_id=bid.campaign_id, reason=ExclusionReason.SAFETY))
            continue
        value = bid.bid_per_as.amount * segment.viewer_count * segment.confidence.value / 100
        if value > ZERO:
            ranked.append((bid, value))
    ranked.sort(key=lambda entry: (-entry[1], entry[0].campaign_id.value))

    candidates: list[_Bundle] = []
    for layout in sorted(opportunity.layouts, key=lambda layout: layout.name):
        for entries in combinations(ranked[:8], len(layout.shares)):
            bids = tuple(bid for bid, _ in entries)
            if not _compatible(bids):
                continue
            slot_values = tuple(
                value * share for (_, value), share in zip(entries, layout.shares, strict=True)
            )
            pair_count = len(bids) * (len(bids) - 1) // 2
            value = sum(slot_values, ZERO) + opportunity.pair_affinity.amount * pair_count
            candidates.append(
                _Bundle(layout=layout, bids=bids, slot_values=slot_values, value=value)
            )

    def as_money(amount: Decimal) -> Money:
        return Money.create(
            currency=reserve.currency,
            amount=amount,
            rounded=True,
            rounding_policy=RoundingPolicy.create(money_rounding=ROUND_HALF_EVEN),
        )

    solo = max((item.value for item in candidates if len(item.bids) == 1), default=ZERO)
    candidates.sort(
        key=lambda item: (-item.value, tuple(bid.campaign_id.value for bid in item.bids))
    )
    if not candidates or candidates[0].value < reserve.amount:
        below_reserve = {bid.campaign_id for candidate in candidates for bid in candidate.bids}
        exclusions.extend(
            Exclusion(campaign_id=campaign_id, reason=ExclusionReason.BELOW_RESERVE)
            for campaign_id in sorted(below_reserve, key=lambda campaign_id: campaign_id.value)
        )
        return AuctionResult(
            layout=None,
            winners=(),
            bundle_value=as_money(ZERO),
            single_brand_value=as_money(solo),
            exclusions=tuple(exclusions),
        )

    best = candidates[0]
    winner_categories = {bid.category for bid in best.bids}
    exclusions.extend(
        Exclusion(campaign_id=bid.campaign_id, reason=ExclusionReason.COMPETITOR)
        for bid, _ in ranked
        if best.excludes(bid.campaign_id) and bid.category in winner_categories
    )
    winners = []
    for bid, slot_value in zip(best.bids, best.slot_values, strict=True):
        without_bid = next(
            (item.value for item in candidates if item.excludes(bid.campaign_id)), ZERO
        )
        price = max(reserve.amount, without_bid - (best.value - slot_value))
        winners.append(Winner(bid=bid, price_per_as=as_money(price)))
    return AuctionResult(
        layout=best.layout,
        winners=tuple(winners),
        bundle_value=as_money(best.value),
        single_brand_value=as_money(solo),
        exclusions=tuple(exclusions),
    )
