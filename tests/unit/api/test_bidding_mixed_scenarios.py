"""Bid requests and mixed operator actions through HTTP and the real screen sockets."""

from datetime import timedelta

import pytest
from scripts.replay_audience import paid_brands

from tests.unit.api.test_auction_loop_e2e import (
    Loop,
    campaign_of,
    loop as loop,
    slide,
    stock,
)


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "", "1e999", "0.1999", "5.0001", "-1", 1, True, None]
)
def test_invalid_bids_return_validation_errors_without_changing_the_book(
    loop: Loop, value: object
) -> None:
    before = loop.client.get("/api/book").json()
    response = loop.client.post(
        "/api/advertiser/bid-multiplier",
        json={
            "campaign_id": campaign_of(loop, "Rayo"),
            "multiplier": value,
            "seq": 1,
        },
    )
    assert response.status_code == 422
    assert loop.client.get("/api/book").json() == before


@pytest.mark.parametrize("value", ["0.2", "5.0"])
def test_inclusive_slider_limits_accept_retries_without_another_auction(
    loop: Loop, value: str
) -> None:
    loop.send("fitness")
    loop.auction(), loop.creative()
    assert slide(loop, "Rayo", value, 1).json() == {"outcome": "applied"}
    loop.auction()
    count = len(loop.container.auction_log.records())
    assert slide(loop, "Rayo", value, 1).json() == {"outcome": "duplicate"}
    assert len(loop.container.auction_log.records()) == count


def test_stock_out_supersedes_a_pending_bid_and_never_replays_the_stale_basket(loop: Loop) -> None:
    loop.send("co-brand")
    loop.auction()
    assert set(paid_brands(loop.creative())) == {"Voltio", "Café Patio"}
    assert slide(loop, "Rayo", "1.5", 1).status_code == 200
    pending = loop.auction()
    assert pending.swap == "held"

    assert stock(loop, "Café Patio", in_stock=False).json() == {"changed": True}
    replacement = loop.auction()
    assert replacement.swap == "swap"
    assert paid_brands(loop.creative()) == ("Rayo",)

    loop.clock.advance(timedelta(seconds=4))
    loop.scheduler.fire_due()
    loop.send("adult")
    loop.auction()
    assert paid_brands(loop.creative()) == ("Malta Sur",)
    loop.send("minor-safe", after_s=0.1)
    safety = loop.auction()
    assert safety.swap == "swap" and safety.winners == []
    assert paid_brands(loop.creative()) == ()
