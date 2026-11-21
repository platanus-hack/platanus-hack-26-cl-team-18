"""Segment affinity: a bid reaches broader, narrower and related segments at a discount.

Table-driven like the rest of the auction suite: the matching is part of the
demo's credibility (who won, why, and what they paid), so these never go red.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.auction.domain.affinity import (
    MatchKind,
    RelatedSegments,
    SegmentAffinity,
    SegmentLink,
)
from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.opportunity import CategoryMultiplier, HeldProduct
from app.modules.auction.domain.result import AuctionResult, ExclusionReason, NoFillReason
from app.modules.auction.domain.valuation import SegmentMatch, Valuation, valuate
from app.modules.audience.domain.value_objects.segment import SegmentEstimate, SegmentId
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.campaigns.domain.book import Bidder
from app.modules.common.domain.value_objects.percentage import Percentage
from tests.unit.auction.builders import (
    basket,
    bidder,
    brand,
    campaign,
    moment,
    opportunity,
    segment,
)

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


def test_affinity_never_widens_an_age_restricted_bid_even_toward_a_minor() -> None:
    table = SegmentAffinity.build(
        paths=PATHS,
        related=(RelatedSegments(left=BEER, right=WATER, weight=Decimal("0.6")),),
    )
    brewer = bidder(0, "alcohol", {BEER: "20"}, age_restricted=True)
    result = clear(
        opportunity(moment(segment(WATER, 2), minor_likely=True), brewer, affinity=table)
    )
    # Stricter than a SAFETY exclusion: age-restricted campaigns match exactly only,
    # so a related segment never even produces a bid (valuation.match_segments).
    assert not result.filled
    assert all(bid.campaign_id != brewer.campaign_id for bid in result.bids)


def test_minor_safety_blocks_an_exact_age_restricted_bid() -> None:
    brewer = bidder(0, "alcohol", {BEER: "20"}, age_restricted=True)
    result = clear(opportunity(moment(segment(BEER, 2), minor_likely=True), brewer))
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


# ---------------------------------------------------------------- edge cases


@pytest.mark.parametrize(
    ("parent", "child", "linked"),
    [
        ("Interest | Sports", "Interest | Sports | Cycling", True),
        # Spacing and case never change the hierarchy.
        ("interest|sports", "  Interest |  SPORTS | Cycling ", True),
        # A string prefix is not a path prefix.
        ("Interest | Sport", "Interest | Sports | Cycling", False),
        # Identical paths are not ancestors of each other.
        ("Interest | Sports", "Interest | Sports", False),
        # Different tiers share no ancestry.
        ("Purchase Intent | Sports", "Interest | Sports | Cycling", False),
    ],
)
def test_hierarchy_compares_whole_path_parts(parent: str, child: str, linked: bool) -> None:
    table = SegmentAffinity.build(paths={SPORTS: parent, CYCLING: child})
    assert (CYCLING in reach(table, SPORTS)) is linked
    assert (SPORTS in reach(table, CYCLING)) is linked


def test_deep_hierarchy_compounds_the_weight_per_level() -> None:
    root, a, b, leaf = (SegmentId.create(f"L{index}") for index in range(4))
    table = SegmentAffinity.build(
        paths={root: "R", a: "R | A", b: "R | A | B", leaf: "R | A | B | C"}
    )
    assert reach(table, root)[leaf] == (MatchKind.BROADER, Decimal("0.512"))
    assert reach(table, leaf)[root] == (MatchKind.NARROWER, Decimal("0.064"))
    assert reach(table, a)[b] == (MatchKind.BROADER, Decimal("0.8"))


def test_custom_hierarchy_weights() -> None:
    table = SegmentAffinity.build(
        paths=PATHS, broader_weight=Decimal("0.9"), narrower_weight=Decimal("0.2")
    )
    assert reach(table, SPORTS)[CYCLING] == (MatchKind.BROADER, Decimal("0.9"))
    assert reach(table, CYCLING)[SPORTS] == (MatchKind.NARROWER, Decimal("0.2"))


def test_a_pair_listed_twice_keeps_its_strongest_weight_both_ways() -> None:
    table = SegmentAffinity.build(
        paths={},
        related=(
            RelatedSegments(left=COFFEE, right=WATER, weight=Decimal("0.3")),
            RelatedSegments(left=WATER, right=COFFEE, weight=Decimal("0.6")),
        ),
    )
    assert reach(table, COFFEE)[WATER] == (MatchKind.RELATED, Decimal("0.6"))
    assert reach(table, WATER)[COFFEE] == (MatchKind.RELATED, Decimal("0.6"))


def test_related_pairs_need_no_path() -> None:
    table = SegmentAffinity.build(
        paths={},
        related=(RelatedSegments(left=COFFEE, right=WATER, weight=Decimal("0.5")),),
    )
    assert reach(table, COFFEE)[WATER] == (MatchKind.RELATED, Decimal("0.5"))


def _valuation(
    bidder_: Bidder, *segments: SegmentEstimate, looking: int | None = None, **changes: object
) -> Valuation:
    base = opportunity(moment(*segments, looking=looking), bidder_, affinity=AFFINITY)
    return valuate(bidder_, replace(base, **changes))  # type: ignore[arg-type]


def test_relevance_boosts_lift_an_approximate_value_but_never_its_ceiling() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10"}, partners=(1,))
    plain = _valuation(fan, segment(CYCLING, 2))
    spoken = _valuation(fan, segment(CYCLING, 2, speech_intent=100))
    context = _valuation(
        fan,
        segment(CYCLING, 2),
        category_multipliers=(CategoryMultiplier(category="bikes", multiplier=Decimal(2)),),
    )
    held = _valuation(
        fan,
        segment(CYCLING, 2),
        held_products=(HeldProduct(brand_id=brand(1), confidence=Percentage.create(100)),),
        approved_baskets=frozenset({basket(0, 1)}),
    )
    assert plain.value == 16 and plain.ceiling_rate == 8
    assert spoken.value == 24  # 8 · 1.5 speech boost · 2 viewers
    assert context.value == 32
    assert held.value == 24  # + 50 % complete-the-basket boost
    assert {spoken.ceiling_rate, context.ceiling_rate, held.ceiling_rate} == {Decimal(8)}


def test_the_live_multiplier_scales_an_approximate_ceiling() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10"}, multiplier="2")
    valuation = _valuation(fan, segment(CYCLING, 2))
    assert (valuation.value, valuation.ceiling_rate) == (32, 16)


def test_parent_and_child_bids_each_take_their_best_route_and_stay_capped() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10", CYCLING: "5"})
    valuation = _valuation(fan, segment(SPORTS, 1), segment(CYCLING, 1), looking=2)
    routes = {(m.moment_segment, m.bid_segment, m.kind) for m in valuation.matches}
    assert routes == {(SPORTS, SPORTS, MatchKind.EXACT), (CYCLING, SPORTS, MatchKind.BROADER)}
    assert valuation.value == 18  # 10 + 8, under the 10 · 2 looking cap
    assert valuation.ceiling_rate == 10


def test_overlapping_segments_never_count_a_viewer_twice() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10", CYCLING: "10"})
    valuation = _valuation(fan, segment(SPORTS, 2), segment(CYCLING, 2))
    assert valuation.value == 20  # capped at the best rate × 2 looking


def test_budget_exposure_uses_the_discounted_ceiling() -> None:
    # One play exposes ceiling × looking × 15 s: 8 · 1 · 15 = 120 at the 0.8 broader weight.
    def run(budget: int) -> AuctionResult:
        fan = bidder(0, "bikes", {SPORTS: "10"}, budget=budget)
        return clear(opportunity(moment(segment(CYCLING, 1)), fan, affinity=AFFINITY))

    assert run(120).filled
    starved = run(119)
    assert not starved.filled
    excluded = starved.exclusion_for(campaign(0))
    assert excluded is not None and excluded.reason is ExclusionReason.BUDGET


def test_a_foreign_currency_bid_reached_by_affinity_is_excluded_not_crashed() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10"}, currency="USD")
    result = clear(opportunity(moment(segment(CYCLING, 1)), fan, affinity=AFFINITY))
    excluded = result.exclusion_for(campaign(0))
    assert excluded is not None and excluded.reason is ExclusionReason.INVALID_BID
    assert not result.filled


def test_an_age_restricted_campaign_matches_exactly_even_when_adults_are_present() -> None:
    table = SegmentAffinity.build(
        paths=PATHS,
        related=(RelatedSegments(left=BEER, right=WATER, weight=Decimal("0.9")),),
    )
    brewer = bidder(0, "alcohol", {BEER: "20"}, age_restricted=True)
    result = clear(opportunity(moment(segment(WATER, 2)), brewer, affinity=table))
    assert result.bids == () and not result.filled
    beer_drinkers = clear(opportunity(moment(segment(BEER, 2)), brewer, affinity=table))
    assert beer_drinkers.winner_ids == {brewer.campaign_id}


def test_partners_reaching_the_same_viewers_by_affinity_keep_only_their_share() -> None:
    # Both reach the cyclists (one exactly, one broadly): a duo adds nobody, so the
    # slots keep screen share, not efficiency, and the solo wins (auction plan D1).
    fan = bidder(0, "bikes", {SPORTS: "10"}, partners=(1,))
    shop = bidder(1, "repairs", {CYCLING: "6"}, partners=(0,))
    result = clear(
        opportunity(
            moment(segment(CYCLING, 2)), fan, shop, baskets=(basket(0, 1),), affinity=AFFINITY
        )
    )
    assert result.layout is not None and result.layout.name == "solo"
    assert result.winner_ids == {fan.campaign_id}


def test_nobody_looking_sells_nothing_whatever_the_table() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10"})
    result = clear(opportunity(moment(segment(CYCLING, 0), looking=0), fan, affinity=AFFINITY))
    assert result.no_fill is NoFillReason.NO_AUDIENCE and result.coverage == 0


def test_low_confidence_scales_an_approximate_value_linearly() -> None:
    fan = bidder(0, "bikes", {SPORTS: "10"})
    assert _valuation(fan, segment(CYCLING, 2, confidence=50)).value == 8
