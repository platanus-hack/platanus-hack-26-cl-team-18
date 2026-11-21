"""Published campaign-book facts: the bidders the auction prices and the brand kits creative uses."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Self

from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.common.domain.primitives.typed_id import TypedId
from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate


class BrandId(TypedId):
    __slots__ = ()


class CampaignId(TypedId):
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class BidMultiplier(ValueObject):
    """The `/advertiser` live multiplier (bidding war), bounded so one slider can't break the market."""

    value: Decimal

    MIN: ClassVar[Decimal] = Decimal("0.2")
    MAX: ClassVar[Decimal] = Decimal("5.0")

    def __post_init__(self) -> None:
        self._enforce_types()

    def _enforce_types(self) -> None:
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise TypeError("BidMultiplier.value must be a finite Decimal")
        if not self.MIN <= self.value <= self.MAX:
            raise ValueError(f"BidMultiplier must be within [{self.MIN}, {self.MAX}]")

    @classmethod
    def create(cls, value: Decimal | int | str) -> Self:
        if isinstance(value, bool | float):
            raise TypeError("BidMultiplier does not accept bool or float")
        return cls(value=Decimal(value))

    @classmethod
    def neutral(cls) -> Self:
        return cls.create(1)


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentBid:
    """Max price per attention-second for looking viewers in one segment."""

    segment_id: SegmentId
    rate: MoneyRate


@dataclass(frozen=True, slots=True, kw_only=True)
class BasketBonus:
    """Opt-in money a brand adds per attention-second when `partner` shares the screen."""

    partner: BrandId
    rate: MoneyRate


@dataclass(frozen=True, slots=True, kw_only=True)
class Bidder:
    """The auction's immutable view of one campaign at clearing time (CONTEXT.md: Bidder).

    `budget_remaining=None` means uncapped (seed/demo house campaigns only).
    """

    campaign_id: CampaignId
    brand_id: BrandId
    category: str
    age_restricted: bool
    segment_bids: tuple[SegmentBid, ...]
    partners: frozenset[BrandId] = frozenset()
    basket_bonuses: tuple[BasketBonus, ...] = ()
    live_multiplier: BidMultiplier = BidMultiplier(value=Decimal(1))
    active: bool = True
    budget_remaining: Money | None = None

    def __post_init__(self) -> None:
        if not self.segment_bids:
            raise ValueError("A bidder needs at least one segment bid")
        if len({bid.segment_id for bid in self.segment_bids}) != len(self.segment_bids):
            raise ValueError("A bidder bids at most once per segment")
        if self.brand_id in self.partners:
            raise ValueError("A brand cannot be its own partner")
        if any(bonus.partner not in self.partners for bonus in self.basket_bonuses):
            raise ValueError("A basket bonus requires the partner on the allow-list")
        if len({bonus.partner for bonus in self.basket_bonuses}) != len(self.basket_bonuses):
            raise ValueError("A bidder offers at most one basket bonus per partner")

    def rate_for(self, segment_id: SegmentId) -> MoneyRate | None:
        return next((bid.rate for bid in self.segment_bids if bid.segment_id == segment_id), None)

    def bonus_for(self, partner: BrandId) -> MoneyRate | None:
        return next((bonus.rate for bonus in self.basket_bonuses if bonus.partner == partner), None)

    def with_multiplier(self, multiplier: BidMultiplier) -> Bidder:
        return Bidder(
            campaign_id=self.campaign_id,
            brand_id=self.brand_id,
            category=self.category,
            age_restricted=self.age_restricted,
            segment_bids=self.segment_bids,
            partners=self.partners,
            basket_bonuses=self.basket_bonuses,
            live_multiplier=multiplier,
            active=self.active,
            budget_remaining=self.budget_remaining,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandKit:
    brand_id: BrandId
    name: str
    logo_url: str
    stock_copy: str
    accent_color: str
    voice_id: str | None = None  # brand voice for solo creatives; None -> narrator
