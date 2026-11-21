from datetime import UTC, datetime, timedelta

from evals.metrics import Decision, invariant_violations, percentile, product_kpis

from app.contract.messages import (
    BidLine,
    ConsoleAuction,
    CreativeReady,
    CreativeSlot,
    CreativeSpec,
    LatencyTrace,
    MoneyDTO,
    Winner,
)

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def _clp(amount: str) -> MoneyDTO:
    return MoneyDTO(amount=amount, currency="CLP")


def decision(
    winners: list[tuple[str, str, str]],
    *,
    minor: bool = False,
    screen: list[str] | None = None,
    bundle: str = "120",
    solo: str = "100",
) -> Decision:
    """winners: (campaign_id, brand, category)."""
    shown = screen if screen is not None else [brand for _, brand, _ in winners]
    trace = LatencyTrace(received_at=T0, cleared_at=T0, ready_at=T0 + timedelta(milliseconds=2))
    return Decision(
        source="t",
        step=0,
        minor_likely=minor,
        creative=CreativeReady(
            screen_id="demo-01",
            creative_id="c1",
            auction_id="a1" if shown else None,
            spec=CreativeSpec(
                layout_id="duo",
                duration_ms=8000,
                slots=[
                    CreativeSlot(brand_id="b", brand_name=name, logo_url="logo.png", copy_text="x")
                    for name in shown or ["house"]
                ],
            ),
            trace=trace,
        ),
        auction=ConsoleAuction(
            screen_id="demo-01",
            auction_id="a1",
            moment_id="m1",
            bids=[
                BidLine(campaign_id=cid, brand_name=b, category=c, bid_per_as=_clp("100"), value=1)
                for cid, b, c in winners
            ],
            exclusions=[],
            winners=[
                Winner(campaign_id=cid, brand_name=b, price_per_as=_clp("35"))
                for cid, b, _ in winners
            ],
            bundle_value=_clp(bundle),
            single_brand_value=_clp(solo),
            bundle_revenue=_clp("70"),
            single_brand_revenue=_clp("56"),
            coverage=1,
            trace=trace,
        ),
        round_trip_ms=3.0,
    )


def test_percentile_interpolates_and_handles_empty() -> None:
    assert percentile([], 95) is None
    assert percentile([10.0], 95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0


def test_clean_basket_has_no_violations_and_reports_lift() -> None:
    d = decision([("c1", "Voltio", "sports_goods"), ("c2", "Café Patio", "coffee")])
    assert invariant_violations([d]) == []
    kpis = product_kpis([d])
    assert kpis["basket_rate"] == 1.0
    assert abs(kpis["basket_value_lift"] - 0.2) < 1e-9  # type: ignore[operator]
    assert abs(kpis["basket_revenue_lift"] - 0.25) < 1e-9  # type: ignore[operator]


def test_oracle_flags_competitors_minors_and_screen_mismatch() -> None:
    competitors = decision([("c1", "Voltio", "energy"), ("c2", "Rayo", "energy")])
    minor_beer = decision([("c3", "Malta Sur", "alcohol")], minor=True)
    mismatch = decision([("c1", "Voltio", "energy")], screen=["Rayo"])
    kinds = {v.kind for v in invariant_violations([competitors, minor_beer, mismatch])}
    assert kinds == {"competitors_together", "minor_targeted", "screen_disagrees_with_auction"}
