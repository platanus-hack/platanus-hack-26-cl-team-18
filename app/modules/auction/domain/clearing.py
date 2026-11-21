"""Bundle clearing: eligibility → candidates → winner → prices → exclusions (docs/auction-plan.md §4).

Units: values and payments are money per *screen*-second (summed over the
looking viewers). A winner's clearing price per attention-second is its
payment divided by the looking count, so `price × measured_AS` charges the
expected payment and zero attention charges zero (R-P5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

from app.modules.auction.domain.opportunity import ZERO, AdOpportunity, Layout, PricingRule
from app.modules.auction.domain.result import (
    AuctionResult,
    BidValue,
    BundleOption,
    Exclusion,
    ExclusionReason,
    NoFillReason,
    Winner,
)
from app.modules.auction.domain.rules import (
    COMPATIBLE,
    Basket,
    BidderInMoment,
    FirstEligibilityFailure,
)
from app.modules.auction.domain.valuation import (
    held_basket_boost,
    price_ceiling,
    reach_weights,
    valuate,
)
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import Bidder, CampaignId
from app.modules.common.domain.value_objects.money_rate import MoneyRate

DEFAULT_TOP_K = 8
_EPSILON = Decimal("0.00000001")
_MAX_CORE_ROUNDS = 64


@dataclass(frozen=True, slots=True)
class _Ranked:
    bidder: Bidder
    value: Decimal  # per screen-second, boosts included (ranking)
    ceiling: Decimal  # max chargeable per AS before basket bonuses
    reach: frozenset[SegmentId] = frozenset()  # moment segments its bids reach

    @property
    def campaign_id(self) -> CampaignId:
        return self.bidder.campaign_id


@dataclass(frozen=True, slots=True)
class _Candidate:
    layout: Layout
    members: tuple[_Ranked, ...]  # in slot order (descending value)
    own: tuple[Decimal, ...]  # each member's own value in this bundle
    limits: tuple[Decimal, ...]  # the most each member can be charged: min(own, ceiling × looking)
    value: Decimal

    @property
    def ids(self) -> frozenset[CampaignId]:
        return frozenset(member.campaign_id for member in self.members)

    def sort_key(self) -> tuple[Decimal, int, tuple[str, ...], str]:
        ids = tuple(sorted(str(member.campaign_id.value) for member in self.members))
        return (-self.value, len(self.members), ids, self.layout.name)


@dataclass(frozen=True, slots=True)
class _Cleared:
    best: _Candidate | None
    payments: tuple[Decimal, ...]
    candidates: tuple[_Candidate, ...]


def clear(
    opportunity: AdOpportunity,
    *,
    pricing: PricingRule = PricingRule.CORE_ADJUSTED,
    top_k: int = DEFAULT_TOP_K,
) -> AuctionResult:
    """Run one bundle auction. Pure: same opportunity → same result."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    currency = opportunity.reserve.currency
    looking = opportunity.moment.looking

    def rate(amount: Decimal) -> MoneyRate:
        return MoneyRate.create(currency=currency, amount=amount)

    zero = rate(ZERO)
    if looking == 0:
        return AuctionResult(
            pricing=pricing,
            layout=None,
            winners=(),
            bids=(),
            exclusions=(),
            bundle_value=zero,
            bundle_revenue=zero,
            single_brand_value=zero,
            single_brand_revenue=zero,
            coverage=ZERO,
            no_fill=NoFillReason.NO_AUDIENCE,
        )

    ranked, exclusions, bids = _eligible(opportunity)
    top = ranked[:top_k]
    cleared = _clear_layouts(opportunity, top, opportunity.layouts, pricing)
    solo_layouts = tuple(layout for layout in opportunity.layouts if layout.slots == 1)
    solo = _clear_layouts(opportunity, top, solo_layouts or (Layout.solo(),), pricing)
    incumbent_value = (
        max(
            (item.value for item in cleared.candidates if item.ids == opportunity.incumbent),
            default=None,
        )
        if opportunity.incumbent
        else None
    )
    best = cleared.best
    if best is None:
        reason = NoFillReason.BELOW_RESERVE if ranked else NoFillReason.NO_ELIGIBLE_BIDS
        exclusions += [
            Exclusion(campaign_id=item.campaign_id, reason=ExclusionReason.BELOW_RESERVE)
            for item in ranked
        ]
        return AuctionResult(
            pricing=pricing,
            layout=None,
            winners=(),
            bids=bids,
            exclusions=tuple(exclusions),
            bundle_value=zero,
            bundle_revenue=zero,
            single_brand_value=rate(_value_of(solo.best)),
            single_brand_revenue=rate(sum(solo.payments, ZERO)),
            coverage=ZERO,
            no_fill=reason,
            incumbent_value=None if incumbent_value is None else rate(incumbent_value),
        )

    winners = tuple(
        Winner(
            bidder=member.bidder,
            slot=slot,
            share=best.layout.shares[slot],
            efficiency=best.layout.efficiencies[slot],
            value_per_s=rate(own),
            payment_per_s=rate(payment),
            price_per_as=rate(payment / looking),
        )
        for slot, (member, own, payment) in enumerate(
            zip(best.members, best.own, cleared.payments, strict=True)
        )
    )
    top_ids = {item.campaign_id for item in top}
    values = {item.campaign_id: item.value for item in ranked}
    exclusions += [
        _losing_reason(item, winners, opportunity, values, in_top=item.campaign_id in top_ids)
        for item in ranked
        if item.campaign_id not in best.ids
    ]
    return AuctionResult(
        pricing=pricing,
        layout=best.layout,
        winners=winners,
        bids=bids,
        exclusions=tuple(exclusions),
        bundle_value=rate(best.value),
        bundle_revenue=rate(sum(cleared.payments, ZERO)),
        single_brand_value=rate(_value_of(solo.best)),
        single_brand_revenue=rate(sum(solo.payments, ZERO)),
        coverage=_coverage(opportunity, winners),
        no_fill=None,
        alternatives=_alternatives(cleared.candidates, rate),
        incumbent_value=None if incumbent_value is None else rate(incumbent_value),
    )


