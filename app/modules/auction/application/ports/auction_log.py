"""The record of every auction (R-O6). Append-only; never on the hot path's critical wait."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.auction.domain.result import AuctionResult
from app.modules.auction.domain.swap import Trigger
from app.modules.audience import AudienceMomentSnapshot


@dataclass(frozen=True, slots=True, kw_only=True)
class AuctionRecord:
    auction_id: str  # UUIDv7 from the IdGenerator
    screen_id: str  # the wire screen id
    moment: AudienceMomentSnapshot
    trigger: Trigger
    result: AuctionResult
    cleared_at: datetime


class AuctionLog(Protocol):
    def append(self, record: AuctionRecord) -> None: ...
