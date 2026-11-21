"""The console's "Canastas evaluadas": the best baskets the auction weighed, explained."""

from __future__ import annotations

from app.modules.auction.domain.clearing import ALTERNATIVES_SHOWN, clear
from tests.unit.auction.builders import (
    CAFE,
    COFFEE,
    DEMO_BASKETS,
    FITNESS,
    RAYO,
    VOLTIO,
    campaign,
    demo_bidders,
    moment,
    opportunity,
    segment,
)

CO_BRAND = moment(segment(FITNESS), segment(COFFEE), looking=2)


def test_the_winning_basket_comes_first_with_its_value() -> None:
    result = clear(opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS))
    first = result.alternatives[0]
    assert set(first.campaign_ids) == {w.campaign_id for w in result.winners}
    assert first.value == result.bundle_value


def test_alternatives_are_best_first_unique_and_never_pair_competitors() -> None:
    result = clear(opportunity(CO_BRAND, *demo_bidders(), baskets=DEMO_BASKETS))
    values = [option.value.amount for option in result.alternatives]
    assert values == sorted(values, reverse=True)
    sets = [frozenset(option.campaign_ids) for option in result.alternatives]
    assert len(sets) == len(set(sets)) <= ALTERNATIVES_SHOWN
    assert frozenset({campaign(VOLTIO), campaign(RAYO)}) not in sets
    # The best single brand is among them: it is the "vs. una marca" baseline.
    assert frozenset({campaign(VOLTIO)}) in sets
    assert frozenset({campaign(RAYO), campaign(CAFE)}) in sets


def test_no_audience_means_no_alternatives() -> None:
    nobody = moment(looking=0)
    assert clear(opportunity(nobody, *demo_bidders(), baskets=DEMO_BASKETS)).alternatives == ()
