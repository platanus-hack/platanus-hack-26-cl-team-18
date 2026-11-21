"""Table-driven bundle-auction cases: the demo's credibility (docs/auction-plan.md §7).

Values and payments are CLP per screen-second; prices are CLP per attention-second.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.opportunity import HeldProduct, Layout, PricingRule
from app.modules.auction.domain.result import AuctionResult, ExclusionReason, NoFillReason
from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.value_objects.percentage import Percentage
from tests.unit.auction.builders import (
    BEER,
    CAFE,
    COFFEE,
    DEMO_BASKETS,
    FITNESS,
    MALTA,
    RAYO,
    VOLTIO,
    basket,
    bidder,
    brand,
    campaign,
    demo_bidders,
    moment,
    opportunity,
    segment,
)

CO_BRAND = moment(segment(FITNESS), segment(COFFEE), looking=2)


def amounts(result: AuctionResult) -> dict[int, Decimal]:
    return {_index(w.campaign_id): w.price_per_as.amount for w in result.winners}


def reasons(result: AuctionResult) -> dict[int, ExclusionReason]:
    return {_index(item.campaign_id): item.reason for item in result.exclusions}


def _index(campaign_id: CampaignId) -> int:
    return int(str(campaign_id.value)[-8:])


def winners(result: AuctionResult) -> set[int]:
    return {_index(w.campaign_id) for w in result.winners}


def test_demo_basket_beats_the_solo_on_value_and_revenue() -> None:
    result = clear(opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS))

    assert result.layout is not None and result.layout.name == "duo"
    assert winners(result) == {VOLTIO, CAFE}
    assert result.bundle_value.amount == Decimal("8.6")  # 8·0.70 + 5·0.60
    assert result.single_brand_value.amount == 8
    assert result.bundle_revenue.amount == Decimal("6.95")
    assert result.single_brand_revenue.amount == Decimal("6.5")  # Rayo prices the solo
    assert amounts(result) == {VOLTIO: Decimal("2.275"), CAFE: Decimal("1.2")}
    assert result.coverage == 1
    competitor = result.exclusion_for(campaign(RAYO))
    assert competitor is not None and competitor.reason is ExclusionReason.COMPETITOR
    assert competitor.related_campaign_id == campaign(VOLTIO)


def test_result_is_independent_of_bidder_and_layout_order() -> None:
    base = opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS)
    shuffled = replace(base, bidders=base.bidders[::-1], layouts=base.layouts[::-1])
    assert clear(shuffled) == clear(base)


def test_unpaid_platform_bonus_is_gone_so_a_same_audience_pair_stays_solo() -> None:
    """Two non-competing partners on the same viewers reach nobody new: solo wins."""
    audience = moment(segment(FITNESS))
    result = clear(
        opportunity(
            audience,
            bidder(0, "sports_goods", {FITNESS: "8"}, partners=(1,)),
            bidder(1, "energy_drink", {FITNESS: "5"}, partners=(0,)),
            baskets=(basket(0, 1),),
        )
    )
    assert result.layout is not None and result.layout.name == "solo"
    assert winners(result) == {0}


@pytest.mark.parametrize(
    ("case", "bidders", "baskets", "expected_winners", "loser_reason"),
    [
        (
            "competitors never share a basket, even as mutual partners",
            (
                bidder(0, "sports_goods", {FITNESS: "8"}, partners=(1,)),
                bidder(1, "sports_goods", {COFFEE: "5"}, partners=(0,)),
            ),
            (basket(0, 1),),
            {0},
            ExclusionReason.COMPETITOR,
        ),
        (
            "a partner on one side only is not a basket",
            (
                bidder(0, "sports_goods", {FITNESS: "8"}, partners=(1,)),
                bidder(1, "coffee", {COFFEE: "5"}),
            ),
            (basket(0, 1),),
            {0},
            ExclusionReason.NOT_PARTNER,
        ),
        (
            "mutual partners without an approved basket offer",
            (
                bidder(0, "sports_goods", {FITNESS: "8"}, partners=(1,)),
                bidder(1, "coffee", {COFFEE: "5"}, partners=(0,)),
            ),
            (),
            {0},
            ExclusionReason.NO_BASKET_OFFER,
        ),
    ],
)
def test_compatibility_rules(
    case: str,
    bidders: tuple,  # type: ignore[type-arg]
    baskets: tuple,  # type: ignore[type-arg]
    expected_winners: set[int],
    loser_reason: ExclusionReason,
) -> None:
    result = clear(opportunity(CO_BRAND, *bidders, baskets=baskets))
    assert winners(result) == expected_winners, case
    assert reasons(result) == {1: loser_reason}, case


def test_minor_likely_only_blocks_age_restricted_bids() -> None:
    audience = moment(segment(FITNESS), segment(COFFEE), segment(BEER), looking=3)
    adult = clear(opportunity(audience, *demo_bidders(), baskets=DEMO_BASKETS))
    minor = clear(
        opportunity(replace(audience, minor_likely=True), *demo_bidders(), baskets=DEMO_BASKETS)
    )
    assert reasons(minor)[MALTA] is ExclusionReason.SAFETY
    assert MALTA not in winners(minor)
    # Every other bid is valued exactly as without the flag.
    others = {
        bid.campaign_id: bid.value_per_s for bid in adult.bids if bid.campaign_id != campaign(MALTA)
    }
    assert {
        bid.campaign_id: bid.value_per_s for bid in minor.bids if bid.campaign_id != campaign(MALTA)
    } == others


def test_stock_out_reforms_the_basket() -> None:
    result = clear(
        opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS, out_of_stock=(CAFE,))
    )
    assert winners(result) == {VOLTIO}
    assert reasons(result)[CAFE] is ExclusionReason.OUT_OF_STOCK


def test_budget_must_cover_worst_case_exposure_of_one_play() -> None:
    # Exposure = 8 CLP/AS × 1 looking × 15 s = 120.
    poor = bidder(0, "sports_goods", {FITNESS: "8"}, budget=119)
    rich = bidder(1, "energy_drink", {FITNESS: "6"}, budget=120)
    result = clear(opportunity(moment(segment(FITNESS)), poor, rich))
    assert reasons(result)[0] is ExclusionReason.BUDGET
    assert winners(result) == {1}


def test_inactive_and_foreign_currency_bids_are_excluded_not_crashed() -> None:
    result = clear(
        opportunity(
            moment(segment(FITNESS)),
            bidder(0, "sports_goods", {FITNESS: "8"}, active=False),
            bidder(1, "energy_drink", {FITNESS: "8"}, currency="USD"),
            bidder(2, "coffee", {FITNESS: "3"}),
        )
    )
    assert reasons(result) == {0: ExclusionReason.INACTIVE, 1: ExclusionReason.INVALID_BID}
    assert winners(result) == {2}


def test_below_reserve_is_a_no_fill_with_a_reason_for_every_bidder() -> None:
    weak = moment(segment(FITNESS, confidence=5))
    result = clear(opportunity(weak, *demo_bidders(), baskets=DEMO_BASKETS))
    assert not result.filled and result.no_fill is NoFillReason.BELOW_RESERVE
    assert reasons(result) == {
        VOLTIO: ExclusionReason.BELOW_RESERVE,
        RAYO: ExclusionReason.BELOW_RESERVE,
    }


def test_nobody_looking_sells_nothing() -> None:
    result = clear(opportunity(moment(looking=0), *demo_bidders(), baskets=DEMO_BASKETS))
    assert not result.filled and result.no_fill is NoFillReason.NO_AUDIENCE
    assert result.exclusions == () and result.bundle_revenue.amount == 0


@pytest.mark.parametrize(("multiplier", "winner"), [("1.2", VOLTIO), ("1.3", RAYO)])
def test_bidding_war_flips_at_the_predicted_threshold(multiplier: str, winner: int) -> None:
    # Solo fitness moment: Rayo needs 8 / 6.5 ≈ ×1.231 to beat Voltio.
    result = clear(opportunity(moment(segment(FITNESS)), *demo_bidders(rayo_multiplier=multiplier)))
    assert winners(result) == {winner}


def test_core_adjusted_prices_close_the_gap_plain_vcg_leaves() -> None:
    """Without the Rayo+Café basket, VCG undercuts what Rayo alone would pay."""
    vcg = clear(
        opportunity(CO_BRAND, *demo_bidders(), baskets=(basket(VOLTIO, CAFE),)),
        pricing=PricingRule.VCG,
    )
    core = clear(opportunity(CO_BRAND, *demo_bidders(), baskets=(basket(VOLTIO, CAFE),)))
    assert winners(vcg) == winners(core) == {VOLTIO, CAFE}
    assert vcg.bundle_revenue.amount == Decimal("5.9")  # 3.5 + 2.4 < Rayo's 6.5
    assert core.bundle_revenue.amount == Decimal("6.5")  # no losing bidder would pay more
    for winner in core.winners:
        assert winner.payment_per_s.amount <= winner.value_per_s.amount


def test_basket_bonus_is_attributed_to_the_bidder_that_offers_it() -> None:
    """A bonus is real money in the offering bidder's own value. VCG credits the extra
    welfare to the partner that creates it, so it lowers the partner's price. It is
    not a revenue lever (docs/auction-plan.md R-P2)."""
    plain = clear(opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS))
    _voltio, cafe, *rest = demo_bidders()
    generous = bidder(VOLTIO, "sports_goods", {FITNESS: "8"}, partners=(CAFE,), bonuses={CAFE: "1"})
    bonus = clear(opportunity(CO_BRAND, generous, cafe, *rest, baskets=DEMO_BASKETS))
    assert bonus.bundle_value.amount == plain.bundle_value.amount + 2  # 1 CLP/AS × 2 looking
    own = {_index(w.campaign_id): w.value_per_s.amount for w in bonus.winners}
    assert own == {VOLTIO: Decimal("7.6"), CAFE: Decimal("3")}  # 5.6 + bonus 2; 5 × 0.60
    for winner in bonus.winners:
        assert winner.payment_per_s.amount <= winner.value_per_s.amount
    # Core pricing still keeps revenue at what Rayo alone would pay.
    assert bonus.bundle_revenue.amount >= bonus.single_brand_revenue.amount


def test_held_product_boosts_its_approved_partner() -> None:
    audience = moment(segment(FITNESS), segment(COFFEE), looking=2)
    base = opportunity(audience, *demo_bidders(), baskets=DEMO_BASKETS)
    held = replace(
        base, held_products=(HeldProduct(brand_id=brand(VOLTIO), confidence=Percentage.create(80)),)
    )
    plain = {b.campaign_id: b.value_per_s.amount for b in clear(base).bids}
    boosted = {b.campaign_id: b.value_per_s.amount for b in clear(held).bids}
    assert boosted[campaign(CAFE)] == plain[campaign(CAFE)] * Decimal("1.4")  # +0.5 × 80 %
    assert boosted[campaign(VOLTIO)] == plain[campaign(VOLTIO)]  # own brand: no boost


@pytest.mark.parametrize(
    ("rayo_multiplier", "expected"),
    [("1", {VOLTIO, CAFE}), ("1.5", {RAYO, CAFE})],
)
def test_a_held_product_keeps_its_brand_in_the_basket_through_a_bidding_war(
    rayo_multiplier: str, expected: set[int]
) -> None:
    """Demo beats 3 → 4: a Café Patio cup in hand, Rayo outbids Voltio. Rayo takes
    Voltio's seat *beside* Café Patio; the held brand is never crowded off its own
    "complete the basket" moment by its partner's solo ad."""
    audience = moment(segment(FITNESS, viewers=3), segment(COFFEE, viewers=3), looking=3)
    held = replace(
        opportunity(audience, *demo_bidders(rayo_multiplier=rayo_multiplier), baskets=DEMO_BASKETS),
        held_products=(HeldProduct(brand_id=brand(CAFE), confidence=Percentage.create(95)),),
    )
    result = clear(held)
    assert winners(result) == expected
    assert result.coverage == 1
    for winner in result.winners:  # relevance never charges above the bid
        assert winner.payment_per_s.amount <= winner.value_per_s.amount


