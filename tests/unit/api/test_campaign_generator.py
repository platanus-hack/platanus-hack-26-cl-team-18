"""The campaign studio produces guarded, media-rich drafts without changing the live market."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config.settings import AppSettings
from app.contract.messages import CampaignCatalog, CampaignGenerated
from app.main import create_app


@pytest.fixture
def studio(tmp_path) -> Iterator[TestClient]:
    with TestClient(
        create_app(
            AppSettings(
                _env_file=None,
                app_env="test",
                media_dir=tmp_path,
                audience_ai=False,
                creative_ai_copy=False,
                voiceovers_enabled=False,
            )
        )
    ) as client:
        yield client


def test_catalog_comes_from_the_live_market_and_serves_its_local_media(studio: TestClient) -> None:
    catalog = CampaignCatalog.model_validate(studio.get("/api/campaigns/catalog").json())
    assert {brand.name for brand in catalog.brands} == {"Voltio", "Café Patio", "Rayo", "Malta Sur"}
    assert {tuple(pair) for pair in catalog.approved_baskets} == {
        ("cafe-patio", "voltio"),
        ("cafe-patio", "rayo"),
    }
    for brand in catalog.brands:
        assert brand.photo_url is not None
        photo = studio.get(brand.photo_url)
        assert photo.status_code == 200 and photo.headers["content-type"] == "image/webp"
        assert len(photo.content) < 400_000


@pytest.mark.parametrize(
    "party,price", [(1, "3490"), (2, "5990"), (3, "8490"), (4, "10990"), (12, "10990")]
)
@pytest.mark.parametrize("media", ["photo", "video"])
def test_cobrand_drafts_preserve_real_assets_and_approved_prices_without_spending(
    studio: TestClient,
    party: int,
    price: str,
    media: str,
) -> None:
    before = studio.get("/api/book").json()
    request = {"brands": ["voltio", "cafe-patio"], "party_size": party, "media": media}
    response = studio.post("/api/campaigns/generate", json=request)
    assert response.status_code == 200
    campaign = CampaignGenerated.model_validate(response.json())
    assert campaign.status == "draft" and not campaign.cache_hit
    assert campaign.spec.offer is not None and campaign.spec.offer.price.amount == price
    assert {slot.brand_name for slot in campaign.spec.slots} == {"Voltio", "Café Patio"}
    assert all(
        slot.hero_image_url == "/campaign-assets/photos/energy-coffee.webp"
        for slot in campaign.spec.slots
    )
    assert (campaign.spec.video_url is not None) == (media == "video")
    if campaign.spec.video_url:
        clip = studio.get(campaign.spec.video_url, headers={"range": "bytes=0-31"})
        assert clip.status_code == 206 and clip.content[4:8] == b"ftyp"
    assert campaign.spec.qr_url is campaign.spec.redemption_code is None
    again = CampaignGenerated.model_validate(
        studio.post(
            "/api/campaigns/generate", json={**request, "brands": list(reversed(request["brands"]))}
        ).json()
    )
    assert (
        again.cache_hit
        and again.campaign_key == campaign.campaign_key
        and again.spec == campaign.spec
    )
    assert studio.get("/api/book").json() == before


@pytest.mark.parametrize(
    "payload",
    [
        {"brands": []},
        {"brands": ["unknown"]},
        {"brands": ["voltio", "voltio"]},
        {"brands": ["voltio", "rayo"]},
        {"brands": ["malta-sur", "cafe-patio"]},
        {"brands": ["malta-sur"], "minor_likely": True},
        {"brands": ["voltio"], "party_size": 0},
        {"brands": ["voltio"], "party_size": 13},
        {"brands": ["voltio"], "price": "1"},
        {"brands": ["voltio"], "moment": "complete_basket"},
    ],
)
def test_unapproved_unsafe_or_ambiguous_campaigns_are_rejected(
    studio: TestClient, payload: dict
) -> None:
    assert studio.post("/api/campaigns/generate", json=payload).status_code == 422


def test_missing_optional_video_still_renders_photography(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from app.contract.messages import CreativeSlot, CreativeSpec
    from app.modules.creative.application.campaigns import apply_campaign_media
    from app.modules.creative.infrastructure.campaign_assets import load_campaign_media

    (tmp_path / "photos").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"campaigns":{"cafe-patio":{"photo":"coffee","video":"coffee"}}}'
    )
    Image.new("RGB", (16, 9)).save(tmp_path / "photos" / "coffee.webp")
    library = load_campaign_media(tmp_path)
    assert library["cafe-patio"].video_url is None
    spec = apply_campaign_media(
        CreativeSpec(
            layout_id="solo",
            duration_ms=12000,
            slots=[
                CreativeSlot(
                    brand_id="cafe", brand_name="Café Patio", logo_url="/kit.svg", copy_text="Café"
                )
            ],
        ),
        library["cafe-patio"],
    )
    assert spec.video_url is None and spec.slots[0].hero_image_url
