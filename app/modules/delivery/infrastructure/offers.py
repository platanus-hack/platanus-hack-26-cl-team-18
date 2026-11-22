"""Persist approved QR facts before publishing them, never frames or tracklets."""

from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from typing import Any

from psycopg2.extras import Json

from app.modules.campaigns.domain.book import CampaignId
from app.modules.delivery.application.redemptions import MAX_OFFERS_REMEMBERED
from app.modules.delivery.domain.redemption import BasketOffer


class PostgresOfferStore:
    def __init__(self, connect: Callable[[], Any], *, organization_id: str | None = None) -> None:
        self._connect = connect
        self._organization_id = organization_id

    def append(self, offer: BasketOffer) -> None:
        body = {
            "auction_id": offer.auction_id,
            "creative_id": offer.creative_id,
            "screen_id": offer.screen_id,
            "campaign_ids": [str(c) for c in offer.campaign_ids],
            "brand_names": list(offer.brand_names),
            "offer_text": offer.offer_text,
        }
        # This single local commit is the publication barrier: a QR on air must
        # already exist durably. A failed insert leaves the last good creative up.
        with closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO basket_offer (code, organization_id, issued_at, expires_at, payload) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (code) DO NOTHING",
                (offer.code, self._organization_id, offer.issued_at, offer.expires_at, Json(body)),
            )

    def offers(self) -> tuple[BasketOffer, ...]:
        with closing(self._connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(
                "SELECT code, issued_at, expires_at, payload FROM basket_offer "
                "WHERE organization_id IS NOT DISTINCT FROM %s "
                "ORDER BY issued_at DESC LIMIT %s",
                (self._organization_id, MAX_OFFERS_REMEMBERED),
            )
            rows: list[tuple[str, datetime, datetime, dict[str, Any]]] = cur.fetchall()
        return tuple(
            BasketOffer(
                code=code,
                issued_at=issued,
                expires_at=expires,
                auction_id=body["auction_id"],
                creative_id=body["creative_id"],
                screen_id=body["screen_id"],
                campaign_ids=tuple(CampaignId.create(c) for c in body["campaign_ids"]),
                brand_names=tuple(body["brand_names"]),
                offer_text=body["offer_text"],
            )
            for code, issued, expires, body in reversed(rows)
        )