def _eligible(
    opportunity: AdOpportunity,
) -> tuple[list[_Ranked], list[Exclusion], tuple[BidValue, ...]]:
    currency = opportunity.reserve.currency
    first_failure = FirstEligibilityFailure()
    ranked: list[_Ranked] = []
    exclusions: list[Exclusion] = []
    bids: list[BidValue] = []
    for bidder in sorted(opportunity.bidders, key=lambda item: item.campaign_id.value):
        valuation = valuate(bidder, opportunity)
        if not valuation.matched:
            continue  # no segment present: no bid for this moment, not an exclusion (R-V2)
        reason = first_failure.apply(
            BidderInMoment(
                bidder=bidder,
                opportunity=opportunity,
                ceiling_rate=price_ceiling(bidder, valuation),
            )
        )
        if reason is ExclusionReason.INVALID_BID:
            exclusions.append(Exclusion(campaign_id=bidder.campaign_id, reason=reason))
            continue
        bids.append(
            BidValue(
                campaign_id=bidder.campaign_id,
                value_per_s=MoneyRate.create(currency=currency, amount=valuation.value),
                matches=valuation.matches,
            )
        )
        if reason is not None:
            exclusions.append(Exclusion(campaign_id=bidder.campaign_id, reason=reason))
        elif valuation.value > ZERO:
            ranked.append(
                _Ranked(
                    bidder=bidder,
                    value=valuation.value,
                    ceiling=valuation.ceiling_rate,
                    reach=frozenset(match.moment_segment for match in valuation.matches),
                )
            )
    ranked.sort(key=lambda item: (-item.value, item.campaign_id.value))
    return ranked, exclusions, tuple(bids)


def _clear_layouts(
    opportunity: AdOpportunity,
    top: Sequence[_Ranked],
    layouts: Sequence[Layout],
    pricing: PricingRule,
) -> _Cleared:
    candidates = _candidates(opportunity, top, layouts)
    if not candidates:
        return _Cleared(best=None, payments=(), candidates=())
    best = candidates[0]
    payments = _vcg(best, candidates)
    if pricing is PricingRule.CORE_ADJUSTED:
        payments = _core_adjust(best, candidates, payments)
    floors = _floors(opportunity, best)
    payments = tuple(
        min(limit, max(payment, floor))
        for limit, payment, floor in zip(best.limits, payments, floors, strict=True)
    )
    return _Cleared(best=best, payments=payments, candidates=candidates)


ALTERNATIVES_SHOWN = 4


