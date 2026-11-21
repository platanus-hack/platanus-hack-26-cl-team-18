"""Charges: the append-only ledger rows of what each brand pays for one play."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.modules.campaigns.domain.book import BrandId, CampaignId
from app.modules.common.domain.primitives.typed_id import TypedId
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.domain.value_objects.money_rate import MoneyRate


class ChargeId(TypedId):
    __slots__ = ()


class ChargeReason(StrEnum):
    ATTENTION = "attention"
    NO_ATTENTION = "no_attention"  # zero attention is recorded explicitly as a $0 charge
    BUDGET_CAP = "budget_cap"  # the charge was capped at the campaign's budget left


@dataclass(frozen=True, slots=True, kw_only=True)
class BilledSlot:
    """One winner of the auction a creative was composed from, at its cleared price."""

    campaign_id: CampaignId
    brand_id: BrandId
    price_per_as: MoneyRate


@dataclass(frozen=True, slots=True, kw_only=True)
class Charge:
    charge_id: ChargeId
    dedupe_key: str  # screen:creative:seq of the playback report it bills
    auction_id: str
    creative_id: str
    screen_id: str
    campaign_id: CampaignId
    brand_id: BrandId
    attention_seconds: Decimal
    price_per_as: MoneyRate
    amount: Money
    reason: ChargeReason
    played_from: datetime
    played_to: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class PricedSlot:
    slot: BilledSlot
    amount: Money
    reason: ChargeReason


def price_play(slots: Sequence[BilledSlot], attention_seconds: Decimal) -> tuple[PricedSlot, ...]:
    """Each brand in the basket pays its own clearing price × the play's attention-seconds.

    The slot share is already inside the price (docs/auction-plan.md D3), so every
    brand bills the same measured attention. Rounded once, half-even, to Money.
    """
    if attention_seconds < 0:
        raise ValueError("attention_seconds cannot be negative")
    reason = ChargeReason.ATTENTION if attention_seconds > 0 else ChargeReason.NO_ATTENTION
    return tuple(
        PricedSlot(slot=slot, amount=slot.price_per_as.total_for(attention_seconds), reason=reason)
        for slot in slots
    )
