"""The single input to an auction: bidders with money plus the live context of one screen."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.modules.audience import AudienceMomentSnapshot
from app.modules.campaigns.domain.book import Bidder, BrandId, CampaignId
from app.modules.common.domain.value_objects.money_rate import MoneyRate
from app.modules.common.domain.value_objects.percentage import Percentage

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    """A creative template: screen `shares` (area) and slot `efficiencies` (value kept).

    Efficiency is the fraction of a brand's solo value that its slot still
    delivers to its own relevant viewers. Efficiencies may sum to more than one.
    That is the basket thesis: a half-screen coffee offer still reaches the coffee
    lover, while the other half reaches someone the solo ad would have missed
    (docs/auction-plan.md D1).
    """

    name: str
    shares: tuple[Decimal, ...]
    efficiencies: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.shares) <= 3 or any(
            not share.is_finite() or share <= ZERO for share in self.shares
        ):
            raise ValueError("A layout requires one to three positive slot shares")
        if sum(self.shares, ZERO) != ONE:
            raise ValueError("Layout slot shares must add up to one")
        if tuple(sorted(self.shares, reverse=True)) != self.shares:
            raise ValueError("Layout slot shares must be in descending order")
        if len(self.efficiencies) != len(self.shares) or any(
            not value.is_finite() or not ZERO < value <= ONE for value in self.efficiencies
        ):
            raise ValueError("Each slot needs an efficiency in (0, 1]")
        if tuple(sorted(self.efficiencies, reverse=True)) != self.efficiencies:
            raise ValueError("Slot efficiencies must be in descending order")

    @property
    def slots(self) -> int:
        return len(self.shares)

    @classmethod
    def solo(cls) -> Layout:
        return cls(name="solo", shares=(ONE,), efficiencies=(ONE,))

    @classmethod
    def duo(cls) -> Layout:
        return cls(
            name="duo",
            shares=(Decimal("0.5"), Decimal("0.5")),
            efficiencies=(Decimal("0.70"), Decimal("0.60")),
        )

    @classmethod
    def trio(cls) -> Layout:
        return cls(
            name="trio",
            shares=(Decimal("0.4"), Decimal("0.3"), Decimal("0.3")),
            efficiencies=(Decimal("0.55"), Decimal("0.45"), Decimal("0.40")),
        )


class PricingRule(StrEnum):
    VCG = "vcg"
    CORE_ADJUSTED = "core_adjusted"


@dataclass(frozen=True, slots=True, kw_only=True)
class HeldProduct:
    """A branded product someone holds (objects only, never the person)."""

    brand_id: BrandId
    confidence: Percentage


@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryMultiplier:
    """Context multiplier for one category, looked up by the caller from the daypart × weather table."""

    category: str
    multiplier: Decimal

    def __post_init__(self) -> None:
        if not self.multiplier.is_finite() or not Decimal("0.2") <= self.multiplier <= 5:
            raise ValueError("Category multipliers must be within [0.2, 5]")


@dataclass(frozen=True, slots=True, kw_only=True)
class AdOpportunity:
    """Everything the engine may read. Same opportunity gives the same decision."""

    moment: AudienceMomentSnapshot
    bidders: tuple[Bidder, ...]
    layouts: tuple[Layout, ...]
    reserve: MoneyRate  # per attention-second; per-slot floor is reserve × share
    # Pairings with an approved basket offer and loop. Solo needs no entry.
    approved_baskets: frozenset[frozenset[BrandId]] = frozenset()
    out_of_stock: frozenset[BrandId] = frozenset()
    category_multipliers: tuple[CategoryMultiplier, ...] = ()
    held_products: tuple[HeldProduct, ...] = ()
    # Worst-case play length used for budget exposure (R-B6).
    max_play_seconds: Decimal = Decimal(15)
    # The winners on air, so the desk can apply hysteresis on same-moment triggers.
    incumbent: frozenset[CampaignId] | None = None

    def __post_init__(self) -> None:
        if not self.layouts:
            raise ValueError("An opportunity needs at least one layout")
        if len({layout.name for layout in self.layouts}) != len(self.layouts):
            raise ValueError("Layout names must be unique")
        if any(len(basket) < 2 for basket in self.approved_baskets):
            raise ValueError("An approved basket names at least two brands")

    def multiplier_for(self, category: str) -> Decimal:
        return next(
            (item.multiplier for item in self.category_multipliers if item.category == category),
            ONE,
        )

    def basket_approved(self, brands: frozenset[BrandId]) -> bool:
        if len(brands) < 2:
            return True
        return all(
            frozenset(pair) in self.approved_baskets or brands in self.approved_baskets
            for pair in _pairs(brands)
        )


def _pairs(brands: frozenset[BrandId]) -> tuple[tuple[BrandId, BrandId], ...]:
    ordered = sorted(brands, key=lambda brand: brand.value)
    return tuple(
        (left, right) for index, left in enumerate(ordered) for right in ordered[index + 1 :]
    )
