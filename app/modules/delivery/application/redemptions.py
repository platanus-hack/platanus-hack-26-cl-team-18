"""Issue a code per cleared basket and redeem it (idempotent per code + device)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import Lock
from typing import Protocol

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.application.id_generator import IdGenerator
from app.modules.delivery.domain.redemption import (
    REDEMPTION_TTL,
    BasketOffer,
    ExpiredRedemptionCode,
    Redemption,
    RedemptionId,
    UnknownRedemptionCode,
    code_for,
    nonce_fingerprint,
)

MAX_OFFERS_REMEMBERED = 2_000


class RedemptionLedger(Protocol):
    def append(self, redemption: Redemption) -> None: ...


class RedemptionDesk:
    def __init__(self, *, ids: IdGenerator, ledger: RedemptionLedger) -> None:
        self._ids = ids
        self._ledger = ledger
        self._lock = Lock()
        self._offers: dict[str, BasketOffer] = {}
        self._redeemed: dict[tuple[str, str], Redemption] = {}

    def issue(
        self,
        *,
        auction_id: str,
        creative_id: str,
        screen_id: str,
        campaign_ids: Sequence[CampaignId],
        brand_names: Sequence[str],
        offer_text: str | None,
        at: datetime,
    ) -> BasketOffer:
        offer = BasketOffer(
            code=code_for(self._ids.new_uuid7()),
            auction_id=auction_id,
            creative_id=creative_id,
            screen_id=screen_id,
            campaign_ids=tuple(campaign_ids),
            brand_names=tuple(brand_names),
            offer_text=offer_text,
            issued_at=at,
            expires_at=at + REDEMPTION_TTL,
        )
        with self._lock:
            self._offers[offer.code] = offer
            while len(self._offers) > MAX_OFFERS_REMEMBERED:
                self._offers.pop(next(iter(self._offers)))
        return offer

    def offer(self, code: str) -> BasketOffer | None:
        with self._lock:
            return self._offers.get(code.upper())

    def redeem(self, code: str, nonce: str, *, at: datetime) -> tuple[Redemption, bool]:
        """Returns (redemption, created). The same device redeeming again is not a new sale."""
        key = (code.upper(), nonce_fingerprint(nonce))
        with self._lock:
            offer = self._offers.get(key[0])
            if offer is None:
                raise UnknownRedemptionCode()
            if (existing := self._redeemed.get(key)) is not None:
                return existing, False
            if not offer.is_open(at):
                raise ExpiredRedemptionCode()
            redemption = Redemption(
                redemption_id=RedemptionId.create(self._ids.new_uuid7()),
                code=offer.code,
                auction_id=offer.auction_id,
                screen_id=offer.screen_id,
                campaign_ids=offer.campaign_ids,
                nonce_hash=key[1],
                redeemed_at=at,
            )
            self._redeemed[key] = redemption
        self._ledger.append(redemption)
        return redemption, True
