"""Approved basket offers sized to the group (architecture §6.1, invariant 1).

Each approved pairing (or solo brand) defines offers by minimum party size,
e.g. 1 → "Combo", 2 → "Dúo", 3 → "Trío", 4 → "Pack x4". The creative takes the
largest tier whose `min_party` fits the people looking. The label, text and
price come from the brands; AI copy may phrase around them but never changes
a price (the copy guard enforces it).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.modules.campaigns.domain.book import BrandId
from app.modules.common.domain.value_objects.money import Money

MAX_PARTY = 12


def format_price(price: Money) -> str:
    """Chilean display: `$3.490` (thousands with a dot, no decimals for CLP)."""
    return price.format(thousands_sep=".", decimal_sep=",")


@dataclass(frozen=True, slots=True, kw_only=True)
class BasketOffer:
    brand_ids: frozenset[BrandId]
    min_party: int
    label: str  # "Trío post-entreno"
    text: str  # "3 combos Voltio + Café Patio"
    price: Money
    claims: tuple[str, ...] = ()  # approved claims the copy may use verbatim

    def __post_init__(self) -> None:
        if not self.brand_ids:
            raise ValueError("An offer needs at least one brand")
        if not 1 <= self.min_party <= MAX_PARTY:
            raise ValueError(f"min_party must be within [1, {MAX_PARTY}]")
        if not self.label.strip() or not self.text.strip():
            raise ValueError("An offer needs a label and a text")
        if self.price.amount <= 0:
            raise ValueError("An offer price must be positive")

    @property
    def price_text(self) -> str:
        return format_price(self.price)

    @property
    def headline(self) -> str:
        """The approved line the offer stage leads with, price included."""
        return f"{self.label}: {self.text} por {self.price_text}"


@dataclass(frozen=True, slots=True)
class OfferBook:
    """All approved offers, indexed by basket. Built once at seed time."""

    by_basket: Mapping[frozenset[BrandId], tuple[BasketOffer, ...]]

    @classmethod
    def of(cls, offers: Iterable[BasketOffer]) -> OfferBook:
        grouped: dict[frozenset[BrandId], list[BasketOffer]] = {}
        for offer in offers:
            tiers = grouped.setdefault(offer.brand_ids, [])
            if any(t.min_party == offer.min_party for t in tiers):
                raise ValueError("Two offers for the same basket and party size")
            tiers.append(offer)
        return cls(
            by_basket={
                basket: tuple(sorted(tiers, key=lambda t: t.min_party))
                for basket, tiers in grouped.items()
            }
        )

    def baskets(self) -> frozenset[frozenset[BrandId]]:
        return frozenset(self.by_basket)

    def tiers(self, brand_ids: frozenset[BrandId]) -> tuple[BasketOffer, ...]:
        return self.by_basket.get(brand_ids, ())

    def for_party(self, brand_ids: frozenset[BrandId], party: int) -> BasketOffer | None:
        """The largest tier that fits `party` (at least the smallest tier when someone looks)."""
        tiers = self.tiers(brand_ids)
        if not tiers:
            return None
        fitting = [t for t in tiers if t.min_party <= max(party, 1)]
        return fitting[-1] if fitting else tiers[0]
