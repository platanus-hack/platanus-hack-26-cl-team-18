"""Issue a code per cleared basket and redeem it (idempotent per code + device)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import Lock
from typing import Protocol

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.application.id_generator import IdGenerator
from app.modules.delivery.domain.redemption import (
    MAX_REDEMPTIONS_PER_CODE,
    REDEMPTION_TTL,
    BasketOffer,
    ExpiredRedemptionCode,
    Redemption,
    RedemptionId,
    RedemptionLimitReached,
    UnknownRedemptionCode,
    code_for,
    nonce_fingerprint,
)

MAX_OFFERS_REMEMBERED = 2_000


class RedemptionLedger(Protocol):
    """Append idempotently by (code, nonce_hash), including ambiguous retries."""

    def append(self, redemption: Redemption) -> None: ...


class RedemptionDesk:
    """Issues codes and redeems them: idempotent per (code, device nonce), and at most
    `max_per_code` distinct devices per code (a randomized nonce is not a new sale).

    Everything is bounded by the offers remembered: when an offer is forgotten, so are
    its redemptions (its code is long expired by then, so nothing new can be sold).
    """

    def __init__(
        self,
        *,
        ids: IdGenerator,
        ledger: RedemptionLedger,
        max_per_code: int = MAX_REDEMPTIONS_PER_CODE,
    ) -> None:
        self._ids = ids
        self._ledger = ledger
        self._max_per_code = max_per_code
        self._lock = Lock()
        self._offers: dict[str, BasketOffer] = {}
        self._redeemed: dict[tuple[str, str], Redemption] = {}
        self._pending: dict[tuple[str, str], Redemption] = {}
        # Per code, the nonce hashes redeemed (or pending) for it: the per-code cap.
        self._devices: dict[str, set[str]] = {}

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
                self._forget(next(iter(self._offers)))
        return offer

    def _forget(self, code: str) -> None:
        self._offers.pop(code, None)
        for nonce_hash in self._devices.pop(code, ()):
            self._redeemed.pop((code, nonce_hash), None)
            self._pending.pop((code, nonce_hash), None)

    def offer(self, code: str) -> BasketOffer | None:
        with self._lock:
            return self._offers.get(code.upper())

    def redeem(self, code: str, nonce: str, *, at: datetime) -> tuple[Redemption, bool]:
        """Returns (redemption, created). The same device redeeming again is not a new sale."""
        key = (code.upper(), nonce_fingerprint(nonce))
        with self._lock:
            if (existing := self._redeemed.get(key)) is not None:
                return existing, False
            redemption = self._pending.get(key)
            if redemption is None:
                offer = self._offers.get(key[0])
                if offer is None:
                    raise UnknownRedemptionCode()
                if not offer.is_open(at):
                    raise ExpiredRedemptionCode()
                devices = self._devices.setdefault(offer.code, set())
                if len(devices) >= self._max_per_code:
                    raise RedemptionLimitReached()
                devices.add(key[1])
                redemption = Redemption(
                    redemption_id=RedemptionId.create(self._ids.new_uuid7()),
                    code=offer.code,
                    auction_id=offer.auction_id,
                    screen_id=offer.screen_id,
                    campaign_ids=offer.campaign_ids,
                    nonce_hash=key[1],
                    redeemed_at=at,
                )
                self._pending[key] = redemption
            # Keep the same record for a retry, even if append succeeded before
            # raising. Never acknowledge a sale whose ledger append failed.
            self._ledger.append(redemption)
            self._redeemed[key] = redemption
            del self._pending[key]
        return redemption, True
