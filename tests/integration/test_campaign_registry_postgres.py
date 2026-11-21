"""Campaigns survive a restart in Postgres: same ids, spend restored, last bid kept.

    make db-up
    DATABASE_URL=postgresql://team18:team18@localhost:5432/team18 \
        uv run pytest -m integration -o addopts="" tests/integration/test_campaign_registry_postgres.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg2
import pytest

from app.modules.campaigns.domain.book import BidMultiplier
from app.modules.campaigns.infrastructure.registry import PostgresCampaignRegistry
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.domain.value_objects.money import Money
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter

pytestmark = pytest.mark.integration

DSN = os.environ.get("DATABASE_URL", "")
T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@pytest.fixture
def connect():  # type: ignore[no-untyped-def]
    if not DSN:
        pytest.skip("DATABASE_URL is not set (make db-up)")
    try:
        psycopg2.connect(DSN, connect_timeout=3).close()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    return lambda: psycopg2.connect(DSN, connect_timeout=3)


def registry(connect, writer):  # type: ignore[no-untyped-def]
    return PostgresCampaignRegistry(connect, writer, ids=IdGenerator(), clock=FixedClock(T0))


def test_a_restart_keeps_ids_spend_and_the_last_bid(connect) -> None:  # type: ignore[no-untyped-def]
    slug = f"it-{uuid4().hex[:12]}"
    budget = Money.create(currency="CLP", amount=50_000)
    writer = WriteBehindWriter(connect, name="it-campaigns")
    try:
        first = registry(connect, writer).open([slug], budget=budget)[slug]
        assert first.spent == Money.zero(currency="CLP")
        # A charge lands in the ledger, and the bidding war moves the slider.
        conn = connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO charge (charge_id, dedupe_key, auction_id, creative_id, screen_id,"
                " campaign_id, brand_id, attention_seconds, price_per_as, amount, currency,"
                " reason, played_from, played_to, recorded_at)"
                " VALUES (%s, %s, %s, 'c', 's', %s, %s, 4, 300, 1200, 'CLP', 'attention',"
                " %s, %s, %s)",
                (
                    str(uuid4()),
                    f"it-{slug}",
                    str(uuid4()),
                    str(first.campaign_id),
                    str(first.brand_id),
                    T0,
                    T0,
                    T0,
                ),
            )
        conn.close()
        reg = registry(connect, writer)
        reg.multiplier_changed(first.campaign_id, BidMultiplier.create("2.5"), 200)
        reg.multiplier_changed(first.campaign_id, BidMultiplier.create("0.5"), 100)  # late, older
        assert writer.flush()

        again = registry(connect, writer).open(
            [slug], budget=Money.create(currency="CLP", amount=1)
        )[slug]
        assert again.campaign_id == first.campaign_id and again.brand_id == first.brand_id
        assert again.budget == budget  # the stored budget, not the new default
        assert again.spent == Money.create(currency="CLP", amount=1200)
        assert again.multiplier == BidMultiplier.create(Decimal("2.5"))
        assert again.multiplier_seq == 200
    finally:
        writer.close()
