"""A bid that is worth nothing this moment still gets a reason the console can show.

CLAUDE.md: "Record every exclusion with its reason". A matched bidder whose value
is zero (a CLP 0 bid, or its only segment seen at 0 % confidence) appears in
`bids` but can never clear the reserve, so it is `below_reserve`, never silent.
"""

from app.modules.auction.domain.clearing import clear
from app.modules.auction.domain.result import ExclusionReason, NoFillReason
from tests.unit.auction.builders import (
    COFFEE,
    FITNESS,
    bidder,
    campaign,
    moment,
    opportunity,
    segment,
)


def test_a_zero_bid_alone_is_recorded_below_reserve() -> None:
    result = clear(opportunity(moment(segment(FITNESS, 2)), bidder(0, "coffee", {FITNESS: 0})))
    assert [bid.campaign_id for bid in result.bids] == [campaign(0)]
    exclusion = result.exclusion_for(campaign(0))
    assert exclusion is not None and exclusion.reason is ExclusionReason.BELOW_RESERVE
    assert result.no_fill is NoFillReason.BELOW_RESERVE


def test_a_zero_confidence_match_next_to_a_winner_still_gets_a_reason() -> None:
    result = clear(
        opportunity(
            moment(segment(FITNESS, 2), segment(COFFEE, 2, confidence=0)),
            bidder(0, "sports_goods", {FITNESS: 8}),
            bidder(1, "coffee", {COFFEE: 5}),
        )
    )
    assert result.winner_ids == {campaign(0)}
    exclusion = result.exclusion_for(campaign(1))
    assert exclusion is not None and exclusion.reason is ExclusionReason.BELOW_RESERVE


def test_every_bid_that_did_not_win_has_exactly_one_reason() -> None:
    result = clear(
        opportunity(
            moment(segment(FITNESS, 2), segment(COFFEE, 1, confidence=0)),
            bidder(0, "sports_goods", {FITNESS: 8}),
            bidder(1, "coffee", {COFFEE: 5}),
            bidder(2, "snacks", {FITNESS: 0}),
            bidder(3, "sports_goods", {FITNESS: 6}),
        )
    )
    excluded = [item.campaign_id for item in result.exclusions]
    assert len(excluded) == len(set(excluded))
    losers = {bid.campaign_id for bid in result.bids} - result.winner_ids
    assert losers == set(excluded)