def test_the_held_brand_is_not_boosted_alone() -> None:
    """The held brand's own lift needs its partner on screen: no solo self-promotion."""
    audience = moment(segment(COFFEE), looking=1)
    cafe = bidder(CAFE, "coffee", {COFFEE: "5"}, partners=(VOLTIO,))
    base = opportunity(audience, cafe)
    held = replace(
        base, held_products=(HeldProduct(brand_id=brand(CAFE), confidence=Percentage.create(95)),)
    )
    assert clear(held).bundle_value == clear(base).bundle_value


def test_speech_intent_boost_is_capped_by_the_looking_viewers() -> None:
    loud = moment(segment(COFFEE, confidence=90, speech_intent=90))
    result = clear(opportunity(loud, bidder(0, "coffee", {COFFEE: "5"})))
    # 5 × 0.9 × (1 + 0.5 × 0.9) = 6.525, never above 5 × 1.45 × 1 looking.
    assert result.bids[0].value_per_s.amount == Decimal("6.525")
    assert result.winners[0].price_per_as.amount <= Decimal("7.25")


def test_trio_layout_fills_when_three_partners_reach_three_audiences() -> None:
    audience = moment(segment(FITNESS), segment(COFFEE), segment(BEER), looking=3)
    result = clear(
        opportunity(
            audience,
            bidder(0, "sports_goods", {FITNESS: "8"}, partners=(1, 2)),
            bidder(1, "coffee", {COFFEE: "6"}, partners=(0, 2)),
            bidder(2, "pastry", {BEER: "5"}, partners=(0, 1)),
            baskets=(basket(0, 1, 2),),
            layouts=(Layout.solo(), Layout.duo(), Layout.trio()),
        )
    )
    assert result.layout is not None and result.layout.name == "trio"
    assert result.coverage == 1
    assert result.bundle_value.amount > result.single_brand_value.amount
    # Without in-category rivals nobody competes inside the basket, so revenue can
    # fall below the solo auction's (docs/auction-plan.md §2.1). That is reported, not hidden.
    assert result.bundle_revenue.amount < result.single_brand_revenue.amount


