"""Campaign media: every brand finds imagery, and a returning basket shows a new photo."""

import json

from app.contract.messages import CreativeSlot, CreativeSpec
from app.modules.creative.application.campaigns import (
    CampaignMedia,
    apply_campaign_media,
    category_key,
    media_for,
)
from app.modules.creative.infrastructure.campaign_assets import load_campaign_media

SCORE = CampaignMedia(photo_url="/score.webp", video_url="/score.mp4", alternates=("/ruta.webp",))
DUO = CampaignMedia(photo_url="/duo.webp")
ICE = CampaignMedia(photo_url="/helado.webp")
MEDIA = {"juan-valdez+score": DUO, "score": SCORE, category_key("ice_cream"): ICE}


def test_the_basket_wins_then_the_lead_brand_its_category_and_the_partner() -> None:
    assert media_for(MEDIA, (("score", "energy_drink"), ("juan-valdez", "coffee"))) == (
        "juan-valdez+score",
        DUO,
    )
    assert media_for(MEDIA, (("score", "energy_drink"),)) == ("score", SCORE)
    # No photo of its own: an ice cream brand gets the unbranded ice cream scene,
    # before its partner's photo.
    assert media_for(MEDIA, (("savory", "ice_cream"), ("score", "energy_drink"))) == (
        "category:ice_cream",
        ICE,
    )
    assert media_for(MEDIA, (("cachantun", "water"), ("score", "energy_drink"))) == (
        "score",
        SCORE,
    )
    assert media_for(MEDIA, (("benedictino", "water"),)) is None


def _spec() -> CreativeSpec:
    return CreativeSpec(
        layout_id="00000000-0000-7000-8000-000000000001",
        slots=[
            CreativeSlot(
                brand_id="00000000-0000-7000-8000-000000000002",
                brand_name="Score",
                logo_url="/logo.svg",
                copy_text="Score",
            )
        ],
        duration_ms=8000,
    )


def test_airings_rotate_the_photos_and_the_loop_stays_with_its_own_photo() -> None:
    first = apply_campaign_media(_spec(), SCORE, airing=0)
    second = apply_campaign_media(_spec(), SCORE, airing=1)
    third = apply_campaign_media(_spec(), SCORE, airing=2)
    assert first.slots[0].hero_image_url == "/score.webp" and first.video_url == "/score.mp4"
    # The loop was made from the primary photo: it never plays over another one.
    assert second.slots[0].hero_image_url == "/ruta.webp" and second.video_url is None
    assert third.slots[0].hero_image_url == "/score.webp"


def test_the_manifest_loads_alternates_and_category_scenes(tmp_path) -> None:
    (tmp_path / "photos").mkdir()
    for name in ("cl-score", "cl-ruta", "cl-helado"):
        (tmp_path / "photos" / f"{name}.webp").write_bytes(b"x")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "campaigns": {
                    "score": {
                        "photo": "cl-score",
                        "video": "cl-score",
                        "alternates": ["cl-ruta", "cl-missing"],
                    },
                    "category:ice_cream": {"photo": "cl-helado", "video": "cl-helado"},
                }
            }
        )
    )
    media = load_campaign_media(tmp_path)
    assert media["score"].photo_urls == (
        "/campaign-assets/photos/cl-score.webp",
        "/campaign-assets/photos/cl-ruta.webp",  # a missing file is skipped, not served
    )
    assert media["category:ice_cream"].photo_url == "/campaign-assets/photos/cl-helado.webp"
