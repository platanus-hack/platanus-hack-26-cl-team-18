"""Basket QR codes: one per cleared basket, valid 10 min, redeemed once per device."""

import base64
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.application.id_generator import IdGenerator
from app.modules.delivery.application import redemptions as redemptions_module
from app.modules.delivery.application.redemptions import RedemptionDesk
from app.modules.delivery.domain.redemption import (
    MAX_REDEMPTIONS_PER_CODE,
    ExpiredRedemptionCode,
    RedemptionLimitReached,
    UnknownRedemptionCode,
    code_for,
)
from app.modules.delivery.infrastructure.qr import qr_data_url
from app.modules.delivery.infrastructure.redemptions import InMemoryRedemptionLedger

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
CAMPAIGNS = (
    CampaignId.create(UUID("018f0000-0000-7000-8000-000200000000")),
    CampaignId.create(UUID("018f0000-0000-7000-8000-000200000001")),
)


def desk() -> tuple[RedemptionDesk, InMemoryRedemptionLedger, str]:
    ledger = InMemoryRedemptionLedger()
    redemptions = RedemptionDesk(ids=IdGenerator(), ledger=ledger)
    offer = redemptions.issue(
        auction_id="018f0000-0000-7000-8000-000500000000",
        creative_id="c1",
        screen_id="demo-01",
        campaign_ids=CAMPAIGNS,
        brand_names=("Voltio", "Café Patio"),
        offer_text="Combo post-entreno · Voltio + Café Patio por $3.490",
        at=T0,
    )
    return redemptions, ledger, offer.code


def test_codes_are_short_unambiguous_and_distinct() -> None:
    codes = {code_for(UUID(int=n)) for n in range(500)}
    assert len(codes) == 500
    assert all(re.fullmatch(r"[A-Z2-7]{8}", code) for code in codes)


def test_a_redemption_credits_every_brand_in_the_basket() -> None:
    redemptions, ledger, code = desk()
    redemption, created = redemptions.redeem(
        code.lower(), "device-aaaa", at=T0 + timedelta(minutes=1)
    )
    assert created and redemption.campaign_ids == CAMPAIGNS
    assert ledger.redemptions() == (redemption,)
    assert "device-aaaa" not in redemption.nonce_hash  # only a hash of the nonce is kept


def test_the_same_device_redeeming_again_is_not_a_new_sale() -> None:
    redemptions, ledger, code = desk()
    first, _ = redemptions.redeem(code, "device-aaaa", at=T0)
    again, created = redemptions.redeem(code, "device-aaaa", at=T0 + timedelta(minutes=12))
    assert not created and again == first  # idempotent even after expiry
    _, second_phone = redemptions.redeem(code, "device-bbbb", at=T0 + timedelta(minutes=2))
    assert second_phone and len(ledger.redemptions()) == 2


def test_codes_expire_after_ten_minutes_and_unknown_codes_fail() -> None:
    redemptions, ledger, code = desk()
    with pytest.raises(ExpiredRedemptionCode):
        redemptions.redeem(code, "device-aaaa", at=T0 + timedelta(minutes=10))
    with pytest.raises(UnknownRedemptionCode):
        redemptions.redeem("ZZZZZZZZ", "device-aaaa", at=T0)
    assert ledger.redemptions() == ()


def test_the_qr_is_a_self_contained_svg_of_the_url() -> None:
    url = qr_data_url("http://localhost:5173/r/ABCD2345")
    assert url.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(url.split(",", 1)[1]).decode()
    assert svg.startswith("<?xml") and "<svg" in svg and "#fff" in svg.lower()


def test_randomized_nonces_cannot_forge_unlimited_sales_for_one_code() -> None:
    redemptions, ledger, code = desk()
    for n in range(MAX_REDEMPTIONS_PER_CODE):
        _, created = redemptions.redeem(code, f"phone-{n:04d}", at=T0)
        assert created
    with pytest.raises(RedemptionLimitReached):
        redemptions.redeem(code, "forged-nonce-9999", at=T0)
    assert len(ledger.redemptions()) == MAX_REDEMPTIONS_PER_CODE
    # A phone that already redeemed still gets its idempotent answer at the cap.
    again, created = redemptions.redeem(code, "phone-0000", at=T0)
    assert not created and again in ledger.redemptions()


def test_the_cap_is_per_code_not_global() -> None:
    redemptions, _, code = desk()
    other = redemptions.issue(
        auction_id="a2",
        creative_id="c2",
        screen_id="demo-01",
        campaign_ids=CAMPAIGNS,
        brand_names=("Voltio",),
        offer_text=None,
        at=T0,
    )
    for n in range(MAX_REDEMPTIONS_PER_CODE):
        redemptions.redeem(code, f"phone-{n:04d}", at=T0)
    _, created = redemptions.redeem(other.code, "phone-0000", at=T0)
    assert created


def test_forgotten_offers_take_their_redemptions_with_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redemptions_module, "MAX_OFFERS_REMEMBERED", 2)
    redemptions, _, code = desk()
    redemptions.redeem(code, "device-aaaa", at=T0)
    for n in range(2):
        redemptions.issue(
            auction_id=f"a{n}",
            creative_id=f"c{n}",
            screen_id="demo-01",
            campaign_ids=CAMPAIGNS,
            brand_names=("Voltio",),
            offer_text=None,
            at=T0,
        )
    assert redemptions.offer(code) is None
    assert not redemptions._redeemed and not redemptions._devices.get(code)
    with pytest.raises(UnknownRedemptionCode):  # never a new sale for a forgotten code
        redemptions.redeem(code, "device-bbbb", at=T0)
