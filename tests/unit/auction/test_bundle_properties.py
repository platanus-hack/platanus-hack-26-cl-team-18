"""Property-based invariants of the bundle auction.

Hypothesis generates random campaign books, moments and layouts and checks the
product invariants from CLAUDE.md and docs/auction-plan.md on every one. The
table-driven cases in test_bundle.py prove the demo scenes; these prove the
rules hold for books nobody wrote by hand.

The two gaps from docs/auction-plan.md §2.1 that used to be strict xfails
(price per AS above the bid, the reserve floor above a winner's own value) are
fixed, and their tests now run as plain invariants.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from uuid import UUID

from hypothesis import HealthCheck, example, given, settings, strategies as st

from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.opportunity import AdOpportunity, Layout, PricingRule
from app.modules.auction.domain.result import ExclusionReason
from app.modules.auction.domain.valuation import price_ceiling, valuate
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
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp
from app.modules.screens.domain.value_objects.ids import ScreenId

T0 = UtcTimestamp.create(datetime(2026, 11, 21, tzinfo=UTC))
SEGMENTS = tuple(SegmentId.create(f"SEG_{index}") for index in range(5))
CATEGORIES = ("coffee", "pastry", "energy_drink", "burger", "alcohol")
RESTRICTED = frozenset({"alcohol"})
LAYOUTS = (Layout.solo(), Layout.duo(), Layout.trio())
TOLERANCE = Decimal("0.0001")  # one rate quantum
PROPERTY_SETTINGS = settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


def _uuid(namespace: int, index: int) -> UUID:
    return UUID(f"018f0000-0000-7000-8000-{namespace:04d}{index:08d}")


def _clp(amount: int | str | Decimal) -> MoneyRate:
    return MoneyRate.create(currency="CLP", amount=amount)


rates = st.decimals(min_value=0, max_value=30, places=2, allow_nan=False, allow_infinity=False)


@st.composite
def opportunities(draw: st.DrawFn) -> AdOpportunity:
    looking = draw(st.integers(min_value=0, max_value=6))
    people = looking + draw(st.integers(min_value=0, max_value=3))
    present = draw(st.lists(st.sampled_from(SEGMENTS), unique=True, max_size=len(SEGMENTS)))
    segments = (
        tuple(
            SegmentEstimate.create(
                segment_id=segment_id,
                viewer_count=draw(st.integers(min_value=1, max_value=looking)),
                confidence=Percentage.create(draw(st.integers(min_value=1, max_value=100))),
                sources=frozenset({SegmentSource.VISION}),
            )
            for segment_id in present
        )
        if looking
        else ()
    )
    brand_count = draw(st.integers(min_value=0, max_value=7))
    brands = tuple(BrandId.create(_uuid(1, index)) for index in range(brand_count))
    bidders = []
    for index, brand in enumerate(brands):
        partners = (
            draw(st.frozensets(st.sampled_from(brands), max_size=brand_count)) - {brand}
            if brands
            else frozenset()
        )
        bonus_partners = (
            draw(st.frozensets(st.sampled_from(sorted(partners, key=str)), max_size=2))
            if partners
            else frozenset()
        )
        category = draw(st.sampled_from(CATEGORIES))
        bid_segments = draw(
            st.lists(st.sampled_from(SEGMENTS), unique=True, min_size=1, max_size=2)
        )
        bidders.append(
            Bidder(
                campaign_id=CampaignId.create(_uuid(2, index)),
                brand_id=brand,
                category=category,
                age_restricted=category in RESTRICTED,
                segment_bids=tuple(
                    SegmentBid(segment_id=segment_id, rate=_clp(draw(rates)))
                    for segment_id in bid_segments
                ),
                partners=partners,
                basket_bonuses=tuple(
                    BasketBonus(partner=partner, rate=_clp(draw(rates) / 10))
                    for partner in sorted(bonus_partners, key=str)
                ),
                live_multiplier=BidMultiplier.create(
                    draw(st.sampled_from(("0.2", "0.5", "1", "1.5", "5")))
                ),
            )
        )
    layouts = tuple(
        draw(st.lists(st.sampled_from(LAYOUTS), unique_by=lambda item: item.name, min_size=1))
    )
    pairs = [frozenset(pair) for pair in combinations(brands, 2)]
    approved = draw(st.frozensets(st.sampled_from(pairs))) if pairs else frozenset()
    return AdOpportunity(
        moment=AudienceMomentSnapshot.create(
            screen_id=ScreenId.create(_uuid(3, 0)),
            moment_id=MomentId.create(_uuid(4, 0)),
            measured_at=T0,
            received_at=T0,
            moment_changed_at=T0,
            people=people,
            looking=looking,
            segments=segments,
            minor_likely=draw(st.booleans()),
        ),
        bidders=tuple(bidders),
        layouts=layouts,
        reserve=_clp(draw(st.decimals(min_value=0, max_value=5, places=1))),
        approved_baskets=approved,
    )


def reserve_floor_case() -> AdOpportunity:
    """Two partners at CLP 15/AS: each duo slot is worth less than its reserve floor."""
    left, right = (BrandId.create(_uuid(1, index)) for index in range(2))
    segments = tuple(
        SegmentEstimate.create(
            segment_id=segment_id,
            viewer_count=1,
            confidence=Percentage.create(100),
            sources=frozenset({SegmentSource.VISION}),
        )
        for segment_id in SEGMENTS[:2]
    )
    return AdOpportunity(
        moment=AudienceMomentSnapshot.create(
            screen_id=ScreenId.create(_uuid(3, 0)),
            moment_id=MomentId.create(_uuid(4, 0)),
            measured_at=T0,
            received_at=T0,
            moment_changed_at=T0,
            people=2,
            looking=2,
            segments=segments,
            minor_likely=False,
        ),
        bidders=tuple(
            Bidder(
                campaign_id=CampaignId.create(_uuid(2, index)),
                brand_id=brand,
                category=CATEGORIES[index],
                age_restricted=False,
                segment_bids=(SegmentBid(segment_id=SEGMENTS[index], rate=_clp(15)),),
                partners=frozenset({partner}),
            )
            for index, (brand, partner) in enumerate(((left, right), (right, left)))
        ),
        layouts=LAYOUTS[:2],
        reserve=_clp(10),
        approved_baskets=frozenset({frozenset({left, right})}),
    )


def _matches(opportunity: AdOpportunity, bidder: Bidder) -> bool:
    present = {segment.segment_id for segment in opportunity.moment.segments}
    return any(bid.segment_id in present for bid in bidder.segment_bids)


@PROPERTY_SETTINGS
@given(opportunities(), st.randoms(use_true_random=False))
def test_result_is_independent_of_bidder_order(opportunity: AdOpportunity, rng) -> None:  # type: ignore[no-untyped-def]
    shuffled = list(opportunity.bidders)
    rng.shuffle(shuffled)
    assert clear(replace(opportunity, bidders=tuple(shuffled))) == clear(opportunity)


@PROPERTY_SETTINGS
@given(opportunities())
def test_winners_are_never_competitors_and_always_approved_mutual_partners(
    opportunity: AdOpportunity,
) -> None:
    winners = [winner.bidder for winner in clear(opportunity).winners]
    assert len({bidder.category for bidder in winners}) == len(winners)
    assert len({bidder.brand_id for bidder in winners}) == len(winners)
    for left, right in combinations(winners, 2):
        assert left.brand_id in right.partners and right.brand_id in left.partners
        assert frozenset({left.brand_id, right.brand_id}) in opportunity.approved_baskets


@PROPERTY_SETTINGS
@given(opportunities())
def test_minor_likely_blocks_and_records_every_age_restricted_match(
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
        if bidder.age_restricted and _matches(opportunity, bidder):
            assert bidder.campaign_id in safety


@PROPERTY_SETTINGS
@given(opportunities())
def test_winners_target_a_present_segment_and_fit_a_supported_layout(
    opportunity: AdOpportunity,
) -> None:
    result = clear(opportunity)
    if result.layout is None:
        assert result.winners == ()
        return
    assert result.layout in opportunity.layouts
    assert len(result.winners) == result.layout.slots
    for winner in result.winners:
        assert _matches(opportunity, winner.bidder)


@PROPERTY_SETTINGS
@given(opportunities())
def test_fill_clears_reserve_and_never_loses_to_the_best_solo(
    opportunity: AdOpportunity,
) -> None:
    result = clear(opportunity)
    if result.layout is None:
        assert result.bundle_value.amount == 0
        return
    looking = opportunity.moment.looking
    assert result.bundle_value.amount >= opportunity.reserve.amount * looking - TOLERANCE
    if any(layout.slots == 1 for layout in opportunity.layouts):
        # The counterfactual is hypothetical; it only bounds a screen that offers solo.
        assert result.bundle_value.amount >= result.single_brand_value.amount
    for winner in result.winners:
        assert winner.price_per_as.amount >= opportunity.reserve.amount * winner.share - TOLERANCE


@PROPERTY_SETTINGS
@given(opportunities())
def test_zero_looking_viewers_never_sells_the_screen(opportunity: AdOpportunity) -> None:
    moment = replace(opportunity.moment, looking=0, segments=())
    result = clear(replace(opportunity, moment=moment))
    assert result.layout is None and result.winners == ()


@PROPERTY_SETTINGS
@given(opportunities())
def test_price_per_attention_second_never_exceeds_the_bid(opportunity: AdOpportunity) -> None:
    """R-P5/R-P6: a segment bid is a contractual maximum per AS. Relevance boosts
    (speech, context, held product) never raise the charge; only the advertiser's
    own live multiplier and its basket bonuses for partners on screen do."""
    result = clear(opportunity)
    on_screen = frozenset(winner.bidder.brand_id for winner in result.winners)
    for winner in result.winners:
        bidder = winner.bidder
        present = {segment.segment_id for segment in opportunity.moment.segments}
        raw_bid = max(bid.rate.amount for bid in bidder.segment_bids if bid.segment_id in present)
        bonuses = sum(
            (b.rate.amount for b in bidder.basket_bonuses if b.partner in on_screen), Decimal(0)
        )
        ceiling = raw_bid * bidder.live_multiplier.value + bonuses
        assert ceiling == price_ceiling(bidder, valuate(bidder, opportunity), on_screen)
        assert winner.price_per_as.amount <= ceiling + TOLERANCE


@PROPERTY_SETTINGS
@given(opportunities())
@example(reserve_floor_case())
def test_no_winner_pays_more_than_its_own_slot_value(opportunity: AdOpportunity) -> None:
    """R-P4: individual rationality, including the per-slot reserve floor."""
    for winner in clear(opportunity).winners:
        assert winner.payment_per_s.amount <= winner.value_per_s.amount + TOLERANCE


@PROPERTY_SETTINGS
@given(opportunities())
def test_core_prices_beat_every_losing_solo_bidder(opportunity: AdOpportunity) -> None:
    """No single losing bidder would have paid more for the whole screen than the basket did."""
    if not any(layout.slots == 1 for layout in opportunity.layouts):
        return
    result = clear(opportunity, pricing=PricingRule.CORE_ADJUSTED)
    if not result.filled:
        return
    reserve_total = opportunity.reserve.amount * opportunity.moment.looking
    eligible_losers = {
        bid.campaign_id: bid.value_per_s.amount
        for bid in result.bids
        if bid.campaign_id not in result.winner_ids
        and (exclusion := result.exclusion_for(bid.campaign_id)) is not None
        and exclusion.reason
        in {
            ExclusionReason.OUTBID,
            ExclusionReason.COMPETITOR,
            ExclusionReason.NOT_PARTNER,
            ExclusionReason.NO_BASKET_OFFER,
        }
    }
    for value in eligible_losers.values():
        if value >= reserve_total:
            assert result.bundle_revenue.amount >= value - TOLERANCE * 4


@PROPERTY_SETTINGS
@given(opportunities())
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
