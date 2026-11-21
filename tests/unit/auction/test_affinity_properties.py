"""Property-based invariants of the bundle auction with segment affinity on.

`test_bundle_properties.py` proves the auction rules on random books with exact
matching. This file draws a random affinity table (hierarchy and curated links
with random weights) and random `broad_match` flags on top of those books, and
checks the same product invariants plus the affinity-specific ones. The oracle
for "which bids reach which segment at what rate" is re-derived here from the
table's links, independently of `match_segments`.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from hypothesis import given, strategies as st

from app.modules.auction.domain.affinity import MatchKind, SegmentAffinity, SegmentLink
from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import ExclusionReason
from app.modules.auction.domain.valuation import price_ceiling, valuate
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.campaigns.domain.book import Bidder
from tests.unit.auction.test_bundle_properties import (
    PROPERTY_SETTINGS,
    SEGMENTS,
    TOLERANCE,
    opportunities,
)

weights = st.decimals(min_value="0.05", max_value="0.95", places=2)


@st.composite
def affinities(draw: st.DrawFn) -> SegmentAffinity:
    pairs = [(bid, seen) for bid in SEGMENTS for seen in SEGMENTS if bid != seen]
    chosen = draw(st.lists(st.sampled_from(pairs), unique=True, max_size=len(pairs)))
    kinds = (MatchKind.BROADER, MatchKind.NARROWER, MatchKind.RELATED)
    return SegmentAffinity(
        links=tuple(
            SegmentLink(
                bid_segment=bid,
                moment_segment=seen,
                kind=draw(st.sampled_from(kinds)),
                weight=draw(weights),
            )
            for bid, seen in chosen
        )
    )


@st.composite
def affinity_opportunities(draw: st.DrawFn) -> AdOpportunity:
    base = draw(opportunities())
    bidders = tuple(replace(bidder, broad_match=draw(st.booleans())) for bidder in base.bidders)
    return replace(base, bidders=bidders, affinity=draw(affinities()))


def _present(opportunity: AdOpportunity) -> frozenset[SegmentId]:
    return frozenset(segment.segment_id for segment in opportunity.moment.segments)


def _oracle_rates(bidder: Bidder, opportunity: AdOpportunity) -> dict[SegmentId, Decimal]:
    """Best rate × weight per present segment, straight from the table's links."""
    broad = bidder.broad_match and not bidder.age_restricted
    present = _present(opportunity)
    links = {
        (link.bid_segment, link.moment_segment): link.weight for link in opportunity.affinity.links
    }
    best: dict[SegmentId, Decimal] = {}
    for bid in bidder.segment_bids:
        for seen in present:
            if seen == bid.segment_id:
                weight = Decimal(1)
            elif broad and (bid.segment_id, seen) in links:
                weight = links[(bid.segment_id, seen)]
            else:
                continue
            best[seen] = max(best.get(seen, Decimal(0)), bid.rate.amount * weight)
    return best


