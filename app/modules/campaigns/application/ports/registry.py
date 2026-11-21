"""Where campaign identities, budgets and live bids outlive the process.

The catalog (brands, rates, partners, offers) is code: `app/bootstrap/demo_catalog.py`.
The registry is the record of what code can't hold: one stable `CampaignId` and
`BrandId` per brand slug, the budget, what was already spent (the charge ledger),
and the last live multiplier. The hot path never reads it: it is read once at
startup into `LiveCampaignBook`, and multiplier changes are written behind.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.modules.campaigns.domain.book import BidMultiplier, BrandId, CampaignId
from app.modules.common.domain.value_objects.money import Money


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredCampaign:
    slug: str
    brand_id: BrandId
    campaign_id: CampaignId
    budget: Money
    spent: Money
    multiplier: BidMultiplier
    multiplier_seq: int


class CampaignRegistry(Protocol):
    def open(self, slugs: Sequence[str], *, budget: Money) -> dict[str, StoredCampaign]:
        """One campaign per slug: the stored one, or a new one opened with `budget`."""
        ...

    def multiplier_changed(
        self, campaign_id: CampaignId, multiplier: BidMultiplier, seq: int
    ) -> None:
        """Record an applied multiplier. Never blocks; an older seq never overwrites a newer one."""
        ...