# ---------------------------------------------------------------- audit regressions


def test_relevance_boosts_never_charge_above_the_bid() -> None:
    """A loud 'tengo energía' raises Voltio's value, not its price above its CLP 8 bid."""
    loud = moment(segment(FITNESS, viewers=2, speech_intent=100), looking=2)
    result = clear(
        opportunity(
            loud,
            bidder(0, "sports_goods", {FITNESS: "8"}),
            bidder(1, "energy_drink", {FITNESS: "7.9"}),
        )
    )
    assert winners(result) == {0}
    assert result.winners[0].price_per_as.amount <= Decimal(8)
    assert result.bids[0].value_per_s.amount > 16  # the boost still ranks it


def test_not_a_partner_only_when_a_bigger_layout_could_have_hosted_it() -> None:
    solo_only = clear(
        opportunity(
            CO_BRAND,
            bidder(0, "sports_goods", {FITNESS: "8"}),
            bidder(1, "coffee", {COFFEE: "5"}),
            layouts=(Layout.solo(),),
        )
    )
    assert reasons(solo_only) == {1: ExclusionReason.OUTBID}


def test_a_partner_too_weak_for_its_slot_floor_is_below_reserve_not_outbid() -> None:
    result = clear(
        opportunity(
            CO_BRAND,
            bidder(0, "sports_goods", {FITNESS: "8"}, partners=(1,)),
            bidder(1, "coffee", {COFFEE: "1"}, partners=(0,)),
            baskets=(basket(0, 1),),
        )
    )
    # Café's duo slot is worth 1 × 0.60 = 0.6 < floor 1.5 × 2 × 0.5 = 1.5.
    assert winners(result) == {0}
    assert reasons(result) == {1: ExclusionReason.BELOW_RESERVE}