def _alternatives(
    candidates: tuple[_Candidate, ...], rate: Callable[[Decimal], MoneyRate]
) -> tuple[BundleOption, ...]:
    """The best baskets, each set of brands once (its best layout), best first."""
    seen: set[frozenset[CampaignId]] = set()
    options: list[BundleOption] = []
    for candidate in candidates:  # already sorted best first
        if candidate.ids in seen:
            continue
        seen.add(candidate.ids)
        options.append(
            BundleOption(
                campaign_ids=tuple(member.campaign_id for member in candidate.members),
                value=rate(candidate.value),
            )
        )
        if len(options) == ALTERNATIVES_SHOWN:
            break
    return tuple(options)


def _candidates(
    opportunity: AdOpportunity, top: Sequence[_Ranked], layouts: Sequence[Layout]
) -> tuple[_Candidate, ...]:
    looking = Decimal(opportunity.moment.looking)
    candidates: list[_Candidate] = []
    for layout in sorted(layouts, key=lambda item: item.name):
        for members in combinations(top, layout.slots):
            basket = Basket(bidders=tuple(m.bidder for m in members), opportunity=opportunity)
            if not COMPATIBLE.is_satisfied_by(basket):
                continue
            brands = {member.bidder.brand_id for member in members}
            reach = [member.reach for member in members]
            # Efficiency above share pays for viewers only this slot reaches. A member
            # that targets the same segments as another slot reaches nobody new, so it
            # keeps only its screen share (same audience → the solo wins).
            weights = tuple(
                layout.efficiencies[slot]
                if not any(
                    reach[slot] & reach[other] for other in range(len(members)) if other != slot
                )
                else layout.shares[slot]
                for slot in range(len(members))
            )
            basket_brands = frozenset(brands)
            own = tuple(
                member.value
                * efficiency
                * held_basket_boost(member.bidder, basket_brands, opportunity)
                + sum(
                    (
                        bonus.rate.amount * looking
                        for bonus in member.bidder.basket_bonuses
                        if bonus.partner in brands
                    ),
                    ZERO,
                )
                for member, efficiency in zip(members, weights, strict=True)
            )
            # A bid is a contractual maximum: boosts raise value, never the charge.
            limits = tuple(
                min(
                    value,
                    (
                        member.ceiling
                        + sum(
                            (
                                bonus.rate.amount
                                for bonus in member.bidder.basket_bonuses
                                if bonus.partner in brands
                            ),
                            ZERO,
                        )
                    )
                    * looking,
                )
                for member, value in zip(members, own, strict=True)
            )
            floors = tuple(opportunity.reserve.amount * looking * share for share in layout.shares)
            # Every slot must clear its own reserve without charging more than the
            # member's value or bid (individual rationality, R-P4).
            if any(limit < floor for limit, floor in zip(limits, floors, strict=True)):
                continue
            candidates.append(
                _Candidate(
                    layout=layout, members=members, own=own, limits=limits, value=sum(own, ZERO)
                )
            )
    return tuple(sorted(candidates, key=_Candidate.sort_key))


def _vcg(best: _Candidate, candidates: Sequence[_Candidate]) -> tuple[Decimal, ...]:
    """p_c = V*(without c) − (V(B*) − own_c), never negative."""
    payments = []
    for member, own in zip(best.members, best.own, strict=True):
        without = max(
            (item.value for item in candidates if member.campaign_id not in item.ids),
            default=ZERO,
        )
        payments.append(max(ZERO, without - (best.value - own)))
    return tuple(payments)


def _core_adjust(
    best: _Candidate, candidates: Sequence[_Candidate], payments: tuple[Decimal, ...]
) -> tuple[Decimal, ...]:
    """Raise VCG payments until no losing basket would have paid more (minimal core point).

    For every candidate B': Σ_{c ∈ B*∖B'} p_c ≥ V(B') − Σ_{c ∈ B*∩B'} own_c.
    Deficits are spread equally over B*∖B', capped at each winner's charge limit
    (own value and bid ceiling). Constraints whose blockers are all at their
    limit can't be met and are skipped, so the loop always terminates.
    """
    ids = [member.campaign_id for member in best.members]
    own = dict(zip(ids, best.own, strict=True))
    limit = dict(zip(ids, best.limits, strict=True))
    paid = {c: min(p, limit[c]) for c, p in zip(ids, payments, strict=True)}
    for _ in range(_MAX_CORE_ROUNDS):
        worst: tuple[Decimal, tuple[str, ...], list[CampaignId]] | None = None
        for item in candidates:
            blocking = [campaign_id for campaign_id in ids if campaign_id not in item.ids]
            if not blocking or all(limit[c] - paid[c] <= _EPSILON for c in blocking):
                continue
            shared = sum((own[c] for c in ids if c in item.ids), ZERO)
            deficit = item.value - shared - sum((paid[c] for c in blocking), ZERO)
            key = tuple(sorted(str(c.value) for c in item.ids)) + (item.layout.name,)
            if deficit > _EPSILON and (worst is None or (deficit, key) > (worst[0], worst[1])):
                worst = (deficit, key, blocking)
        if worst is None:
            break
        deficit, _, blocking = worst
        while deficit > _EPSILON:
            open_ = [c for c in blocking if limit[c] - paid[c] > _EPSILON]
            if not open_:
                break
            step = deficit / len(open_)
            for campaign_id in open_:
                raise_by = min(step, limit[campaign_id] - paid[campaign_id])
                paid[campaign_id] += raise_by
                deficit -= raise_by
    return tuple(paid[campaign_id] for campaign_id in ids)


