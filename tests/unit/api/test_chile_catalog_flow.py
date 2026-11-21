"""DEMO_CATALOG=chile: real brands at a Pronto Copec run the same loop as the fictional seed."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.bootstrap.demo import DEMO_SCREEN, _brand_logo, _slug
from app.bootstrap.demo_catalog import FICTIONAL, catalog_named
from app.bootstrap.demo_catalog_chile import CHILE
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    ConsoleAuction,
    ConsoleMoment,
    CreativeReady,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.main import create_app
from app.modules.campaigns.domain.book import BrandId
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.domain.brief import BrandBrief
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from tests.unit.creative.factory import brief

BRANDS = {brand.slug: brand for brand in CHILE.brands}


def test_the_selector_keeps_the_fictional_catalog_as_default() -> None:
    assert AppSettings(app_env="test").demo_catalog == "fictional"
    assert catalog_named("fictional") is FICTIONAL
    assert catalog_named("chile") is CHILE
    with pytest.raises(ValueError):
        catalog_named("peru")


def test_slugs_match_the_names_markers_and_loops_derive_them_from() -> None:
    assert [brand.slug for brand in CHILE.brands] == [_slug(b.name) for b in CHILE.brands]
    assert _slug("Lay's") == "lays"
    assert _slug("Super 8") == "super-8"
    assert len(BRANDS) == len(CHILE.brands)


def test_baskets_are_two_known_brands_named_in_the_offer_and_every_brand_sells_alone() -> None:
    solo = {next(iter(b.slugs)) for b in CHILE.baskets if len(b.slugs) == 1}
    assert solo == set(BRANDS)
    for basket in CHILE.baskets:
        assert 1 <= len(basket.slugs) <= 2  # ADR 0008 D4: at most two brands
        assert basket.slugs <= set(BRANDS)
        for slug in basket.slugs:
            assert BRANDS[slug].name in basket.names  # the copy guard needs every name
        sizes = [party for party, *_ in basket.tiers]
        assert sizes == sorted(sizes) and sizes[0] == 1
        prices = [price for *_, price in basket.tiers]
        assert prices == sorted(prices)
        assert all(str(price).endswith("90") for price in prices)


def test_partner_baskets_never_pair_rivals() -> None:
    for basket in CHILE.baskets:
        categories = [BRANDS[slug].category for slug in basket.slugs]
        assert len(set(categories)) == len(categories)


def test_the_main_aisles_have_a_rival_so_vcg_prices_are_real() -> None:
    per_category = Counter(brand.category for brand in CHILE.brands)
    for category in (
        "energy_drink",
        "coffee",
        "cola",
        "water",
        "snacks",
        "candy",
        "sports_drink",
        "dairy",
        "juice",
        "ice_cream",
    ):
        assert per_category[category] >= 2, category


def test_alto_en_products_are_never_shown_to_a_group_with_a_child() -> None:
    # Ley 20.606: no advertising of "ALTO EN" foods to children under 14.
    assert {slug for slug, b in BRANDS.items() if b.age_restricted} == {
        "lays",
        "marco-polo",
        "super-8",
        "ambrosoli",
        "colun",
        "soprole",
        "watts",
        "andina-del-valle",
        "savory",
        "bresler",
    }
    # No alcohol at a gas station (Ley 21.363).
    assert "alcohol" not in {brand.category for brand in CHILE.brands}


def test_a_brand_whose_name_contains_a_known_brand_is_not_its_own_competitor() -> None:
    # "Bilz y Pap" contains "Bilz" (KNOWN_BRANDS); "Pronto Copec" contains "Copec".
    bilz = BrandBrief(
        brand_id=BrandId.create("00000000-0000-7000-8000-0000000000b1"),
        name="Bilz y Pap",
        category="cola",
        stock_copy="Bilz y Pap · Bien heladas, para el completo.",
    )
    own = replace(brief(), brands=(bilz,), layout="solo", offer=None)
    draft = CopyDraft(
        teaser="Completo y Bilz bien helada.",
        offer="Bilz y Pap, bien heladas.",
        close="Escanea y canjea en caja.",
        voice_line="Bilz y Pap, bien heladas.",
        source=CopySource.AI,
    )
    assert ViolationKind.COMPETITOR_NAME not in check_copy(draft, own).kinds()
    rival = replace(draft, teaser="Más rica que Coca-Cola.")
    assert ViolationKind.COMPETITOR_NAME in check_copy(rival, own).kinds()


def test_official_logos_are_used_where_present_and_a_wordmark_elsewhere() -> None:
    red_bull = BRANDS["red-bull"]
    assert _brand_logo(red_bull.slug, red_bull.name, red_bull.color) == (
        "/campaign-assets/logos/red-bull.svg"
    )
    for slug in ("powerade", "gatorade", "soprole", "watts", "bresler"):
        brand = BRANDS[slug]
        assert _brand_logo(slug, brand.name, brand.color) == f"/campaign-assets/logos/{slug}.svg"
    score = BRANDS["score"]
    assert _brand_logo(score.slug, score.name, score.color).startswith("data:image/svg+xml")


def _snapshot(seq: int, at: datetime, objects: list[DetectedObject]) -> AudienceSnapshot:
    return AudienceSnapshot(
        screen_id=DEMO_SCREEN,
        seq=seq,
        measured_at=at,
        people=2,
        looking=2,
        tracklets=[
            Tracklet(id=f"t{i}", dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
            for i in (1, 2)
        ],
        objects=objects,
        minor_likely=MinorLikely(value=False, confidence=1),
    )


def test_a_duo_with_an_energy_can_and_a_coffee_gets_the_score_and_juan_valdez_basket(
    tmp_path,
) -> None:
    clock = FixedClock(datetime(2026, 11, 21, 2, tzinfo=UTC))
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, demo_catalog="chile"
    )
    container = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        objects = [
            DetectedObject(label="energy drink can", score=1),
            DetectedObject(label="cup", score=1),
        ]
        agent.send_text(_snapshot(1, clock.now_utc(), objects).model_dump_json())
        first = ConsoleMoment.model_validate(console.receive_json())
        auction = ConsoleAuction.model_validate(console.receive_json())
        console.receive_json()  # latency
        creative = CreativeReady.model_validate(player.receive_json())

        # At a gas station an energy can is a tired-driver moment, not a gym exit.
        assert "Energy Drinks" in {segment.label for segment in first.segments}
        assert "Fitness and Exercise" not in {segment.label for segment in first.segments}
        assert creative.spec.angle in {"energy", "coffee_break"}
        assert [slot.brand_name for slot in creative.spec.slots] == ["Score", "Juan Valdez"]
        assert creative.spec.offer is not None and creative.spec.offer.price.amount == "7490"
        assert creative.spec.slots[1].logo_url == "/campaign-assets/logos/juan-valdez.svg"

        assert {winner.brand_name for winner in auction.winners} == {"Score", "Juan Valdez"}
        assert auction.offer is not None
        assert auction.offer.label == "Dúo"
        assert auction.offer.price.amount == "7490"
        # Red Bull bid for Score's slot inside the same basket and set its price.
        assert any(bid.brand_name == "Red Bull" for bid in auction.bids)

        # A held Juan Valdez cup (printed marker) is a coffee product of this catalog.
        now = clock.advance(timedelta(milliseconds=100))
        marker = DetectedObject(label="product:juan-valdez", score=0.95)
        agent.send_text(_snapshot(2, now, [*objects, marker]).model_dump_json())
        moment = ConsoleMoment.model_validate(console.receive_json())
        assert [(p.category, p.brand_ref) for p in moment.held_products] == [
            ("coffee", "juan-valdez")
        ]


def _creative(player) -> CreativeReady:
    while True:
        message = player.receive_json()
        if message.get("type") == "creative.ready":
            return CreativeReady.model_validate(message)


def test_a_basket_that_comes_back_shows_its_next_photo_and_every_basket_has_one(
    tmp_path,
) -> None:
    clock = FixedClock(datetime(2026, 11, 21, 2, tzinfo=UTC))
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, demo_catalog="chile"
    )
    container = build_container(settings, clock=clock)
    night = [
        DetectedObject(label="energy drink can", score=1),
        DetectedObject(label="cup", score=1),
    ]
    lunch = [DetectedObject(label="hot dog", score=1), DetectedObject(label="soda can", score=1)]
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        aired = []
        for seq, objects in enumerate((night, lunch, night), start=1):
            agent.send_text(_snapshot(seq, clock.now_utc(), objects).model_dump_json())
            creative = _creative(player)
            clock.advance(timedelta(seconds=5))
            aired.append(
                (
                    [slot.brand_name for slot in creative.spec.slots],
                    creative.spec.slots[0].hero_image_url,
                )
            )
    (first_brands, first_photo), (lunch_brands, lunch_photo), (back_brands, back_photo) = aired
    assert first_brands == back_brands == ["Score", "Juan Valdez"]
    assert lunch_brands != first_brands
    assert first_photo and lunch_photo and back_photo
    assert back_photo != first_photo  # the returning basket rotates to its next photo
