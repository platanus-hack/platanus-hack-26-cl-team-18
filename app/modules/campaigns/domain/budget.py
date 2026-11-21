"""A campaign's money: budget, spent, and in-flight reservations (R-B6/R-B7).

A reservation holds the worst-case exposure of one play while it is on air,
so a re-auction or a second screen can't spend the same money twice. It is
settled by the actual charge (delivery) or expires if no charge ever comes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.primitives.errors import DomainError
from app.modules.common.domain.value_objects.money import Money


class BudgetCurrencyMismatch(DomainError):
    def __init__(self, **_: Any) -> None:
        super().__init__(
            code="campaigns.budget_currency_mismatch",
            msg="Budget, reservations and charges must share one currency.",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Reservation:
    key: str  # what holds it: one on-air creative
    amount: Money
    at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetAccount:
    campaign_id: CampaignId
    budget: Money
    spent: Money
    reservations: tuple[Reservation, ...] = ()

    @classmethod
    def open(cls, campaign_id: CampaignId, budget: Money) -> BudgetAccount:
        return cls(
            campaign_id=campaign_id, budget=budget, spent=Money.zero(currency=budget.currency)
        )

    @property
    def reserved(self) -> Money:
        return sum(
            (item.amount for item in self.reservations), Money.zero(currency=self.budget.currency)
        )

    @property
    def remaining(self) -> Money:
        """Never negative: an overspend shows as zero left, and the bidder drops out."""
        left = self.budget - self.spent - self.reserved
        return left if left.amount > 0 else Money.zero(currency=self.budget.currency)

    def available_for(self, key: str) -> Money:
        """Left to spend if the hold `key` were released: what a charge for it may use."""
        held_elsewhere = sum(
            (item.amount for item in self.reservations if item.key != key),
            Money.zero(currency=self.budget.currency),
        )
        left = self.budget - self.spent - held_elsewhere
        return left if left.amount > 0 else Money.zero(currency=self.budget.currency)

    def capped_charge(self, key: str, charge: Money) -> Money:
        """The charge, capped so spend never exceeds the budget."""
        self._same_currency(charge)
        available = self.available_for(key)
        return charge if charge <= available else available

    def reserve(self, key: str, amount: Money, *, at: datetime) -> BudgetAccount:
        """Hold `amount` for `key`. Re-reserving the same key replaces it (idempotent)."""
        self._same_currency(amount)
        kept = tuple(item for item in self.reservations if item.key != key)
        return replace(self, reservations=(*kept, Reservation(key=key, amount=amount, at=at)))

    def settle(self, key: str, charge: Money) -> BudgetAccount:
        """Book the actual charge and drop the reservation it settles."""
        self._same_currency(charge)
        return replace(
            self,
            spent=self.spent + charge,
            reservations=tuple(item for item in self.reservations if item.key != key),
        )

    def release(self, key: str) -> BudgetAccount:
        return replace(
            self, reservations=tuple(item for item in self.reservations if item.key != key)
        )

    def expire(self, *, before: datetime) -> BudgetAccount:
        """Drop reservations whose play never reported a charge (player gone, crash)."""
        kept = tuple(item for item in self.reservations if item.at >= before)
        return self if len(kept) == len(self.reservations) else replace(self, reservations=kept)

    def _same_currency(self, money: Money) -> None:
        if money.currency != self.budget.currency:
            raise BudgetCurrencyMismatch()
