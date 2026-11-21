"""Auctions and charges reach Postgres through the write-behind path (run on purpose).

    make db-up
    DATABASE_URL=postgresql://team18:team18@localhost:5432/team18 \
        uv run pytest -m integration -o addopts="" tests/integration/test_records_postgres.py

The full app runs with app_env=local and a real DATABASE_URL: an agent
snapshot clears an auction, the player reports a watched play, and both rows
land in Postgres (append-only, deduped by natural key).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import CreativeReady, PlaybackReport
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter
from app.modules.delivery.infrastructure.ledger import PostgresChargeLedger

pytestmark = pytest.mark.integration

DSN = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def db():  # type: ignore[no-untyped-def]
    if not DSN:
        pytest.skip("DATABASE_URL is not set (make db-up)")
    try:
        conn = psycopg2.connect(DSN, connect_timeout=3)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Postgres is not reachable: {exc}")
    conn.autocommit = True
    yield conn
    conn.close()


def scalar(conn, sql: str, *params):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def test_the_live_loop_records_auction_and_charge_rows(db, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    t0 = datetime.now(UTC).replace(microsecond=0)
    clock = FixedClock(t0)
    settings = AppSettings(
        app_env="local",
        demo_simulator=True,
        media_dir=tmp_path,
        database_url=DSN,
        auction_swap_holds=False,
    )
    container = build_container(settings, clock=clock)
    assert container.writer is not None
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        CreativeReady.model_validate(player.receive_json())  # house ad
        agent.send_text(snapshot_for("fitness", 1, t0).model_dump_json())
        creative = CreativeReady.model_validate(player.receive_json())
        assert creative.auction_id is not None
        clock.advance(timedelta(seconds=4))
        player.send_text(
            PlaybackReport(
                screen_id="demo-01",
                creative_id=creative.creative_id,
                seq=1,
                started_at=t0,
                ended_at=t0 + timedelta(seconds=4),
            ).model_dump_json()
        )
        deadline = time.monotonic() + 10
        while not container.ledger.charges() and time.monotonic() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.02)  # the report is billed on the router thread
        assert container.writer.flush(timeout=10)

    row = scalar(
        db,
        "SELECT trigger, layout, pricing, people, looking, winners->0->>'price_per_as',"
        " bundle_revenue FROM auction WHERE auction_id = %s",
        creative.auction_id,
    )
    assert row is not None
    trigger, layout, pricing, people, looking, price, revenue = row
    assert (trigger, layout, pricing, people, looking) == (
        "moment_changed",
        "solo",
        "core_adjusted",
        1,
        1,
    )
    assert price == "6.5000" and float(revenue) == 6.5
    charge = scalar(
        db,
        "SELECT attention_seconds, price_per_as, amount, currency, reason FROM charge"
        " WHERE auction_id = %s",
        creative.auction_id,
    )
    assert charge is not None
    assert [str(value) for value in charge] == [
        "4.000",
        "6.500000",
        "26.000000",
        "CLP",
        "attention",
    ]


def test_a_replayed_charge_is_deduped_by_its_natural_key(db) -> None:  # type: ignore[no-untyped-def]
    from app.modules.campaigns.domain.book import BrandId, CampaignId
    from app.modules.common.domain.value_objects.money import Money
    from app.modules.common.domain.value_objects.money_rate import MoneyRate
    from app.modules.delivery.domain.charge import Charge, ChargeId, ChargeReason

    writer = WriteBehindWriter(lambda: psycopg2.connect(DSN, connect_timeout=3), name="it")
    ledger = PostgresChargeLedger(writer)
    now = datetime.now(UTC)
    key = f"it:{uuid4()}:1"
    campaign = CampaignId.create(uuid4())

    def charge() -> Charge:
        return Charge(
            charge_id=ChargeId.create(uuid4()),
            dedupe_key=key,
            auction_id=str(uuid4()),
            creative_id="c",
            screen_id="it",
            campaign_id=campaign,
            brand_id=BrandId.create(uuid4()),
            attention_seconds=Decimal("1.5"),
            price_per_as=MoneyRate.create(currency="CLP", amount="2"),
            amount=Money.create(currency="CLP", amount=3),
            reason=ChargeReason.ATTENTION,
            played_from=now,
            played_to=now,
            recorded_at=now,
        )

    ledger.append((charge(),))
    ledger.append((charge(),))  # a replay with a new id but the same natural key
    assert writer.flush(timeout=10)
    writer.close()
    assert scalar(db, "SELECT count(*) FROM charge WHERE dedupe_key = %s", key) == (1,)


def test_the_charge_table_rejects_negative_money(db) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg2.errors.CheckViolation), db.cursor() as cur:
        cur.execute(
            "INSERT INTO charge (charge_id, dedupe_key, auction_id, creative_id, screen_id,"
            " campaign_id, brand_id, attention_seconds, price_per_as, amount, currency, reason,"
            " played_from, played_to, recorded_at) VALUES (%s, 'neg', %s, 'c', 's', %s, %s,"
            " 1, 1, -1, 'CLP', 'attention', now(), now(), now())",
            (str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())),
        )
