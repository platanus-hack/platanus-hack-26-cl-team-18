"""What one auction decided, including why every losing bidder lost."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.modules.auction.domain.opportunity import Layout, PricingRule
from app.modules.auction.domain.valuation import SegmentMatch
from app.modules.campaigns.domain.book import Bidder, CampaignId
from app.modules.common.domain.value_objects.money_rate import MoneyRate


class ExclusionReason(StrEnum):
    # Eligibility, checked in this order; the first failure is recorded (R-L1).
    INVALID_BID = "invalid_bid"
    INACTIVE = "inactive"
    SAFETY = "safety"
    OUT_OF_STOCK = "out_of_stock"
    BUDGET = "budget"
    # Lost the clearing (R-O2).
    COMPETITOR = "competitor"
    NOT_PARTNER = "not_partner"
    NO_BASKET_OFFER = "no_basket_offer"
    OUTBID = "outbid"
    BELOW_RESERVE = "below_reserve"


# Reasons that make an ad already on air unsafe or unsellable: swap immediately (D5.1/D5.2).
INELIGIBLE_ON_AIR = frozenset(
    {
        ExclusionReason.SAFETY,
        ExclusionReason.OUT_OF_STOCK,
        ExclusionReason.INACTIVE,
        ExclusionReason.BUDGET,
        ExclusionReason.INVALID_BID,
    }
)


class NoFillReason(StrEnum):
    NO_AUDIENCE = "no_audience"
    NO_ELIGIBLE_BIDS = "no_eligible_bids"
    BELOW_RESERVE = "below_reserve"
    ENGINE_ERROR = "engine_error"  # the engine failed and what was on air became unsafe


@dataclass(frozen=True, slots=True, kw_only=True)
class Exclusion:
    campaign_id: CampaignId
    reason: ExclusionReason
    # The winner it conflicts with (competitor / not a partner / no basket offer).
    related_campaign_id: CampaignId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BidValue:
    """A bidder's relevance-weighted value for this moment, money per screen-second."""

    campaign_id: CampaignId
    value_per_s: MoneyRate
    # How each reached moment segment matched (exact, broader, narrower, related).
    matches: tuple[SegmentMatch, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Winner:
    bidder: Bidder
    slot: int
    share: Decimal
    efficiency: Decimal
    value_per_s: MoneyRate  # own value in this bundle (slot value + its basket bonuses)
    payment_per_s: MoneyRate  # what it pays per second of play
    price_per_as: MoneyRate  # clearing price per attention-second = payment / looking

    @property
    def campaign_id(self) -> CampaignId:
        return self.bidder.campaign_id


@dataclass(frozen=True, slots=True, kw_only=True)
class BundleOption:
    """A basket the auction weighed, best first: who, and what it was worth."""

    campaign_ids: tuple[CampaignId, ...]  # slot order
    value: MoneyRate  # per screen-second


@dataclass(frozen=True, slots=True, kw_only=True)
class AuctionResult:
    pricing: PricingRule
    layout: Layout | None
    winners: tuple[Winner, ...]
    bids: tuple[BidValue, ...]
    exclusions: tuple[Exclusion, ...]
    bundle_value: MoneyRate  # per screen-second
    bundle_revenue: MoneyRate  # per screen-second
    single_brand_value: MoneyRate
    single_brand_revenue: MoneyRate
    coverage: Decimal  # 0..1, upper-bound estimate (R-O4)
    no_fill: NoFillReason | None
    incumbent_value: MoneyRate | None = None  # best value of the on-air set, if still feasible
    # The best baskets considered (the winner first), each set once: the console's
    # "Canastas evaluadas". Explainability only; never used to bill.
    alternatives: tuple[BundleOption, ...] = ()

    @classmethod
    def house_ad(cls, *, currency: MoneyRate, reason: NoFillReason) -> AuctionResult:
        """A no-fill with no bids: the safe fallback when no auction can be trusted."""
        zero = MoneyRate.zero(currency.currency)
        return cls(
            pricing=PricingRule.CORE_ADJUSTED,
            layout=None,
            winners=(),
            bids=(),
            exclusions=(),
            bundle_value=zero,
            bundle_revenue=zero,
            single_brand_value=zero,
            single_brand_revenue=zero,
            coverage=Decimal(0),
            no_fill=reason,
        )

    @property
    def winner_ids(self) -> frozenset[CampaignId]:
        return frozenset(winner.campaign_id for winner in self.winners)

    @property
    def filled(self) -> bool:
        return self.layout is not None

    def exclusion_for(self, campaign_id: CampaignId) -> Exclusion | None:
        return next((item for item in self.exclusions if item.campaign_id == campaign_id), None)
