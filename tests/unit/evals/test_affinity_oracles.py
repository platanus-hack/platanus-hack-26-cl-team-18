"""Eval oracles and scenario checks for segment affinity (evals/metrics.py, evals/harness.py).

The oracles re-derive affinity rules from the wire, so a regression in the
engine's own bookkeeping still fails `make eval`.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from evals.harness import _check
from evals.metrics import invariant_violations, product_kpis
from evals.scenario import Expect, load_scenarios, parse_scenario

from app.contract.messages import BidLine, BidMatch, SegmentMatchKind, Winner
from tests.unit.evals.test_metrics import _clp, decision


def _match(kind: str, bid: str, seen: str, weight: float, **labels: str) -> BidMatch:
    return BidMatch(
        segment_id=seen,
        bid_segment_id=bid,
        kind=SegmentMatchKind(kind),
        weight=weight,
        segment_label=labels.get("seen_label", seen),
        bid_segment_label=labels.get("bid_label", bid),
    )


def _solo(
    matches: list[BidMatch],
    *,
    category: str = "coffee",
    bid: str = "2",
    price: str = "1.5",
    multiplier: str | None = "1",
):  # type: ignore[no-untyped-def]
    d = decision([("c1", "Café Patio", category)])
    assert d.auction is not None
    line = BidLine(
        campaign_id="c1",
        brand_name="Café Patio",
        category=category,
        bid_per_as=_clp(bid),
        value=1,
        multiplier=multiplier,
        matches=matches,
    )
    auction = d.auction.model_copy(
        update={
            "bids": [line],
            "winners": [
                Winner(campaign_id="c1", brand_name="Café Patio", price_per_as=_clp(price))
            ],
        }
    )
    return replace(d, auction=auction)


def kinds(d) -> list[str]:  # type: ignore[no-untyped-def]
    return [violation.kind for violation in invariant_violations([d])]


def test_well_formed_exact_and_related_matches_pass() -> None:
    d = _solo([_match("exact", "977", "977", 1.0), _match("related", "977", "1045", 0.4)])
    assert kinds(d) == []


@pytest.mark.parametrize(
    ("matches", "expected"),
    [
        ([_match("exact", "977", "1045", 1.0)], "bid_match_inconsistent"),  # exact across segments
        ([_match("related", "977", "977", 0.4)], "bid_match_inconsistent"),  # approximate to itself
        ([_match("exact", "977", "977", 0.5)], "bid_match_inconsistent"),  # exact at a discount
        ([_match("related", "977", "1045", 1.0)], "bid_match_inconsistent"),  # full-price guess
        (
            [_match("exact", "977", "977", 1.0), _match("related", "1045", "977", 0.4)],
            "bid_match_duplicate",
        ),
    ],
)
def test_malformed_matches_are_violations(matches: list[BidMatch], expected: str) -> None:
    assert expected in kinds(_solo(matches))


def test_an_age_restricted_bid_reached_by_affinity_is_a_violation() -> None:
    d = _solo([_match("related", "369", "1000", 0.5)], category="alcohol")
    assert "restricted_reach_widened" in kinds(d)
    assert "restricted_reach_widened" not in kinds(
        _solo([_match("exact", "369", "369", 1.0)], category="alcohol")
    )


@pytest.mark.parametrize(
    ("bid", "multiplier", "price", "violates"),
    [
        ("2", "1", "2", False),
        ("2", "1", "2.0002", True),
        ("2", "1.5", "3", False),
        ("2", None, "2.5", True),  # no multiplier on the wire means 1
    ],
)
def test_a_solo_winner_never_clears_above_its_matched_rate(
    bid: str, multiplier: str | None, price: str, violates: bool
) -> None:
    d = _solo([_match("related", "977", "1045", 0.4)], bid=bid, price=price, multiplier=multiplier)
    assert ("charged_above_matched_bid" in kinds(d)) is violates


def test_affinity_kpis_count_reached_and_affinity_only_bids() -> None:
    reached = _solo([_match("exact", "977", "977", 1.0), _match("related", "977", "1045", 0.4)])
    only = _solo([_match("related", "977", "1045", 0.4)])
    exact = _solo([_match("exact", "977", "977", 1.0)])
    kpis = product_kpis([reached, only, exact])
    assert kpis["affinity_bid_share"] == pytest.approx(2 / 3)
    assert kpis["affinity_only_bids"] == 1
    assert product_kpis([])["affinity_bid_share"] is None


# ---------------------------------------------------------------- scenario checks


def _failed(expect: Expect, d) -> list[str]:  # type: ignore[no-untyped-def]
    return [check.name for check in _check("t", 0, expect, d) if not check.passed]


def test_match_expectations_accept_kind_or_full_route() -> None:
    d = _solo(
        [_match("related", "977", "1045", 0.4, bid_label="Coffee", seen_label="Pastry/Doughnuts")]
    )
    ok = Expect(matches={"Café Patio": ("related", "related:Coffee>Pastry/Doughnuts")})
    assert _failed(ok, d) == []
    wrong = Expect(matches={"Café Patio": ("exact",), "Miga": ("related",)})
    assert _failed(wrong, d) == ["match:Café Patio:exact", "match:Miga:related"]


def test_no_bid_price_and_coverage_expectations() -> None:
    d = _solo([_match("related", "977", "1045", 0.4)], price="1.5")
    assert _failed(Expect(no_bid=frozenset({"Miga"})), d) == []
    assert _failed(Expect(no_bid=frozenset({"Café Patio"})), d) == ["no_bid:Café Patio"]
    assert _failed(Expect(max_price_per_as={"Café Patio": "1.5"}), d) == []
    assert _failed(Expect(max_price_per_as={"Café Patio": "1.4"}), d) == ["price:Café Patio"]
    assert _failed(Expect(max_price_per_as={"Miga": "9"}), d) == ["price:Miga"]
    assert _failed(Expect(max_coverage=1.0), d) == []
    assert _failed(Expect(max_coverage=0.5), d) == ["coverage_max"]


def test_scenario_parser_reads_affinity_keys_and_rejects_typos() -> None:
    step = {
        "scene": {"people": 1},
        "expect": {
            "matches": {"Miga": ["related"]},
            "no_bid": ["Voltio"],
            "max_price_per_as": {"Miga": 4},
            "max_coverage": 0.5,
        },
    }
    (parsed,) = parse_scenario({"name": "x", "steps": [step]}, {}).steps
    assert parsed.expect.matches == {"Miga": ("related",)}
    assert parsed.expect.no_bid == frozenset({"Voltio"})
    assert parsed.expect.max_price_per_as == {"Miga": "4"}
    assert parsed.expect.max_coverage == 0.5
    with pytest.raises(ValueError, match="unknown expectation keys"):
        parse_scenario(
            {"name": "x", "steps": [{"scene": {"people": 1}, "expect": {"match": {}}}]}, {}
        )


def test_segment_affinity_scenario_exists_and_checks_matches() -> None:
    (scenario,) = load_scenarios(only={"segment-affinity"})
    assert any(step.expect.matches for step in scenario.steps)
    assert any(step.expect.no_bid for step in scenario.steps)


def test_probe_scenarios_are_flagged_out_of_the_kpi_mix() -> None:
    scenarios = {scenario.name: scenario for scenario in load_scenarios()}
    assert scenarios["segment-affinity"].kpis is False
    assert scenarios["demo-run"].kpis is True
    parsed = parse_scenario({"name": "x", "kpis": False, "steps": []}, {})
    assert parsed.kpis is False
