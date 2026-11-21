"""The default engine: the pure bundle auction, in process (R-E2)."""

from dataclasses import dataclass

from app.modules.auction.domain.clearing import DEFAULT_TOP_K, clear
from app.modules.auction.domain.opportunity import AdOpportunity, PricingRule
from app.modules.auction.domain.result import AuctionResult


@dataclass(frozen=True, slots=True)
class InProcessBundleEngine:
    pricing: PricingRule = PricingRule.CORE_ADJUSTED
    top_k: int = DEFAULT_TOP_K

    def decide(self, opportunity: AdOpportunity) -> AuctionResult:
        return clear(opportunity, pricing=self.pricing, top_k=self.top_k)
