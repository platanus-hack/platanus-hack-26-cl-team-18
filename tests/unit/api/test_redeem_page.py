"""The `/r/{code}` offer page a judge's phone opens from the QR."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.container import Container, build_container
from app.config.settings import AppSettings
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.delivery.domain.redemption import REDEMPTION_TTL, BasketOffer

# 15:00 UTC on 21 Nov 2026 is 12:00 in Santiago (CLST, UTC-3).
ISSUED = datetime(2026, 11, 21, 15, tzinfo=UTC)
OFFER = BasketOffer(
    code="ABCDEFGH",
    auction_id="auction",
    creative_id="creative",
    screen_id="demo-01",
    campaign_ids=(),
    brand_names=("Score", "Juan Valdez"),
    offer_text="Trío $9.990",
    issued_at=ISSUED,
    expires_at=ISSUED + REDEMPTION_TTL,
)


CLOCK = FixedClock(ISSUED)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    CLOCK.set(ISSUED)
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container: Container = build_container(settings, clock=CLOCK)
    assert container.demo is not None
    container.demo = replace(
        container.demo, offer_for=lambda code: OFFER if code == OFFER.code else None
    )
    with TestClient(create_app(container=container)) as test_client:
        yield test_client


def test_the_offer_page_shows_the_expiry_in_the_venue_clock(client: TestClient) -> None:
    page = client.get("/r/abcdefgh")
    assert page.status_code == 200
    assert "válido hasta las 12:10" in page.text  # Santiago, not UTC ("15:10")
    assert "Trío $9.990" in page.text and "Score + Juan Valdez" in page.text


def test_unknown_and_malformed_codes_are_404_not_500(client: TestClient) -> None:
    for path in ("/r/ZZZZZZZZ", "/r/a", "/r/" + "A" * 40, "/r/%3Cscript%3E"):
        page = client.get(path)
        assert page.status_code == 404
        # A judge's phone gets a readable page in Spanish, never raw JSON.
        assert page.headers["content-type"].startswith("text/html")
        assert "Esta oferta no existe" in page.text and "<script>" not in page.text
    garbage = client.post("/r/ZZZZZZZZ/redeem", json={"nonce": "anonymous-phone"})
    assert garbage.status_code == 404
    assert client.post("/r/ABCDEFGH/redeem", json={"nonce": "short"}).status_code == 422
    assert client.post("/r/ABCDEFGH/redeem", json={"nonce": "<script>x"}).status_code == 422


def test_expiry_shown_is_the_instant_redeem_starts_refusing() -> None:
    assert OFFER.is_open(OFFER.expires_at - timedelta(microseconds=1))
    assert not OFFER.is_open(OFFER.expires_at)


def test_an_expired_offer_page_says_so_and_offers_no_redeem_button(client: TestClient) -> None:
    CLOCK.set(OFFER.expires_at)
    page = client.get("/r/ABCDEFGH")
    assert page.status_code == 410
    assert page.headers["content-type"].startswith("text/html")
    assert "Esta oferta expiró" in page.text and 'id="go"' not in page.text


def test_an_already_redeemed_offer_says_ya_canjeado_on_the_phone(client: TestClient) -> None:
    page = client.get("/r/ABCDEFGH").text
    # The page reads `new` from the redeem response: a repeat scan is not a new sale.
    assert "r.json()" in page and "Ya canjeaste esta oferta" in page


def test_a_code_redeemed_by_too_many_phones_answers_409(tmp_path: Path) -> None:
    from app.modules.delivery.domain.redemption import RedemptionLimitReached

    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container: Container = build_container(settings, clock=CLOCK)
    assert container.demo is not None

    def full(code: str, nonce: str) -> object:
        raise RedemptionLimitReached()

    container.demo = replace(container.demo, redeem=full)  # type: ignore[arg-type]
    with TestClient(create_app(container=container)) as test_client:
        answer = test_client.post("/r/ABCDEFGH/redeem", json={"nonce": "anonymous-phone"})
    assert answer.status_code == 409
    assert "redeemed" in answer.json()["detail"]


def test_the_page_never_hangs_a_judge_on_a_bad_link(client: TestClient) -> None:
    # Venue stress (evals/network_stress.py phones): on 64 kbit/s and 800 ms RTT,
    # one redeem in five timed out. A hung fetch left the button disabled forever;
    # it must give up, say so, and let a second tap retry with the same nonce
    # (idempotent: never a second credit). Fonts must not block the first paint
    # on a LAN without internet either.
    page = client.get("/r/ABCDEFGH").text
    assert "AbortController" in page and "ctl.abort()" in page
    assert 'rel="stylesheet"' in page and 'media="print" onload="this.media=\'all\'"' in page
    notice = client.get("/r/NOPE1234").text
    assert 'media="print" onload="this.media=\'all\'"' in notice
