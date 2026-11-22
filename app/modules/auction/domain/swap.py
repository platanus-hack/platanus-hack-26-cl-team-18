"""When may a new auction result replace what is on screen? (docs/auction-plan.md §9, D5/D8).

The auction always clears and the console always sees the result. This
policy only decides whether, and from when, the *screen* swaps. Pure: the
caller passes `now`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import INELIGIBLE_ON_AIR, AuctionResult, NoFillReason
from app.modules.campaigns.domain.book import BrandId, CampaignId
from app.modules.common.domain.primitives.policy import Policy


class Trigger(StrEnum):
    MOMENT_CHANGED = "moment_changed"
    BID_CHANGED = "bid_changed"
    STOCK_CHANGED = "stock_changed"
    CONDITIONS_CHANGED = "conditions_changed"
    HELD_PRODUCTS_CHANGED = "held_products_changed"
    BUDGET_CHANGED = "budget_changed"


# Re-auctions of the *same* audience: these get hysteresis and the longer hold.
SAME_MOMENT_TRIGGERS = frozenset(
    {
        Trigger.BID_CHANGED,
        Trigger.STOCK_CHANGED,
        Trigger.CONDITIONS_CHANGED,
        Trigger.HELD_PRODUCTS_CHANGED,
        Trigger.BUDGET_CHANGED,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SwapTimings:
    """Minimum ad seconds, measured from when the on-air creative was displayed."""

    new_group: timedelta = timedelta(seconds=1.5)  # D5.3
    same_moment: timedelta = timedelta(seconds=3.0)  # D5.4, one full teaser stage
    leave_grace: timedelta = timedelta(seconds=3.0)  # D5.5, people glance away
    # D5.5: nobody looking but the group still in front (talking, reading a phone).
    present_grace: timedelta = timedelta(seconds=6.0)
    hysteresis: Decimal = Decimal("0.02")  # D8, challenger must beat incumbent by 2 %


@dataclass(frozen=True, slots=True, kw_only=True)
class OnAir:
    """What the screen is showing. Empty `campaign_ids` means the house ad."""

    campaign_ids: frozenset[CampaignId]
    layout_name: str | None
    displayed_at: datetime
    brand_ids: frozenset[BrandId] = frozenset()
    age_restricted: bool = False  # any on-air brand is in an age-restricted category

    @property
    def is_house_ad(self) -> bool:
        return not self.campaign_ids


class SwapAction(StrEnum):
    UNCHANGED = "unchanged"  # same basket already on air
    KEEP_INCUMBENT = "keep_incumbent"  # challenger within the hysteresis margin
    SWAP = "swap"  # replace at `not_before` (may be now)


@dataclass(frozen=True, slots=True, kw_only=True)
class SwapInput:
    result: AuctionResult
    opportunity: AdOpportunity  # the facts the result was cleared against
    trigger: Trigger
    on_air: OnAir | None
    now: datetime
    creative_changed: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class SwapPlan:
    action: SwapAction
    not_before: datetime | None = None


# No-fills that are a blink rather than a decision: the audience looked away or its
# signal dropped out. Engine errors are handled by the desk, never graced here.
GRACED_NO_FILLS = frozenset(
    {NoFillReason.NO_AUDIENCE, NoFillReason.NO_ELIGIBLE_BIDS, NoFillReason.BELOW_RESERVE}
)


class MinAdTimePolicy(Policy[SwapInput, SwapPlan]):
    def __init__(self, timings: SwapTimings | None = None) -> None:
        self.timings = timings or SwapTimings()

    def apply(self, input: SwapInput) -> SwapPlan:
        result, on_air, now = input.result, input.on_air, input.now
        if on_air is None or on_air.is_house_ad:
            # Nothing sold is on screen: a paid result goes up at once.
            if result.filled:
                return SwapPlan(action=SwapAction.SWAP, not_before=now)
            return SwapPlan(action=SwapAction.UNCHANGED)
        layout_name = result.layout.name if result.layout else None
        same_winners = (
            result.winner_ids == on_air.campaign_ids and layout_name == on_air.layout_name
        )
        # Safety and stock-outs are never held (invariant 2, D5.1/D5.2).
        if self._unsafe_on_air(on_air, input):
            return SwapPlan(action=SwapAction.SWAP, not_before=now)
        if same_winners and not input.creative_changed:
            return SwapPlan(action=SwapAction.UNCHANGED)
        timings = self.timings
        if (
            input.trigger in SAME_MOMENT_TRIGGERS
            and not same_winners
            and result.filled
            and result.incumbent_value is not None
            and result.bundle_value.amount
            <= result.incumbent_value.amount * (1 + timings.hysteresis)
        ):
            return SwapPlan(action=SwapAction.KEEP_INCUMBENT)
        if result.no_fill is NoFillReason.NO_AUDIENCE and input.opportunity.moment.people > 0:
            # The group is still in front, eyes elsewhere. A 3 s grace here blinked the
            # ad off to the house ad and back on (as a fresh creative, restarting at the
            # teaser) whenever someone looked away a little longer. Unwatched seconds
            # bill nothing either way; a group that keeps ignoring it still frees the
            # screen. The plain leave grace applies once the room is empty.
            return SwapPlan(action=SwapAction.SWAP, not_before=now + timings.present_grace)
        if not result.filled and result.no_fill in GRACED_NO_FILLS:
            # D5.5: keep the creative for the grace *after attention or the signal left*,
            # not after it went on air: a glance away, or a cup hidden behind a hand for a
            # second, never strobes the house ad. If it comes back within the grace, the
            # next moment supersedes this held swap. Unsafe ads came down above already.
            return SwapPlan(action=SwapAction.SWAP, not_before=now + timings.leave_grace)
        if input.trigger is Trigger.MOMENT_CHANGED:
            hold = timings.new_group
        else:
            hold = timings.same_moment
        return SwapPlan(action=SwapAction.SWAP, not_before=max(now, on_air.displayed_at + hold))

    def unsafe_on_air(self, on_air: OnAir | None, opportunity: AdOpportunity) -> bool:
        """Must what is on air come down now, whatever the engine says? (invariant 2, D5.2)"""
        if on_air is None or on_air.is_house_ad:
            return False
        return self._facts_unsafe(on_air, opportunity)

    @staticmethod
    def _facts_unsafe(on_air: OnAir, opportunity: AdOpportunity) -> bool:
        if on_air.age_restricted and opportunity.moment.minor_likely:
            return True
        if on_air.brand_ids & opportunity.out_of_stock:
            return True
        book = {bidder.campaign_id: bidder for bidder in opportunity.bidders}
        return any(
            (bidder := book.get(campaign_id)) is None or not bidder.active
            for campaign_id in on_air.campaign_ids
        )

    @staticmethod
    def _unsafe_on_air(on_air: OnAir, input: SwapInput) -> bool:
        """Read the facts directly: an exclusion is only recorded for bidders that
        still match a segment, but a minor in front of a beer ad must end it even
        when the beer segment is gone or nobody is looking."""
        if MinAdTimePolicy._facts_unsafe(on_air, input.opportunity):
            return True
        return any(
            (exclusion := input.result.exclusion_for(campaign_id)) is not None
            and exclusion.reason in INELIGIBLE_ON_AIR
            for campaign_id in on_air.campaign_ids
        )
