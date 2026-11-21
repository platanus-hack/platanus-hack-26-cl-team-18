"""Charge ledger adapters: an in-memory list (always) and a Postgres write-behind tee."""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter
from app.modules.delivery.domain.charge import Charge

INSERT_CHARGE = """
INSERT INTO charge (
    charge_id, dedupe_key, auction_id, creative_id, screen_id, campaign_id, brand_id,
    attention_seconds, price_per_as, amount, currency, reason, played_from, played_to,
    recorded_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT charge_dedupe_unique DO NOTHING
"""


class InMemoryChargeLedger:
    def __init__(self) -> None:
        self._lock = Lock()
        self._charges: list[Charge] = []

    def append(self, charges: tuple[Charge, ...]) -> None:
        with self._lock:
            self._charges.extend(charges)

    def charges(self) -> tuple[Charge, ...]:
        with self._lock:
            return tuple(self._charges)


class PostgresChargeLedger:
    """All charges of one playback report commit in one transaction."""

    def __init__(self, writer: WriteBehindWriter, *, mirror: InMemoryChargeLedger | None = None):
        self._writer = writer
        self._mirror = mirror or InMemoryChargeLedger()

    def charges(self) -> tuple[Charge, ...]:
        """What this process recorded (the console's read; Postgres is the record)."""
        return self._mirror.charges()

    def append(self, charges: tuple[Charge, ...]) -> None:
        self._mirror.append(charges)
        if charges:
            self._writer.submit(tuple((INSERT_CHARGE, charge_row(charge)) for charge in charges))


def charge_row(charge: Charge) -> tuple[Any, ...]:
    return (
        str(charge.charge_id),
        charge.dedupe_key,
        charge.auction_id,
        charge.creative_id,
        charge.screen_id,
        str(charge.campaign_id),
        str(charge.brand_id),
        charge.attention_seconds,
        charge.price_per_as.amount,
        charge.amount.amount,
        str(charge.amount.currency),
        charge.reason.value,
        charge.played_from,
        charge.played_to,
        charge.recorded_at,
    )
