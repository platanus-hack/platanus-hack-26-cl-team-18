"""Redemption ledger adapters (in memory always; Postgres write-behind when configured)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from threading import Lock
from typing import Any

from psycopg2.extras import Json

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter
from app.modules.delivery.domain.redemption import Redemption, RedemptionId

INSERT_REDEMPTION = """
INSERT INTO redemption (
    redemption_id, code, auction_id, screen_id, campaign_ids, nonce_hash, redeemed_at
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT redemption_once_per_device DO NOTHING
"""


class InMemoryRedemptionLedger:
    def __init__(self) -> None:
        self._lock = Lock()
        self._rows: dict[tuple[str, str], Redemption] = {}

    def append(self, redemption: Redemption) -> None:
        with self._lock:
            self._rows.setdefault((redemption.code, redemption.nonce_hash), redemption)

    def redemptions(self) -> tuple[Redemption, ...]:
        with self._lock:
            return tuple(self._rows.values())


class PostgresRedemptionLedger:
    def __init__(
        self,
        writer: WriteBehindWriter,
        *,
        connect: Callable[[], Any] | None = None,
        organization_id: str | None = None,
    ) -> None:
        self._writer = writer
        self._mirror = InMemoryRedemptionLedger()
        self._connect = connect
        if connect is not None:
            with closing(connect()) as conn, conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT r.redemption_id, r.code, r.auction_id, r.screen_id, r.campaign_ids, r.nonce_hash, r.redeemed_at "
                    "FROM redemption r JOIN basket_offer o ON o.code = r.code "
                    "WHERE o.organization_id IS NOT DISTINCT FROM %s ORDER BY r.redeemed_at DESC LIMIT 12000",
                    (organization_id,),
                )
                for rid, code, auction, screen, campaigns, nonce, at in cur.fetchall():
                    self._mirror.append(
                        Redemption(
                            redemption_id=RedemptionId.create(rid),
                            code=code,
                            auction_id=str(auction),
                            screen_id=screen,
                            campaign_ids=tuple(CampaignId.create(c) for c in campaigns),
                            nonce_hash=nonce,
                            redeemed_at=at,
                        )
                    )

    def redemptions(self) -> tuple[Redemption, ...]:
        return self._mirror.redemptions()

    def append(self, redemption: Redemption) -> None:
        row = (
            str(redemption.redemption_id),
            redemption.code,
            redemption.auction_id,
            redemption.screen_id,
            Json([str(campaign) for campaign in redemption.campaign_ids]),
            redemption.nonce_hash,
            redemption.redeemed_at,
        )
        if self._connect is None:
            self._writer.submit(((INSERT_REDEMPTION, row),))
        else:
            with closing(self._connect()) as conn, conn, conn.cursor() as cur:
                cur.execute(INSERT_REDEMPTION, row)
        self._mirror.append(redemption)
