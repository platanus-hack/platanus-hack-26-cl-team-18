"""Campaign registry adapters: in memory (local, tests) and Postgres (prod record)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from decimal import Decimal
from threading import Lock
from typing import Any

from app.modules.campaigns.application.ports.registry import StoredCampaign
from app.modules.campaigns.domain.book import BidMultiplier, BrandId, CampaignId
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter

logger = logging.getLogger(__name__)

INSERT_CAMPAIGN = """
INSERT INTO campaign (
    campaign_id, brand_id, brand_slug, currency, budget, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (brand_slug) DO NOTHING
"""

SELECT_CAMPAIGNS = """
SELECT brand_slug, campaign_id, brand_id, currency, budget, live_multiplier, multiplier_seq
FROM campaign WHERE brand_slug = ANY(%s)
"""

SELECT_SPENT = """
SELECT campaign_id, currency, COALESCE(SUM(amount), 0)
FROM charge WHERE campaign_id = ANY(%s::uuid[])
GROUP BY campaign_id, currency
"""

# Monotonic by seq: a late write from an older instance never rolls a bid back.
UPDATE_MULTIPLIER = """
UPDATE campaign SET live_multiplier = %s, multiplier_seq = %s, updated_at = %s
WHERE campaign_id = %s AND multiplier_seq < %s
"""


class InMemoryCampaignRegistry:
    """Fresh ids per process; reopening a slug in the same process returns the same campaign."""

    def __init__(self, ids: IdGenerator | None = None) -> None:
        self._ids = ids or IdGenerator()
        self._lock = Lock()
        self._campaigns: dict[str, StoredCampaign] = {}

    def open(self, slugs: Sequence[str], *, budget: Money) -> dict[str, StoredCampaign]:
        with self._lock:
            for slug in slugs:
                if slug not in self._campaigns:
                    self._campaigns[slug] = StoredCampaign(
                        slug=slug,
                        brand_id=BrandId.create(self._ids.new_uuid7()),
                        campaign_id=CampaignId.create(self._ids.new_uuid7()),
                        budget=budget,
                        spent=Money.zero(currency=budget.currency),
                        multiplier=BidMultiplier.neutral(),
                        multiplier_seq=0,
                    )
            return {slug: self._campaigns[slug] for slug in slugs}

    def multiplier_changed(
        self, campaign_id: CampaignId, multiplier: BidMultiplier, seq: int
    ) -> None:
        return None  # the live book already holds it; nothing outlives the process


class PostgresCampaignRegistry:
    """Startup reads are synchronous (never on the hot path); multiplier writes go behind."""

    def __init__(
        self,
        connect: Callable[[], Any],
        writer: WriteBehindWriter,
        *,
        ids: IdGenerator,
        clock: BaseClock,
    ) -> None:
        self._connect = connect
        self._writer = writer
        self._ids = ids
        self._clock = clock

    def open(self, slugs: Sequence[str], *, budget: Money) -> dict[str, StoredCampaign]:
        now = self._clock.now_utc()
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                # Concurrent boots (an overlapping deploy) race safely: the slug is unique.
                for slug in slugs:
                    cur.execute(
                        INSERT_CAMPAIGN,
                        (
                            str(self._ids.new_uuid7()),
                            str(self._ids.new_uuid7()),
                            slug,
                            str(budget.currency),
                            budget.amount,
                            now,
                            now,
                        ),
                    )
                cur.execute(SELECT_CAMPAIGNS, (list(slugs),))
                rows = cur.fetchall()
                cur.execute(SELECT_SPENT, ([str(row[1]) for row in rows],))
                spent_rows = cur.fetchall()
        finally:
            conn.close()
        spent = {
            str(campaign_id): (currency, amount) for campaign_id, currency, amount in spent_rows
        }
        campaigns = {}
        for slug, campaign_id, brand_id, currency, stored_budget, multiplier, seq in rows:
            currency = currency.strip()
            spent_currency, spent_amount = spent.get(str(campaign_id), (currency, Decimal(0)))
            if spent_currency.strip() != currency:
                raise ValueError(f"Campaign {slug} was charged in {spent_currency}, not {currency}")
            campaigns[slug] = StoredCampaign(
                slug=slug,
                brand_id=BrandId.create(brand_id),
                campaign_id=CampaignId.create(campaign_id),
                budget=Money.create(currency=currency, amount=stored_budget, rounded=True),
                spent=Money.create(currency=currency, amount=spent_amount, rounded=True),
                multiplier=BidMultiplier.create(Decimal(multiplier)),
                multiplier_seq=int(seq),
            )
        missing = set(slugs) - campaigns.keys()
        if missing:
            raise RuntimeError(f"Campaign rows missing after insert: {sorted(missing)}")
        return campaigns

    def multiplier_changed(
        self, campaign_id: CampaignId, multiplier: BidMultiplier, seq: int
    ) -> None:
        self._writer.submit(
            (
                (
                    UPDATE_MULTIPLIER,
                    (multiplier.value, seq, self._clock.now_utc(), str(campaign_id), seq),
                ),
            )
        )
