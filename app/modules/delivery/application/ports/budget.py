"""Delivery asks campaigns what it may actually charge (a brand never pays past its budget)."""

from typing import Protocol

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.value_objects.money import Money


class BudgetGate(Protocol):
    def charge(self, hold_key: str, campaign_id: CampaignId, amount: Money) -> Money:
        """Book up to `amount` against the campaign (settling `hold_key`); return what was booked."""
        ...
