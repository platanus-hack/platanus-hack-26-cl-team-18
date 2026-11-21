"""Eligibility and compatibility rules. Pure Specifications/Policies: no IO, no repositories."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import ExclusionReason
from app.modules.campaigns.domain.book import Bidder
from app.modules.common.domain.primitives.policy import Policy
from app.modules.common.domain.primitives.specification import Specification


@dataclass(frozen=True, slots=True, kw_only=True)
class BidderInMoment:
    """One bidder evaluated against one opportunity (the candidate for eligibility specs)."""

    bidder: Bidder
    opportunity: AdOpportunity
    ceiling_rate: Decimal  # max chargeable per AS: bid × live multiplier + basket bonuses


class ValidBid(Specification[BidderInMoment]):
    def is_satisfied_by(self, candidate: BidderInMoment) -> bool:
        currency = candidate.opportunity.reserve.currency
        bidder = candidate.bidder
        rates = [bid.rate for bid in bidder.segment_bids] + [
            bonus.rate for bonus in bidder.basket_bonuses
        ]
        budget_ok = bidder.budget_remaining is None or bidder.budget_remaining.currency == currency
        return budget_ok and all(rate.currency == currency for rate in rates)


class Active(Specification[BidderInMoment]):
    def is_satisfied_by(self, candidate: BidderInMoment) -> bool:
        return candidate.bidder.active


class NoAgeRestrictedCategoryWhenMinorLikely(Specification[BidderInMoment]):
    """Invariant 2: `minor_likely` only ever blocks; it never changes another bid."""

    def is_satisfied_by(self, candidate: BidderInMoment) -> bool:
        return not (candidate.opportunity.moment.minor_likely and candidate.bidder.age_restricted)


class InStockAtScreen(Specification[BidderInMoment]):
    def is_satisfied_by(self, candidate: BidderInMoment) -> bool:
        return candidate.bidder.brand_id not in candidate.opportunity.out_of_stock


class BudgetCovers(Specification[BidderInMoment]):
    """Budget left covers the worst-case exposure of one play (R-B6)."""

    def is_satisfied_by(self, candidate: BidderInMoment) -> bool:
        budget = candidate.bidder.budget_remaining
        if budget is None:
            return True
        opportunity = candidate.opportunity
        exposure = (
            candidate.ceiling_rate * opportunity.moment.looking * opportunity.max_play_seconds
        )
        return budget.amount >= exposure


ELIGIBILITY: tuple[tuple[ExclusionReason, Specification[BidderInMoment]], ...] = (
    (ExclusionReason.INVALID_BID, ValidBid()),
    (ExclusionReason.INACTIVE, Active()),
    (ExclusionReason.SAFETY, NoAgeRestrictedCategoryWhenMinorLikely()),
    (ExclusionReason.OUT_OF_STOCK, InStockAtScreen()),
    (ExclusionReason.BUDGET, BudgetCovers()),
)


class FirstEligibilityFailure(Policy[BidderInMoment, ExclusionReason | None]):
    """The first failing eligibility reason, in the documented order, or None."""

    def apply(self, input: BidderInMoment) -> ExclusionReason | None:
        return next(
            (reason for reason, spec in ELIGIBILITY if not spec.is_satisfied_by(input)), None
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Basket:
    """A candidate set of bidders for one opportunity (the candidate for compatibility specs)."""

    bidders: tuple[Bidder, ...]
    opportunity: AdOpportunity


class DistinctBrands(Specification[Basket]):
    def is_satisfied_by(self, candidate: Basket) -> bool:
        return len({bidder.brand_id for bidder in candidate.bidders}) == len(candidate.bidders)


class NoSharedCategory(Specification[Basket]):
    """Competitor exclusion is per brand category."""

    def is_satisfied_by(self, candidate: Basket) -> bool:
        return len({bidder.category for bidder in candidate.bidders}) == len(candidate.bidders)


class MutualPartners(Specification[Basket]):
    """Pre-approved partners only: every pair is on *both* allow-lists."""

    def is_satisfied_by(self, candidate: Basket) -> bool:
        return all(
            left.brand_id in right.partners and right.brand_id in left.partners
            for left, right in combinations(candidate.bidders, 2)
        )


class ApprovedBasket(Specification[Basket]):
    """The pairing has an approved basket offer and loop."""

    def is_satisfied_by(self, candidate: Basket) -> bool:
        return candidate.opportunity.basket_approved(
            frozenset(bidder.brand_id for bidder in candidate.bidders)
        )


COMPATIBLE: Specification[Basket] = (
    DistinctBrands() & NoSharedCategory() & MutualPartners() & ApprovedBasket()
)