@PROPERTY_SETTINGS
@given(affinity_opportunities(), st.randoms(use_true_random=False))
def test_result_is_independent_of_bidder_order(opportunity: AdOpportunity, rng) -> None:  # type: ignore[no-untyped-def]
    shuffled = list(opportunity.bidders)
    rng.shuffle(shuffled)
    assert clear(replace(opportunity, bidders=tuple(shuffled))) == clear(opportunity)


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_matches_agree_with_the_oracle_and_are_well_formed(opportunity: AdOpportunity) -> None:
    by_campaign = {bidder.campaign_id: bidder for bidder in opportunity.bidders}
    present = _present(opportunity)
    for bid in clear(opportunity).bids:
        bidder = by_campaign[bid.campaign_id]
        oracle = _oracle_rates(bidder, opportunity)
        segments = [match.moment_segment for match in bid.matches]
        assert len(segments) == len(set(segments)) and set(segments) <= present
        assert {match.moment_segment: match.rate for match in bid.matches} == oracle
        own = {item.segment_id: item.rate.amount for item in bidder.segment_bids}
        for match in bid.matches:
            assert match.bid_segment in own
            assert match.rate == own[match.bid_segment] * match.weight
            exact = match.moment_segment == match.bid_segment
            assert (match.kind is MatchKind.EXACT) == exact
            assert match.weight == 1 if exact else Decimal(0) < match.weight < 1
            if not (bidder.broad_match and not bidder.age_restricted):
                assert match.kind is MatchKind.EXACT


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_affinity_never_lowers_a_bid_value(opportunity: AdOpportunity) -> None:
    exact = replace(opportunity, affinity=SegmentAffinity.exact_only())
    for bidder in opportunity.bidders:
        widened, literal = valuate(bidder, opportunity), valuate(bidder, exact)
        assert widened.value >= literal.value
        assert widened.ceiling_rate >= literal.ceiling_rate
        assert widened.matched or not literal.matched


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_an_exact_only_table_is_the_same_as_every_bidder_opting_out(
    opportunity: AdOpportunity,
) -> None:
    opted_out = replace(
        opportunity,
        bidders=tuple(replace(bidder, broad_match=False) for bidder in opportunity.bidders),
    )
    assert clear(opted_out) == clear(replace(opted_out, affinity=SegmentAffinity.exact_only()))
    # And opting out everywhere clears like the table never existed.
    literal = clear(replace(opportunity, affinity=SegmentAffinity.exact_only()))
    flat = clear(opted_out)
    assert [(w.campaign_id, w.price_per_as) for w in flat.winners] == [
        (w.campaign_id, w.price_per_as) for w in literal.winners
    ]
    assert (flat.coverage, flat.bundle_value, flat.exclusions) == (
        literal.coverage,
        literal.bundle_value,
        literal.exclusions,
    )


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_price_never_exceeds_the_discounted_bid(opportunity: AdOpportunity) -> None:
    result = clear(opportunity)
    on_screen = frozenset(winner.bidder.brand_id for winner in result.winners)
    for winner in result.winners:
        bidder = winner.bidder
        best = max(_oracle_rates(bidder, opportunity).values())
        bonuses = sum(
            (b.rate.amount for b in bidder.basket_bonuses if b.partner in on_screen), Decimal(0)
        )
        ceiling = best * bidder.live_multiplier.value + bonuses
        assert ceiling == price_ceiling(bidder, valuate(bidder, opportunity), on_screen)
        assert winner.price_per_as.amount <= ceiling + TOLERANCE
        assert winner.payment_per_s.amount <= winner.value_per_s.amount + TOLERANCE


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_winners_reach_a_present_segment_and_are_compatible(opportunity: AdOpportunity) -> None:
    result = clear(opportunity)
    winners = [winner.bidder for winner in result.winners]
    for bidder in winners:
        assert _oracle_rates(bidder, opportunity)
    assert len({bidder.category for bidder in winners}) == len(winners)
    for index, left in enumerate(winners):
        for right in winners[index + 1 :]:
            assert left.brand_id in right.partners and right.brand_id in left.partners
            assert frozenset({left.brand_id, right.brand_id}) in opportunity.approved_baskets
    if result.layout is None:
        assert result.winners == ()
    else:
        looking = opportunity.moment.looking
        assert result.bundle_value.amount >= opportunity.reserve.amount * looking - TOLERANCE


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_minor_likely_blocks_every_age_restricted_bid_even_with_affinity(
    opportunity: AdOpportunity,
) -> None:
    result = clear(opportunity)
    if not opportunity.moment.minor_likely:
        return
    assert not any(winner.bidder.age_restricted for winner in result.winners)
    if opportunity.moment.looking == 0:
        return
    safety = {e.campaign_id for e in result.exclusions if e.reason is ExclusionReason.SAFETY}
    for bidder in opportunity.bidders:
        if bidder.age_restricted and _oracle_rates(bidder, opportunity):
            assert bidder.campaign_id in safety


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_coverage_is_a_share_and_only_counts_reached_viewers(opportunity: AdOpportunity) -> None:
    result = clear(opportunity)
    assert Decimal(0) <= result.coverage <= 1
    if not result.winners:
        assert result.coverage == 0
    exact = clear(replace(opportunity, affinity=SegmentAffinity.exact_only()))
    if exact.winner_ids == result.winner_ids:
        # Same winners: affinity can only add (partial) reach, never remove it.
        assert result.coverage >= exact.coverage


@PROPERTY_SETTINGS
@given(affinity_opportunities())
def test_every_valued_loser_gets_exactly_one_reason(opportunity: AdOpportunity) -> None:
    result = clear(opportunity)
    excluded = [item.campaign_id for item in result.exclusions]
    assert len(excluded) == len(set(excluded))
    if opportunity.moment.looking == 0:
        return
    for bid in result.bids:
        if bid.campaign_id in result.winner_ids:
            assert bid.campaign_id not in excluded
        elif bid.value_per_s.amount > 0 or result.exclusion_for(bid.campaign_id):
            assert bid.campaign_id in excluded
