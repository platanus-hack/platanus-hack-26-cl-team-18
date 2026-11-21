"""Auction log adapters: an in-memory ring (always) and a Postgres write-behind tee."""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from threading import Lock
from typing import Any

from psycopg2.extras import Json

from app.modules.auction.application.ports.auction_log import AuctionRecord
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter

INSERT_AUCTION = """
INSERT INTO auction (
    auction_id, screen_id, moment_id, trigger, pricing, layout, no_fill, currency,
    bundle_value, bundle_revenue, single_brand_value, single_brand_revenue, coverage,
    people, looking, minor_likely, winners, bids, exclusions, moment_changed_at, cleared_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
) ON CONFLICT (auction_id) DO NOTHING
"""


class InMemoryAuctionLog:
    def __init__(self, capacity: int = 200) -> None:
        self._lock = Lock()
        self._records: deque[AuctionRecord] = deque(maxlen=capacity)

    def append(self, record: AuctionRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> tuple[AuctionRecord, ...]:
        with self._lock:
            return tuple(self._records)


class PostgresAuctionLog:
    """Appends each auction as one row; the decision detail goes to jsonb columns."""

    def __init__(self, writer: WriteBehindWriter, *, mirror: InMemoryAuctionLog | None = None):
        self._writer = writer
        self._mirror = mirror or InMemoryAuctionLog()

    def records(self) -> tuple[AuctionRecord, ...]:
        return self._mirror.records()

    def append(self, record: AuctionRecord) -> None:
        self._mirror.append(record)
        self._writer.submit(((INSERT_AUCTION, auction_row(record)),))


def auction_row(record: AuctionRecord) -> tuple[Any, ...]:
    result, moment = record.result, record.moment
    winners = [
        {
            "campaign_id": str(winner.campaign_id),
            "brand_id": str(winner.bidder.brand_id),
            "slot": winner.slot,
            "share": str(winner.share),
            "efficiency": str(winner.efficiency),
            "value_per_s": str(winner.value_per_s.amount),
            "payment_per_s": str(winner.payment_per_s.amount),
            "price_per_as": str(winner.price_per_as.amount),
        }
        for winner in result.winners
    ]
    bids = [
        {
            "campaign_id": str(bid.campaign_id),
            "value_per_s": str(bid.value_per_s.amount),
            "matches": [
                {
                    "segment_id": match.moment_segment.value,
                    "bid_segment_id": match.bid_segment.value,
                    "kind": match.kind.value,
                    "weight": str(match.weight),
                }
                for match in bid.matches
            ],
        }
        for bid in result.bids
    ]
    exclusions = [
        {
            "campaign_id": str(item.campaign_id),
            "reason": item.reason.value,
            "related_campaign_id": None
            if item.related_campaign_id is None
            else str(item.related_campaign_id),
        }
        for item in result.exclusions
    ]
    return (
        record.auction_id,
        record.screen_id,
        str(moment.moment_id),
        record.trigger.value,
        result.pricing.value,
        None if result.layout is None else result.layout.name,
        None if result.no_fill is None else result.no_fill.value,
        str(result.bundle_value.currency),
        result.bundle_value.amount,
        result.bundle_revenue.amount,
        result.single_brand_value.amount,
        result.single_brand_revenue.amount,
        result.coverage.quantize(Decimal("0.000001")),
        moment.people,
        moment.looking,
        moment.minor_likely,
        Json(winners),
        Json(bids),
        Json(exclusions),
        moment.moment_changed_at.value,
        record.cleared_at,
    )
