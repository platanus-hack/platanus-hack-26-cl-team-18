"""Segment affinity: a bid reaches broader, narrower and related segments at a discount.

Table-driven like the rest of the auction suite: the matching is part of the
demo's credibility (who won, why, and what they paid), so these never go red.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.auction.domain.affinity import (
    MatchKind,
    RelatedSegments,
    SegmentAffinity,
    SegmentLink,
)
from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.result import ExclusionReason, NoFillReason
from app.modules.auction.domain.valuation import SegmentMatch
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from tests.unit.auction.builders import basket, bidder, moment, opportunity, segment

SPORTS = SegmentId.create("607")
CYCLING = SegmentId.create("625")
SKATE = SegmentId.create("636")
COFFEE = SegmentId.create("977")
RTD_COFFEE = SegmentId.create("982")
BEER = SegmentId.create("369")
WATER = SegmentId.create("1000")

PATHS = {
    SPORTS: "Interest | Sports",
    CYCLING: "Interest | Sports | Cycling",
    # Two levels below Sports: "Extreme Sports" is not in the catalog.
    SKATE: "Interest | Sports | Extreme Sports | Skateboarding",
    COFFEE: "Purchase Intent | Beverages | Coffee & Tea | Coffee",
    RTD_COFFEE: "Purchase Intent | Beverages | Coffee & Tea | Tea/Coffee - Ready-to-Drink",
    BEER: "Interest | Food & Drink | Alcoholic Beverages",
    WATER: "Purchase Intent | Beverages | Water | Bottled Water",
}
AFFINITY = SegmentAffinity.build(
    paths=PATHS,
    related=(RelatedSegments(left=COFFEE, right=RTD_COFFEE, weight=Decimal("0.7")),),
)


def reach(table: SegmentAffinity, bid: SegmentId) -> dict[SegmentId, tuple[MatchKind, Decimal]]:
    return {link.moment_segment: (link.kind, link.weight) for link in table.reach(bid)}


# ---------------------------------------------------------------- the table


@pytest.mark.parametrize(
    ("bid", "expected"),
    [
        (
            SPORTS,
            {
                SPORTS: (MatchKind.EXACT, Decimal(1)),
                CYCLING: (MatchKind.BROADER, Decimal("0.8")),
                SKATE: (MatchKind.BROADER, Decimal("0.64")),  # 0.8 per level
            },
        ),
        # A child bid reaches its parent weakly, and never its siblings.
        (CYCLING, {CYCLING: (MatchKind.EXACT, 1), SPORTS: (MatchKind.NARROWER, Decimal("0.4"))}),
        (SKATE, {SKATE: (MatchKind.EXACT, 1), SPORTS: (MatchKind.NARROWER, Decimal("0.16"))}),
        # Curated pairs are symmetric.
        (COFFEE, {COFFEE: (MatchKind.EXACT, 1), RTD_COFFEE: (MatchKind.RELATED, Decimal("0.7"))}),
        (
            RTD_COFFEE,
            {RTD_COFFEE: (MatchKind.EXACT, 1), COFFEE: (MatchKind.RELATED, Decimal("0.7"))},
        ),
        # A shared path prefix that isn't itself a segment links nothing.
        (WATER, {WATER: (MatchKind.EXACT, 1)}),
        (BEER, {BEER: (MatchKind.EXACT, 1)}),
        # Unknown to the table: exact only.
        (SegmentId.create("X1"), {SegmentId.create("X1"): (MatchKind.EXACT, 1)}),
    ],
)
def test_affinity_table_from_paths_and_curated_pairs(
    bid: SegmentId, expected: dict[SegmentId, tuple[MatchKind, Decimal]]
) -> None:
    assert reach(AFFINITY, bid) == expected


def test_exact_match_is_always_listed_first() -> None:
    assert AFFINITY.reach(SPORTS)[0].kind is MatchKind.EXACT


def test_exact_only_table_reaches_nothing_else() -> None:
    assert reach(SegmentAffinity.exact_only(), SPORTS) == {SPORTS: (MatchKind.EXACT, 1)}


@pytest.mark.parametrize(
    ("weight", "sports_to_cycling", "cycling_to_sports"),
    [
        # A curated pair stronger than the hierarchy wins in both directions.
        ("0.9", (MatchKind.RELATED, Decimal("0.9")), (MatchKind.RELATED, Decimal("0.9"))),
        # A weaker one keeps the broader link, but still beats the narrower one.
        ("0.5", (MatchKind.BROADER, Decimal("0.8")), (MatchKind.RELATED, Decimal("0.5"))),
    ],
)
def test_overlapping_hierarchy_and_curated_pair_keeps_the_better_weight(
    weight: str,
    sports_to_cycling: tuple[MatchKind, Decimal],
    cycling_to_sports: tuple[MatchKind, Decimal],
) -> None:
    table = SegmentAffinity.build(
        paths=PATHS,
        related=(RelatedSegments(left=SPORTS, right=CYCLING, weight=Decimal(weight)),),
    )
    assert reach(table, SPORTS)[CYCLING] == sports_to_cycling
    assert reach(table, CYCLING)[SPORTS] == cycling_to_sports


def test_build_is_independent_of_input_order() -> None:
    reversed_paths = dict(reversed(list(PATHS.items())))
    assert SegmentAffinity.build(paths=reversed_paths, related=()) == SegmentAffinity.build(
        paths=PATHS, related=()
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda: RelatedSegments(left=COFFEE, right=COFFEE, weight=Decimal("0.5")),
        lambda: RelatedSegments(left=COFFEE, right=WATER, weight=Decimal(1)),
        lambda: RelatedSegments(left=COFFEE, right=WATER, weight=Decimal(0)),
        lambda: SegmentLink(
            bid_segment=COFFEE, moment_segment=COFFEE, kind=MatchKind.EXACT, weight=Decimal("0.5")
        ),
        lambda: SegmentLink(
            bid_segment=COFFEE, moment_segment=WATER, kind=MatchKind.EXACT, weight=Decimal(1)
        ),
        lambda: SegmentLink(
            bid_segment=COFFEE, moment_segment=WATER, kind=MatchKind.RELATED, weight=Decimal("1.2")
        ),
        lambda: SegmentAffinity(
            links=(
                SegmentLink(
                    bid_segment=COFFEE,
                    moment_segment=COFFEE,
                    kind=MatchKind.EXACT,
                    weight=Decimal(1),
                ),
            )
        ),
        lambda: SegmentAffinity(
            links=(
                SegmentLink(
                    bid_segment=COFFEE,
                    moment_segment=WATER,
                    kind=MatchKind.RELATED,
                    weight=Decimal("0.5"),
                ),
            )
            * 2
        ),
    ],
)
def test_invalid_links_cannot_exist(build: object) -> None:
    with pytest.raises(ValueError):
        build()  # type: ignore[operator]


# ---------------------------------------------------------------- the auction


def test_without_the_table_a_parent_bid_misses_the_child_segment() -> None:
    sports_fan = bidder(0, "bikes", {SPORTS: "10"})
    result = clear(opportunity(moment(segment(CYCLING, 2)), sports_fan))
    assert result.bids == ()
    assert result.no_fill is NoFillReason.NO_ELIGIBLE_BIDS


def test_a_broader_bid_reaches_the_child_segment_at_its_discount() -> None:
    sports_fan = bidder(0, "bikes", {SPORTS: "10"})
    result = clear(opportunity(moment(segment(CYCLING, 2)), sports_fan, affinity=AFFINITY))

    assert [w.campaign_id for w in result.winners] == [sports_fan.campaign_id]
    (bid,) = result.bids
    assert bid.value_per_s.amount == 16  # 10 · 0.8 · 2 viewers
    assert bid.matches == (
        SegmentMatch(
            moment_segment=CYCLING,
            bid_segment=SPORTS,
            kind=MatchKind.BROADER,
            weight=Decimal("0.8"),
            rate=Decimal(8),
        ),
    )
    assert result.coverage == Decimal("0.8")  # an approximate ad covers by its weight


def test_an_approximate_match_never_charges_above_the_discounted_bid() -> None:
    # Speech intent lifts the sports fan's *value* to 24/s, and the exact rival's
    # 18/s would price it there, but its charge stops at 10 · 0.8 per attention-second.
    sports_fan = bidder(0, "bikes", {SPORTS: "10"})
    cyclist_shop = bidder(1, "bikes", {CYCLING: "6"})
    result = clear(
        opportunity(
            moment(segment(CYCLING, 2, speech_intent=100)),
            sports_fan,
            cyclist_shop,
            affinity=AFFINITY,
        )
    )

    (winner,) = result.winners
    assert winner.campaign_id == sports_fan.campaign_id
    assert winner.price_per_as.amount == 8
    rival = result.exclusion_for(cyclist_shop.campaign_id)
    assert rival is not None and rival.reason is ExclusionReason.COMPETITOR


def test_at_equal_bids_the_exact_segment_outranks_the_broader_one() -> None:
    cyclist_shop = bidder(0, "bikes", {CYCLING: "6"})
    sports_fan = bidder(1, "bikes", {SPORTS: "6"})
    result = clear(
        opportunity(moment(segment(CYCLING, 2)), sports_fan, cyclist_shop, affinity=AFFINITY)
    )
    assert [w.campaign_id for w in result.winners] == [cyclist_shop.campaign_id]
    by_campaign = {bid.campaign_id: bid.value_per_s.amount for bid in result.bids}
    assert by_campaign == {cyclist_shop.campaign_id: 12, sports_fan.campaign_id: Decimal("9.6")}


@pytest.mark.parametrize(
    ("bids", "bid_segment", "kind", "value"),
    [
        # The broader route pays more (10 · 0.8 > 5): it is the one counted.
        ({SPORTS: "10", CYCLING: "5"}, SPORTS, MatchKind.BROADER, Decimal(8)),
        # A tie (5 · 0.8 = 4) prefers the literal match.
        ({SPORTS: "5", CYCLING: "4"}, CYCLING, MatchKind.EXACT, Decimal(4)),
    ],
)
def test_each_moment_segment_counts_once_through_the_best_paying_bid(
    bids: dict[SegmentId, str | int], bid_segment: SegmentId, kind: MatchKind, value: Decimal
) -> None:
    result = clear(
        opportunity(moment(segment(CYCLING, 1)), bidder(0, "bikes", bids), affinity=AFFINITY)
    )
    (bid,) = result.bids
    (match,) = bid.matches
    assert (match.bid_segment, match.kind) == (bid_segment, kind)
    assert bid.value_per_s.amount == value


def test_a_campaign_can_opt_out_to_exact_match_only() -> None:
    sports_fan = bidder(0, "bikes", {SPORTS: "10"}, broad_match=False)
    result = clear(opportunity(moment(segment(CYCLING, 2)), sports_fan, affinity=AFFINITY))
    assert result.bids == ()
    assert not result.filled


def test_a_related_match_completes_a_basket_and_counts_partial_coverage() -> None:
    # One cyclist, one coffee drinker. The ready-to-drink brand reaches the coffee
    # drinker through the curated pair; together they beat the sports solo.
    sports_fan = bidder(0, "bikes", {SPORTS: "8"}, partners=(1,))
    rtd = bidder(1, "coffee", {RTD_COFFEE: "5"}, partners=(0,))
    result = clear(
        opportunity(
            moment(segment(CYCLING, 1), segment(COFFEE, 1), looking=2),
            sports_fan,
            rtd,
            baskets=(basket(0, 1),),
            affinity=AFFINITY,
        )
    )

    assert result.layout is not None and result.layout.name == "duo"
    assert result.winner_ids == {sports_fan.campaign_id, rtd.campaign_id}
    kinds = {bid.campaign_id: [m.kind for m in bid.matches] for bid in result.bids}
    assert kinds == {
        sports_fan.campaign_id: [MatchKind.BROADER],
        rtd.campaign_id: [MatchKind.RELATED],
    }
    assert result.coverage == Decimal("0.75")  # (1 · 0.8 + 1 · 0.7) / 2


def test_minor_safety_still_blocks_an_age_restricted_bid_reached_by_affinity() -> None:
    table = SegmentAffinity.build(
        paths=PATHS,
        related=(RelatedSegments(left=BEER, right=WATER, weight=Decimal("0.6")),),
    )
    brewer = bidder(0, "alcohol", {BEER: "20"}, age_restricted=True)
    result = clear(
        opportunity(moment(segment(WATER, 2), minor_likely=True), brewer, affinity=table)
    )
    assert not result.filled
    blocked = result.exclusion_for(brewer.campaign_id)
    assert blocked is not None and blocked.reason is ExclusionReason.SAFETY


def test_affinity_auction_is_independent_of_bidder_order() -> None:
    bidders = (
        bidder(0, "bikes", {SPORTS: "8"}, partners=(1,)),
        bidder(1, "coffee", {RTD_COFFEE: "5"}, partners=(0,)),
        bidder(2, "bikes", {CYCLING: "6"}),
    )
    audience = moment(segment(CYCLING, 1), segment(COFFEE, 1), looking=2)
    forward = clear(opportunity(audience, *bidders, baskets=(basket(0, 1),), affinity=AFFINITY))
    backward = clear(
        opportunity(audience, *reversed(bidders), baskets=(basket(0, 1),), affinity=AFFINITY)
    )
    assert forward == backward


# ---------------------------------------------------------------- the shipped catalog


def _catalog_affinity() -> SegmentAffinity:
    catalog = load_catalog().catalog
    return SegmentAffinity.build(
        paths={item.segment_id: item.path for item in catalog.segments},
        related=(
            RelatedSegments(left=item.left, right=item.right, weight=item.weight.to_ratio())
            for item in catalog.relations
        ),
    )


def test_shipped_catalog_links_the_hierarchy_and_curated_pairs() -> None:
    table = _catalog_affinity()
    assert reach(table, SPORTS)[CYCLING] == (MatchKind.BROADER, Decimal("0.8"))
    assert reach(table, COFFEE)[RTD_COFFEE] == (MatchKind.RELATED, Decimal("0.7"))
    assert reach(table, SegmentId.create("1115"))[SegmentId.create("1122")][0] is MatchKind.BROADER


def test_shipped_catalog_never_widens_alcohol_reach() -> None:
    """Invariant 2 hardening: an alcohol bid reaches alcohol viewers only, and nothing reaches alcohol."""
    table = _catalog_affinity()
    assert reach(table, BEER) == {BEER: (MatchKind.EXACT, 1)}
    assert all(link.moment_segment != BEER and link.bid_segment != BEER for link in table.links)
