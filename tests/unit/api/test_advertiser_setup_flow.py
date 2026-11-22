"""Advertiser self-serve setup (/advertisers) through HTTP: profiles and budget top-ups."""

from typing import Any

from app.contract.messages import AdvertiserProfiles, ConsoleBook
from tests.unit.api.test_auction_loop_e2e import Loop, campaign_of, loop as loop


def top_up(loop: Loop, brand: str, amount: Any, key: str = "topup-0001") -> Any:
    return loop.client.post(
        "/api/advertiser/budget",
        json={
            "type": "advertiser.budget_top_up",
            "v": 1,
            "campaign_id": campaign_of(loop, brand),
            "amount": amount,
            "currency": "CLP",
            "idempotency_key": key,
        },
    )


def line(loop: Loop, brand: str) -> Any:
    book = ConsoleBook.model_validate(loop.client.get("/api/book").json())
    return next(entry for entry in book.lines if entry.brand_name == brand)


def test_profiles_show_what_each_brand_buys_with_partners_offers_and_rivals(loop: Loop) -> None:
    response = loop.client.get("/api/advertiser/profiles")
    assert response.status_code == 200
    profiles = {
        p.brand_name: p for p in AdvertiserProfiles.model_validate(response.json()).profiles
    }
    voltio = profiles["Voltio"]
    assert voltio.campaign_id == campaign_of(loop, "Voltio")
    assert [a.segment_id for a in voltio.audiences] == ["408"]
    assert voltio.audiences[0].rate_per_attention_s.amount == "8"
    assert voltio.audiences[0].label  # a human label, not just the IAB id
    assert "Rayo" in voltio.competitors
    cafe = next(p for p in voltio.partners if p.brand_name == "Café Patio")
    assert cafe.offers and all(o.price.currency == "CLP" for o in cafe.offers)
    # Invariant 2: an age-restricted brand says so.
    assert profiles["Malta Sur"].age_restricted


def test_a_top_up_grows_the_budget_once_even_when_retried(loop: Loop) -> None:
    before = int(line(loop, "Rayo").budget.amount)
    assert top_up(loop, "Rayo", "25000").json() == {"outcome": "applied"}
    assert top_up(loop, "Rayo", "25000").json() == {"outcome": "duplicate"}
    assert int(line(loop, "Rayo").budget.amount) == before + 25000


def test_a_reused_key_with_another_amount_is_a_conflict(loop: Loop) -> None:
    assert top_up(loop, "Rayo", "1000").status_code == 200
    assert top_up(loop, "Rayo", "2000").status_code == 409


def test_invalid_top_ups_are_rejected_without_changing_the_book(loop: Loop) -> None:
    before = loop.client.get("/api/book").json()
    for amount in ["0", "-5", "1.5", "10000001", "", 100, None, "1e9"]:
        assert top_up(loop, "Rayo", amount).status_code == 422, amount
    assert loop.client.get("/api/book").json() == before


def test_an_unknown_campaign_is_not_found(loop: Loop) -> None:
    response = loop.client.post(
        "/api/advertiser/budget",
        json={
            "campaign_id": "0190f2a0-0000-7000-8000-000000000000",
            "amount": "1000",
            "idempotency_key": "topup-unknown",
        },
    )
    assert response.status_code == 404
