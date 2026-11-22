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


def test_the_chile_catalog_is_the_default_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_CATALOG", raising=False)  # tests/conftest.py pins fictional
    for env in ("local", "test", "prod"):
        assert AppSettings(app_env=env, _env_file=None).demo_catalog == "chile"
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
    # Ley 20.606: no advertising of "ALTO EN" foods to children under 14; Ley 21.363
    # treats an alcohol brand's 0.0 line as alcohol advertising (Guinness 0.0).
    # Sugared energy drinks carry "ALTO EN AZÚCARES" too (Score's own ad shows the seal).
    assert {slug for slug, b in BRANDS.items() if b.age_restricted} == {
        "score",
        "red-bull",
        "monster",
        "guinness-0-0",
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
    # No alcohol at a gas station (Ley 21.363): the only beer brand sells its 0.0.
    assert "alcohol" not in {brand.category for brand in CHILE.brands}
    assert {b.slug for b in CHILE.brands if b.category.startswith("beer")} == {"guinness-0-0"}


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
    savory = BRANDS["savory"]
    assert _brand_logo(savory.slug, savory.name, savory.color).startswith("data:image/svg+xml")


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
        assert "Bebidas energéticas" in {segment.label for segment in first.segments}
        assert "Deporte y ejercicio" not in {segment.label for segment in first.segments}
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


def test_every_chilean_brand_and_basket_airs_a_photo_and_most_rotate() -> None:
    from app.modules.creative.application.campaigns import media_for
    from app.modules.creative.infrastructure.campaign_assets import load_campaign_media

    media = load_campaign_media()
    for basket in CHILE.baskets:
        slots = tuple((slug, BRANDS[slug].category) for slug in sorted(basket.slugs))
        found = media_for(media, slots)
        assert found is not None, basket.names
    rotating = [
        brand.slug
        for brand in CHILE.brands
        if (found := media_for(media, ((brand.slug, brand.category),)))
        and len(found[1].photo_urls) > 1
    ]
    assert len(rotating) >= len(CHILE.brands) * 2 // 3


@pytest.mark.parametrize(
    ("brands", "party", "hook"),
    [
        (["savory"], 1, "Antojo de algo frío."),
        (["super-8"], 3, "Un antojo dulce para los tres."),
        (["watts"], 2, "Algo rico para los dos antes de partir."),
    ],
)
def test_a_craving_gets_its_own_hook_not_generic_hunger(
    tmp_path, brands: list[str], party: int, hook: str
) -> None:
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        demo_catalog="chile",
        media_dir=tmp_path,
        audience_ai=False,
        creative_ai_copy=False,
        voiceovers_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/campaigns/generate",
            json={"brands": brands, "party_size": party, "moment": "hunger", "media": "photo"},
        )
    assert response.status_code == 200
    teaser = next(s for s in response.json()["spec"]["stages"] if s["name"] == "teaser")
    assert teaser["copy_text"] == hook


def test_completo_and_a_beer_at_night_airs_guinness_zero_and_a_child_swaps_it_out(
    tmp_path,
) -> None:
    # The team's wow beat: Dominó's completo + Guinness 0.0 for adults; a child steps
    # into frame and the age-restricted 0.0 beer is gone on the next decision.
    clock = FixedClock(datetime(2026, 11, 21, 23, tzinfo=UTC))  # 8 p.m. at the venue
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, demo_catalog="chile"
    )
    container = build_container(settings, clock=clock)
    objects = [
        DetectedObject(label="hot dog", score=1),
        DetectedObject(label="beer bottle", score=1),
    ]
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        agent.send_text(_snapshot(1, clock.now_utc(), objects).model_dump_json())
        adults = _creative(player)
        assert {slot.brand_name for slot in adults.spec.slots} == {"Dominó", "Guinness 0.0"}
        assert adults.spec.offer is not None
        assert adults.spec.offer.label == "Completo + Guinness 0.0 para 2"
        teaser = next(s.copy_text for s in adults.spec.stages if s.name == "teaser")
        assert "cero alcohol" in teaser

        now = clock.advance(timedelta(seconds=5))
        child = _snapshot(2, now, objects).model_copy(
            update={"minor_likely": MinorLikely(value=True, confidence=1)}
        )
        agent.send_text(child.model_dump_json())
        swapped = _creative(player)
        assert "Guinness 0.0" not in {slot.brand_name for slot in swapped.spec.slots}


