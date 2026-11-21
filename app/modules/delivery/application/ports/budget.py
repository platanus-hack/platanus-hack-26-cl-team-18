"""Delivery asks campaigns what it may actually charge (a brand never pays past its budget)."""

from typing import Protocol

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.value_objects.money import Money


class BudgetGate(Protocol):
    def charge_many(
        self, hold_key: str, amounts: dict[CampaignId, Money]
    ) -> dict[CampaignId, Money]:
        """Cap and book all basket charges atomically; return each amount actually booked."""
        ...
