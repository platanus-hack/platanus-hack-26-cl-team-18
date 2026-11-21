"""Copy-only upgrades preserve the current basket's approved truth and media."""

from dataclasses import replace

import pytest

from app.modules.campaigns.domain.book import BrandKit
from app.modules.creative.application.rendering import render_copy_upgrade, render_spec
from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.draft import CopySource
from tests.unit.creative.factory import brief


def test_copy_upgrade_preserves_media_offer_and_identity_but_rechecks_voice() -> None:
    b = brief()
    kits = {
        brand.brand_id: BrandKit(
            brand_id=brand.brand_id,
            name=brand.name,
            logo_url=f"/kits/{index}.svg",
            stock_copy=brand.stock_copy,
            accent_color=None,
        )
        for index, brand in enumerate(b.brands)
    }
    stock = StockCopywriter().compose(b)
    previous = render_spec(b, stock, kits)
    previous = previous.model_copy(
        update={
            "video_url": "/media/loops/approved.mp4",
            "qr_url": "data:image/svg+xml,qr",
            "redemption_code": "BASKET01",
            "redeem_url": "/r/BASKET01",
            "voiceover_url": "/media/old-line.mp3",
            "slots": [
                slot.model_copy(update={"hero_image_url": f"/hero/{slot.brand_id}.png"})
                for slot in previous.slots
            ],
        }
    )
    draft = replace(stock, teaser=stock.teaser + "!", source=CopySource.AI)
    upgraded = render_copy_upgrade(previous, b, draft, kits)
    assert upgraded.model_dump(
        exclude={"slots", "stages", "copy_source", "voiceover_url"}
    ) == previous.model_dump(exclude={"slots", "stages", "copy_source", "voiceover_url"})
    assert upgraded.stages[0].copy_text == draft.teaser
    assert upgraded.copy_source.value == "ai"
    assert upgraded.voiceover_url is None  # must resolve against the current spoken line
    for old, new in zip(previous.slots, upgraded.slots, strict=True):
        assert new.model_dump(exclude={"copy_text"}) == old.model_dump(exclude={"copy_text"})
    other = previous.model_copy(
        update={"slots": [previous.slots[0].model_copy(update={"brand_id": "other"})]}
    )
    with pytest.raises(ValueError, match="brands"):
        render_copy_upgrade(other, b, draft, kits)


def test_without_an_approved_offer_there_is_no_redeem_stage() -> None:
    """Nothing priced, nothing to redeem: the hook and the brand line, never a QR stage."""
    b = brief(with_offer=False)
    kits = {
        brand.brand_id: BrandKit(
            brand_id=brand.brand_id,
            name=brand.name,
            logo_url=f"/kits/{index}.svg",
            stock_copy=brand.stock_copy,
            accent_color=None,
        )
        for index, brand in enumerate(b.brands)
    }
    spec = render_spec(b, StockCopywriter().compose(b), kits)
    assert spec.offer is None
    assert [stage.name.value for stage in spec.stages] == ["teaser", "offer"]
    with_offer = render_spec(brief(), StockCopywriter().compose(brief()), kits)
    assert [stage.name.value for stage in with_offer.stages] == ["teaser", "offer", "close"]