def test_a_zero_alcohol_line_gets_its_brand_initial_not_the_abv() -> None:
    import base64

    guinness = BRANDS["guinness-0-0"]
    logo = _brand_logo(guinness.slug, guinness.name, guinness.color)
    svg = base64.b64decode(logo.removeprefix("data:image/svg+xml;base64,")).decode()
    assert ">G</text>" in svg


def _aired_with_boost(tmp_path, objects: list[str], hour: int, boost: dict[str, str]) -> list[str]:
    clock = FixedClock(datetime(2026, 11, 21, hour, tzinfo=UTC))
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, demo_catalog="chile"
    )
    container = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        lines = {
            line["brand_name"]: line["campaign_id"]
            for line in client.get("/api/book").json()["lines"]
        }
        for seq, (brand, multiplier) in enumerate(boost.items(), start=1):
            response = client.post(
                "/api/advertiser/bid-multiplier",
                json={
                    "type": "advertiser.bid_multiplier",
                    "v": 1,
                    "campaign_id": lines[brand],
                    "multiplier": multiplier,
                    "seq": seq,
                },
            )
            assert response.status_code < 300, response.text
        found = [DetectedObject(label=label, score=1) for label in objects]
        agent.send_text(_snapshot(1, clock.now_utc(), found).model_dump_json())
        return [slot.brand_name for slot in _creative(player).spec.slots]


@pytest.mark.parametrize(
    ("objects", "hour", "boost", "aired"),
    [
        # The hero at 2 a.m.: Red Bull raises a little to a lot and takes Score's seat,
        # the basket stays (co-marketing, DemoBasket.bonus_clp_per_as)...
        (["energy drink can", "cup"], 5, {}, ["Score", "Juan Valdez"]),
        (["energy drink can", "cup"], 5, {"Red Bull": "1.5"}, ["Red Bull", "Juan Valdez"]),
        (["energy drink can", "cup"], 5, {"Red Bull": "3"}, ["Red Bull", "Juan Valdez"]),
        # ...and only an all-in raise buys the whole screen alone.
        (["energy drink can", "cup"], 5, {"Red Bull": "5"}, ["Red Bull"]),
        # The completo at lunch: Dominó takes Pronto's seat, Bilz takes Coca-Cola's.
        (["hot dog", "soda can"], 16, {}, ["Pronto Copec", "Coca-Cola"]),
        (["hot dog", "soda can"], 16, {"Dominó": "2"}, ["Dominó", "Coca-Cola"]),
        (["hot dog", "soda can"], 16, {"Bilz y Pap": "2"}, ["Bilz y Pap", "Pronto Copec"]),
    ],
)
def test_the_bidding_war_swaps_a_seat_in_the_basket_before_it_buys_the_screen(
    tmp_path, objects: list[str], hour: int, boost: dict[str, str], aired: list[str]
) -> None:
    assert sorted(_aired_with_boost(tmp_path, objects, hour, boost)) == sorted(aired)


def test_with_a_child_in_the_group_the_hero_gives_way_to_the_coffee(tmp_path) -> None:
    clock = FixedClock(datetime(2026, 11, 21, 5, tzinfo=UTC))  # 2 a.m.: the hero's hour
    settings = AppSettings(
        app_env="test", demo_simulator=True, media_dir=tmp_path, demo_catalog="chile"
    )
    container = build_container(settings, clock=clock)
    objects = [
        DetectedObject(label="energy drink can", score=1),
        DetectedObject(label="cup", score=1),
    ]
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        child = _snapshot(1, clock.now_utc(), objects).model_copy(
            update={"minor_likely": MinorLikely(value=True, confidence=1)}
        )
        agent.send_text(child.model_dump_json())
        aired = {slot.brand_name for slot in _creative(player).spec.slots}
    energy = {b.name for b in CHILE.brands if b.category in {"energy_drink", "energy_pack"}}
    assert aired and not aired & energy
    assert "Juan Valdez" in aired
