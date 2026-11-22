"""A receipt and all its charges commit together before acknowledgment to the player."""

from collections.abc import Callable, Mapping
from contextlib import closing
from threading import Lock
from typing import Any

from psycopg2.extras import Json

from app.contract.messages import ConsoleReceipt
from app.modules.campaigns.domain.book import BrandId
from app.modules.delivery.application.billing import Receipt
from app.modules.delivery.application.receipts import receipt_message
from app.modules.delivery.infrastructure.ledger import INSERT_CHARGE, charge_row


class PostgresReceiptHistory:
    def __init__(
        self,
        connect: Callable[[], Any],
        names: Mapping[BrandId, str],
        *,
        organization_id: str | None = None,
    ) -> None:
        self._connect, self._names, self._organization_id = connect, names, organization_id
        self._lock = Lock()
        with closing(connect()) as conn, conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (screen_id) screen_id, payload FROM playback_receipt "
                "WHERE organization_id IS NOT DISTINCT FROM %s ORDER BY screen_id, recorded_at DESC",
                (organization_id,),
            )
            self._latest = {
                screen: ConsoleReceipt.model_validate(body) for screen, body in cur.fetchall()
            }

    def latest(self, screen_id: str) -> ConsoleReceipt | None:
        with self._lock:
            return self._latest.get(screen_id)

    def append(self, receipt: Receipt) -> None:
        message = receipt_message(receipt, self._names)
        if not receipt.charges:
            return
        with closing(self._connect()) as conn, conn, conn.cursor() as cur:
            for charge in receipt.charges:
                cur.execute(INSERT_CHARGE, charge_row(charge))
            cur.execute(
                "INSERT INTO playback_receipt (dedupe_key, organization_id, screen_id, recorded_at, payload) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING",
                (
                    receipt.charges[0].dedupe_key,
                    self._organization_id,
                    message.screen_id,
                    max(c.recorded_at for c in receipt.charges),
                    Json(message.model_dump(mode="json")),
                ),
            )
        with self._lock:
            self._latest[message.screen_id] = message
