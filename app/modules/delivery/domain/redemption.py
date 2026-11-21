"""Basket offers and their redemptions (architecture §6.2): the QR that proves the sale.

Each cleared basket gets a short code, valid 10 minutes. Redeeming it records a
`Redemption` credited to every brand in the basket. The code identifies the
auction (moment + basket), never a person. A device nonce is kept only as a hash,
so the same phone redeeming twice is idempotent and nothing about it is stored.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.primitives.errors import DomainError
from app.modules.common.domain.primitives.typed_id import TypedId

REDEMPTION_TTL = timedelta(minutes=10)
CODE_LENGTH = 8


class RedemptionId(TypedId):
    __slots__ = ()


class UnknownRedemptionCode(DomainError):
    def __init__(self, **_: Any) -> None:
        super().__init__(code="delivery.unknown_code", msg="This offer code doesn't exist.")


class ExpiredRedemptionCode(DomainError):
    def __init__(self, **_: Any) -> None:
        super().__init__(code="delivery.expired_code", msg="This offer has expired.")


def code_for(seed: UUID) -> str:
    """A short, unambiguous code from an id (base32 of a hash: no 0/1/8/9 lookalikes)."""
    digest = hashlib.sha256(seed.bytes).digest()
    return base64.b32encode(digest).decode("ascii")[:CODE_LENGTH]


def nonce_fingerprint(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class BasketOffer:
    code: str
    auction_id: str
    creative_id: str
    screen_id: str
    campaign_ids: tuple[CampaignId, ...]
    brand_names: tuple[str, ...]
    offer_text: str | None
    issued_at: datetime
    expires_at: datetime

    def is_open(self, at: datetime) -> bool:
        return self.issued_at <= at < self.expires_at


@dataclass(frozen=True, slots=True, kw_only=True)
class Redemption:
    redemption_id: RedemptionId
    code: str
    auction_id: str
    screen_id: str
    campaign_ids: tuple[CampaignId, ...]  # every brand in the basket is credited
    nonce_hash: str
    redeemed_at: datetime