def _floors(opportunity: AdOpportunity, best: _Candidate) -> tuple[Decimal, ...]:
    looking = Decimal(opportunity.moment.looking)
    return tuple(opportunity.reserve.amount * looking * share for share in best.layout.shares)


def _value_of(candidate: _Candidate | None) -> Decimal:
    return ZERO if candidate is None else candidate.value


def _losing_reason(
    loser: _Ranked,
    winners: Sequence[Winner],
    opportunity: AdOpportunity,
    values: dict[CampaignId, Decimal],
    *,
    in_top: bool,
) -> Exclusion:
    """The honest reason a valued, eligible bidder did not win (R-O2)."""
    bidder = loser.bidder

    def excluded(reason: ExclusionReason, winner: Winner | None = None) -> Exclusion:
        return Exclusion(
            campaign_id=bidder.campaign_id,
            reason=reason,
            related_campaign_id=None if winner is None else winner.campaign_id,
        )

    if not in_top:
        return excluded(ExclusionReason.OUTBID)
    for winner in winners:
        if winner.bidder.category == bidder.category:
            return excluded(ExclusionReason.COMPETITOR, winner)
    joined = next(
        (
            layout
            for layout in sorted(opportunity.layouts, key=lambda item: item.name)
            if layout.slots == len(winners) + 1
        ),
        None,
    )
    if joined is None:
        return excluded(ExclusionReason.OUTBID)  # no bigger layout: partnership couldn't help
    for winner in winners:
        other = winner.bidder
        if other.brand_id == bidder.brand_id:
            return excluded(ExclusionReason.OUTBID, winner)
        if other.brand_id not in bidder.partners or bidder.brand_id not in other.partners:
            return excluded(ExclusionReason.NOT_PARTNER, winner)
    for winner in winners:
        if not opportunity.basket_approved(frozenset({winner.bidder.brand_id, bidder.brand_id})):
            return excluded(ExclusionReason.NO_BASKET_OFFER, winner)
    # It could have joined: would its slot in the bigger layout clear the reserve?
    slot = sum(1 for winner in winners if values.get(winner.campaign_id, ZERO) >= loser.value)
    looking = Decimal(opportunity.moment.looking)
    floor = opportunity.reserve.amount * looking * joined.shares[slot]
    best_case = min(loser.value * joined.efficiencies[slot], loser.ceiling * looking)
    if best_case < floor:
        return excluded(ExclusionReason.BELOW_RESERVE)
    return excluded(ExclusionReason.OUTBID)


def _coverage(opportunity: AdOpportunity, winners: Sequence[Winner]) -> Decimal:
    """Upper-bound share of looking viewers matched by a winner (segment counts may overlap).

    An approximate match covers its segment's viewers only by its affinity weight,
    so a "related" ad never counts as fully reaching someone.
    """
    looking = opportunity.moment.looking
    if not looking:
        return ZERO
    present = frozenset(segment.segment_id for segment in opportunity.moment.segments)
    reached: dict[SegmentId, Decimal] = {}
    for winner in winners:
        for segment_id, weight in reach_weights(
            winner.bidder, present, opportunity.affinity
        ).items():
            reached[segment_id] = max(reached.get(segment_id, ZERO), weight)
    covered = sum(
        (
            segment.viewer_count * reached[segment.segment_id]
            for segment in opportunity.moment.segments
            if segment.segment_id in reached
        ),
        ZERO,
    )
    return min(Decimal(1), covered / looking)
