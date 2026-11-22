"""A bid reaches a related segment through the live loop and the console says how.

Café Patio bids on Coffee; a donut on camera tags Pastry/Doughnuts, which the
catalog pairs with Coffee (the breakfast run). The console must show the bid
with its match ("via Coffee → Pastry/Doughnuts · related"), priced at the
discounted rate, and never crash on a bid with no exact segment.
"""

from datetime import timedelta

from tests.unit.api.test_auction_loop_e2e import Loop, loop as loop
from tests.unit.api.test_live_vision_labels import _agent_snapshot


def test_a_related_segment_reaches_the_partner_bid_and_the_console_shows_the_match(
    loop: Loop,
) -> None:
    now = loop.clock.advance(timedelta(milliseconds=100))
    loop.agent.send_text(_agent_snapshot(1, now, {"donut": 0.95}).model_dump_json())
    auction = loop.auction()

    patio = next(bid for bid in auction.bids if bid.brand_name == "Café Patio")
    (match,) = patio.matches
    assert (match.kind, match.bid_segment_label, match.segment_label) == (
        "related",
        "Café",
        "Pastelería",
    )
    assert match.weight == 0.4
    assert patio.bid_per_as.amount == "2"  # 5 CLP/AS · 0.4
