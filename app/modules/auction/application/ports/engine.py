"""The seam between the application and the algorithm that decides (R-E1).

Synchronous and IO-free: it runs on the hot path. A remote engine may only
implement this behind a timeout with the in-process engine as fallback (R-E4).
"""

from typing import Protocol

from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import AuctionResult


class AuctionEngine(Protocol):
    def decide(self, opportunity: AdOpportunity) -> AuctionResult: ...
