"""Redemption ledger adapters (in memory always; Postgres write-behind when configured)."""

from __future__ import annotations

from threading import Lock

from psycopg2.extras import Json

from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter
from app.modules.delivery.domain.redemption import Redemption

INSERT_REDEMPTION = """
INSERT INTO redemption (
    redemption_id, code, auction_id, screen_id, campaign_ids, nonce_hash, redeemed_at
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT redemption_once_per_device DO NOTHING
"""


class InMemoryRedemptionLedger:
    def __init__(self) -> None:
        self._lock = Lock()
        self._rows: list[Redemption] = []

    def append(self, redemption: Redemption) -> None:
        with self._lock:
            self._rows.append(redemption)

    def redemptions(self) -> tuple[Redemption, ...]:
        with self._lock:
            return tuple(self._rows)


class PostgresRedemptionLedger:
    def __init__(self, writer: WriteBehindWriter) -> None:
        self._writer = writer
        self._mirror = InMemoryRedemptionLedger()

    def redemptions(self) -> tuple[Redemption, ...]:
        return self._mirror.redemptions()

    def append(self, redemption: Redemption) -> None:
        self._mirror.append(redemption)
        row = (
            str(redemption.redemption_id),
            redemption.code,
            redemption.auction_id,
            redemption.screen_id,
            Json([str(campaign) for campaign in redemption.campaign_ids]),
            redemption.nonce_hash,
            redemption.redeemed_at,
        )
        self._writer.submit(((INSERT_REDEMPTION, row),))
