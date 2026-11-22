"""Invariant 1 over the real catalogs: partners only, never rivals, an offer for every group.

Checked on the seeded market (the bidders and the offer book the auction reads),
not only the catalog tuples, so a seeding change cannot drift from the data.
"""

from __future__ import annotations

import re

import pytest

from app.bootstrap.demo_catalog import DemoBasket
from app.modules.creative.domain.guard import party_mentions
from app.modules.creative.domain.lexicon import normalize
from app.modules.creative.domain.offer import MAX_PARTY
from tests.unit.catalog_sweep.markets import CATALOGS, seeded

_COUNT_WORDS = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "duo": 2, "trio": 3}


def _baskets() -> list[tuple[str, DemoBasket]]:
    return [(name, basket) for name in CATALOGS for basket in seeded(name).catalog.baskets]


def _id(case: tuple[str, DemoBasket]) -> str:
    return f"{case[0]}-{'+'.join(sorted(case[1].slugs))}"


@pytest.mark.parametrize("catalog", CATALOGS)
def test_no_basket_pairs_two_brands_of_one_category(catalog: str) -> None:
    market = seeded(catalog)
    for basket in market.market.offers.pairings():
        categories = [market.bidders[b].category for b in basket]
        assert len(set(categories)) == len(categories), market.label(basket)


@pytest.mark.parametrize("catalog", CATALOGS)
def test_partner_allow_lists_are_mutual_and_match_the_approved_baskets(catalog: str) -> None:
    market = seeded(catalog)
    pairings = market.market.offers.pairings()
    for bidder in market.bidders.values():
        for partner in bidder.partners:
            assert bidder.brand_id in market.bidders[partner].partners, (
                market.slug_of[bidder.brand_id],
                market.slug_of[partner],
            )
            assert frozenset({bidder.brand_id, partner}) in pairings
    for basket in pairings:
        assert len(basket) == 2  # ADR 0008 D4: at most two brands
        left, right = tuple(basket)
        assert right in market.bidders[left].partners


@pytest.mark.parametrize("catalog", CATALOGS)
def test_every_brand_sells_alone_and_every_basket_names_its_brands(catalog: str) -> None:
    market = seeded(catalog)
    solos = {next(iter(b)) for b in market.market.offers.baskets() if len(b) == 1}
    assert solos == set(market.bidders)
    names = {brand.slug: brand.name for brand in market.catalog.brands}
    for basket in market.catalog.baskets:
        for slug in basket.slugs:
            assert names[slug] in basket.names  # the copy guard needs every name verbatim


@pytest.mark.parametrize("case", _baskets(), ids=_id)
def test_tiers_start_at_one_step_by_one_and_grow_in_price(case: tuple[str, DemoBasket]) -> None:
    _, basket = case
    sizes = [party for party, *_ in basket.tiers]
    assert sizes == list(range(1, len(sizes) + 1)), sizes  # a group of N always has a tier
    assert sizes[-1] <= MAX_PARTY
    prices = [price for *_, price in basket.tiers]
    assert prices == sorted(prices) and len(set(prices)) == len(prices), prices


@pytest.mark.parametrize("case", _baskets(), ids=_id)
def test_each_tier_label_and_text_claim_its_own_party_size(case: tuple[str, DemoBasket]) -> None:
    """The 3-tier says "Trío" and " x3": never a label promising another group."""
    catalog, basket = case
    names = [b.name for b in seeded(catalog).catalog.brands if b.slug in basket.slugs]
    for party, label, suffix, _ in basket.tiers:
        claimed = normalize(label)
        for name in names:  # brand names carry numbers of their own ("Super 8")
            claimed = claimed.replace(normalize(name), " ")
        said = party_mentions(claimed) | {int(n) for n in re.findall(r"\bx\s?(\d+)\b", claimed)}
        words = claimed.split()
        if words and words[0] in _COUNT_WORDS:
            said.add(_COUNT_WORDS[words[0]])
        assert said <= {party}, (label, party, said)
        multiplier = {int(n) for n in re.findall(r"x(\d+)", suffix)} or {1}
        assert multiplier == {party}, (suffix, party)
