"""DEMO_CATALOG=chile is an uneven market: budgets, core rates and dayparts differ by brand."""

from decimal import Decimal

from app.bootstrap.demo import seed_market
from app.bootstrap.demo_catalog import FICTIONAL
from app.bootstrap.demo_catalog_chile import CHILE
from app.modules.auction.domain.opportunity import CategoryMultiplier
from app.modules.campaigns.infrastructure.registry import InMemoryCampaignRegistry
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.creative.domain.brief import Daypart


def clp(amount: int) -> Money:
    return Money.create(currency="CLP", amount=amount)


def test_a_registry_opens_each_campaign_with_its_own_budget_or_the_shared_one() -> None:
    opened = InMemoryCampaignRegistry(IdGenerator()).open(
        ["big", "plain"], budget=clp(50_000), budgets={"big": clp(150_000)}
    )
    assert opened["big"].budget == clp(150_000)
    assert opened["plain"].budget == clp(50_000)


def test_the_fictional_catalog_stays_flat_so_evals_and_auction_tests_do_not_move() -> None:
    assert FICTIONAL.daypart_boosts == ()
    assert all(brand.budget_clp is None for brand in FICTIONAL.brands)
    assert all(brand.secondary_rate_clp_per_as is None for brand in FICTIONAL.brands)


def test_every_chile_brand_has_a_budget_and_spenders_differ() -> None:
    budgets = {brand.slug: brand.budget_clp for brand in CHILE.brands}
    assert None not in budgets.values()
    assert len(set(budgets.values())) >= 10
    # The house always fills; the small water brand paces out during a demo.
    assert max(budgets, key=lambda slug: budgets[slug] or 0) == "pronto-copec"
    assert min(budgets, key=lambda slug: budgets[slug] or 0) == "benedictino"


def test_seeded_bidders_carry_their_budget_and_pay_less_outside_their_core_audience() -> None:
    market = seed_market(ids=IdGenerator(), budget_clp=50_000, catalog=CHILE)
    slug_of = {
        kit.brand_id: brand.slug for kit, brand in zip(market.kits, CHILE.brands, strict=True)
    }
    bidders = {slug_of[bidder.brand_id]: bidder for bidder in market.bidders}

    assert bidders["coca-cola"].budget_remaining == clp(150_000)
    assert bidders["benedictino"].budget_remaining == clp(4_000)
    core, other = (bid.rate.amount for bid in bidders["red-bull"].segment_bids)
    assert core == Decimal(7) and other == Decimal("5.5")
    # The challenger bids its core rate everywhere (the night-shift hero basket).
    assert {bid.rate.amount for bid in bidders["score"].segment_bids} == {Decimal(8)}


def test_highway_dayparts_boost_coffee_and_energy_at_night_and_cold_drinks_in_the_heat() -> None:
    night = dict(CHILE.boosts_for(Daypart.NIGHT.value))
    afternoon = dict(CHILE.boosts_for(Daypart.AFTERNOON.value))
    assert night["energy_drink"] > 1 and night["coffee"] > 1
    assert afternoon["water"] > 1 and afternoon["ice_cream"] > 1
    assert afternoon["coffee"] < 1


def test_every_daypart_boost_is_a_valid_auction_multiplier_and_names_a_real_category() -> None:
    categories = {brand.category for brand in CHILE.brands}
    for daypart in Daypart:
        pairs = CHILE.boosts_for(daypart.value)
        assert len({category for category, _ in pairs}) == len(pairs)
        for category, multiplier in pairs:
            assert category in categories
            CategoryMultiplier(category=category, multiplier=multiplier)  # range-checked


def test_some_chile_baskets_are_named_packs_not_the_generic_combo_ladder() -> None:
    labels = {basket.names: [tier[1] for tier in basket.tiers] for basket in CHILE.baskets}
    assert labels["Cachantun + Marco Polo"][:2] == ["Pack ruta", "Pack ruta para 2"]
    assert labels["Lay's + Pepsi"][0] == "Pack carrete"
    # The hero basket keeps the label its demo beat and evals name.
    assert labels["Score + Juan Valdez"][1] == "Dúo"
