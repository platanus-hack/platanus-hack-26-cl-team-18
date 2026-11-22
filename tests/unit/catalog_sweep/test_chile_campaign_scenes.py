"""Every Chile campaign has a camera scene that airs it, and its tags are what YOLO can see.

Two halves:

- **Tags** (pure): every brand's IAB segments are real catalog segments with a Spanish
  label, and one of them is reachable from what the camera reports: a label the
  perception agent emits (edge/agent/vision/vocabulary.py → catalog vision cue) or, for a
  product YOLOE cannot tell apart, the brand's printed marker (Catalog.marker_cues).
  Every vision cue names a label YOLO can emit, so no cue is dead.
- **Scenes** (the real loop): for each of the catalog's campaigns, a group of two holding
  the products (YOLO labels + the printed "product:<slug>" markers) at a plausible hour
  gets exactly that campaign: the right winners, the party-of-two offer, a stock hook that
  names no rival, and a real Chilean photo from the campaign manifest.
"""

from __future__ import annotations

import ast
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.bootstrap.demo import DEMO_SCREEN
from app.bootstrap.demo_catalog_chile import CHILE
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    ConsoleAuction,
    CreativeReady,
    DetectedObject,
    DistanceBand,
    MinorLikely,
    Tracklet,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.modules.creative.application.campaigns import media_for
from app.modules.creative.infrastructure.campaign_assets import load_campaign_media

ROOT = Path(__file__).resolve().parents[3]
IAB = json.loads(
    (ROOT / "app/modules/audience/infrastructure/catalog/iab_audience_1_1_cstore.json").read_text()
)
SEGMENTS = {segment["id"]: segment for segment in IAB["segments"]}
VISION_CUES = IAB["cues"]["vision"]
BRANDS = {brand.slug: brand for brand in CHILE.brands}
_MEDIA = tempfile.TemporaryDirectory(prefix="chile-scenes-")

# Labels on the wire that are deliberately not interests: child props only block
# (invariant 2), the rest are context the classifier reads elsewhere or ignores.
NON_INTEREST_LABELS = {"teddy bear", "stroller", "backpack", "handbag", "cell phone"}
# Cues the camera never emits on purpose: "cake" exercises the related-only affinity case
# in evals/scenarios/11_segment_affinity.json, and a prop must never turn a paid moment
# into the house ad (test_props_fill_sweep), which a cake would in the fictional catalog.
EVAL_ONLY_CUES = {"cake"}


def _yolo_labels() -> set[str]:
    source = (ROOT / "edge" / "agent" / "vision" / "vocabulary.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "OBJECT_VOCABULARY"
        ):
            assert node.value is not None
            return {label for _, label in ast.literal_eval(node.value)}
    raise AssertionError("OBJECT_VOCABULARY not found")


YOLO_LABELS = _yolo_labels()

# What YOLO sees in a hand for each brand's product; None = only its printed marker
# tells it apart (Catalog.marker_cues; measured in edge/agent/vision/vocabulary.py).
SEEN: dict[str, str | None] = {
    "score": "energy drink can",
    "red-bull": "energy drink can",
    "monster": "energy drink can",
    "juan-valdez": "cup",
    "nescafe": "cup",
    "pronto-copec": "pastry",  # and "hot dog" (1404) for the completo
    "coca-cola": "soda can",
    "pepsi": "soda can",
    "bilz-y-pap": "soda can",
    "cachantun": "bottle",
    "benedictino": "bottle",
    "lays": "chips",
    "marco-polo": "chips",
    "super-8": None,
    "ambrosoli": None,
    "powerade": None,  # reads as "bottle" (water): the marker says sports drink
    "gatorade": None,
    "colun": None,
    "soprole": None,
    "watts": "juice",
    "andina-del-valle": "juice",
    "savory": "ice cream",
    "bresler": "ice cream",
}

# Hour (UTC; Santiago is UTC-3) each basket's story happens at. Default: lunch.
HOUR = {
    frozenset({"juan-valdez", "score"}): 5,  # 2 a.m. on the road
    frozenset({"juan-valdez", "red-bull"}): 5,
    frozenset({"monster", "pronto-copec"}): 5,  # night shift
    frozenset({"juan-valdez", "pronto-copec"}): 11,  # breakfast
    frozenset({"colun", "pronto-copec"}): 11,
    frozenset({"pronto-copec", "soprole"}): 11,
    frozenset({"pronto-copec", "watts"}): 11,
    frozenset({"andina-del-valle", "pronto-copec"}): 11,
    frozenset({"cachantun", "savory"}): 19,  # summer afternoon
    frozenset({"bresler", "cachantun"}): 19,
    frozenset({"gatorade", "lays"}): 19,  # the match
    frozenset({"lays", "pepsi"}): 23,  # carrete
    frozenset({"juan-valdez", "super-8"}): 23,
    frozenset({"ambrosoli", "juan-valdez"}): 23,
}
# A completo is a hot dog, not a croissant.
HOT_DOG_BASKETS = {
    frozenset({"coca-cola", "pronto-copec"}),
    frozenset({"bilz-y-pap", "pronto-copec"}),
    frozenset({"monster", "pronto-copec"}),
}


# ------------------------------------------------------------------- tags


def test_every_brand_segment_is_a_real_catalog_segment_with_a_spanish_label() -> None:
    for brand in CHILE.brands:
        assert brand.segments, brand.slug
        for segment_id in brand.segments:
            segment = SEGMENTS.get(segment_id)
            assert segment is not None, (brand.slug, segment_id)
            assert segment.get("label_es"), (brand.slug, segment_id)


def test_every_vision_cue_names_a_label_yolo_can_emit() -> None:
    assert {cue["cue"] for cue in VISION_CUES} - EVAL_ONLY_CUES <= YOLO_LABELS
    assert not EVAL_ONLY_CUES & YOLO_LABELS
    for cue in VISION_CUES:
        assert cue["segment"] in SEGMENTS, cue


def test_every_yolo_label_is_an_interest_or_deliberately_not_one() -> None:
    cued = {cue["cue"] for cue in VISION_CUES}
    assert YOLO_LABELS - cued == NON_INTEREST_LABELS


@pytest.mark.parametrize("slug", sorted(BRANDS))
def test_each_brand_is_seen_by_the_camera_through_its_own_tags(slug: str) -> None:
    brand = BRANDS[slug]
    label = SEEN[slug]
    marker_cues = dict(CHILE.marker_cues)
    if label is None:
        assert set(brand.segments) & {marker_cues[slug]}, (slug, marker_cues.get(slug))
        return
    assert slug not in marker_cues  # a marker cue only where the camera is blind
    assert label in YOLO_LABELS
    cued = {cue["segment"] for cue in VISION_CUES if cue["cue"] == label}
    cued |= {segment for cue, segment in CHILE.cue_segments if cue == label}
    assert set(brand.segments) & cued, (slug, label, brand.segments, cued)


def test_rivals_bid_on_the_same_primary_segment_so_their_auction_is_real() -> None:
    by_category: dict[str, set[str]] = {}
    for brand in CHILE.brands:
        by_category.setdefault(brand.category, set()).add(brand.segments[0])
    for category, primaries in by_category.items():
        if category in {"energy_drink", "candy"}:
            # Score bids fitness first, its rivals energy first: both bid both. Super 8
            # bids chocolate first and candy second, Ambrosoli candy: they meet on candy.
            continue
        assert len(primaries) == 1, (category, primaries)


def test_every_brand_has_a_printed_marker_so_every_basket_can_be_completed() -> None:
    # A challenger's basket (Red Bull, Monster, Bilz, Bresler...) only airs when its can
    # is held: the aisle leader wins the camera's category alone. markers-chile-sheet.svg
    # prints every id below; ArUco DICT_4X4_50 has ids 0-49.
    markers = json.loads((ROOT / "edge/props/markers-chile.json").read_text())
    assert sorted(markers.values()) == sorted(BRANDS)
    assert all(0 <= int(marker) < 50 for marker in markers)
    sheet = (ROOT / "edge/props/markers-chile-sheet.svg").read_text()
    for marker, slug in markers.items():
        assert f"{BRANDS[slug].name.replace('&', '&amp;')} · id {marker}<" in sheet, slug


# ------------------------------------------------------------------- scenes


def _campaigns() -> list[frozenset[str]]:
    return [frozenset(basket.slugs) for basket in CHILE.baskets]


def _snapshot(at: datetime, objects: list[str]) -> AudienceSnapshot:
    return AudienceSnapshot(
        screen_id=DEMO_SCREEN,
        seq=1,
        measured_at=at,
        people=2,
        looking=2,
        tracklets=[
            Tracklet(id=f"t{i}", dwell_s=1.0, looking=True, distance_band=DistanceBand.NEAR)
            for i in (1, 2)
        ],
        objects=[DetectedObject(label=label, score=1) for label in objects],
        minor_likely=MinorLikely(value=False, confidence=1),
    )


def _scene(slugs: frozenset[str]) -> list[str]:
    """What the camera sees: the products in hand, plus the printed markers of a basket.

    A solo is won on the camera's category, not on a held brand, except where only the
    marker can tell the product apart."""
    seen = [label for slug in sorted(slugs) if (label := SEEN[slug]) is not None]
    if slugs in HOT_DOG_BASKETS:
        seen = ["hot dog" if label == "pastry" else label for label in seen]
    markers = [f"product:{slug}" for slug in sorted(slugs) if len(slugs) == 2 or SEEN[slug] is None]
    return [*seen, *markers]


def test_the_table_covers_every_campaign() -> None:
    assert len(_campaigns()) == len(set(_campaigns()))
    assert set(HOUR) <= set(_campaigns())
    assert set(_campaigns()) >= HOT_DOG_BASKETS


@pytest.mark.parametrize("slugs", _campaigns(), ids=lambda s: "+".join(sorted(s)))
def test_each_campaign_airs_in_its_scene(slugs: frozenset[str]) -> None:
    basket = next(b for b in CHILE.baskets if frozenset(b.slugs) == slugs)
    clock = FixedClock(datetime(2026, 11, 21, HOUR.get(slugs, 16), tzinfo=UTC))
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        demo_simulator=True,
        media_dir=Path(_MEDIA.name),
        demo_catalog="chile",
        audience_ai=False,
        creative_ai_copy=False,
        voiceovers_enabled=False,
    )
    container = build_container(settings, clock=clock)
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/console") as console,
        client.websocket_connect(f"/ws/screen/{DEMO_SCREEN}") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        if len(slugs) == 1:
            # The bidding war (/demo/<key>/advertiser): the brand raises its bid 3x, so
            # the challenger, not only the aisle leader, takes the screen.
            (slug,) = slugs
            lines = {
                line["brand_name"]: line["campaign_id"]
                for line in client.get("/api/book").json()["lines"]
            }
            response = client.post(
                "/api/advertiser/bid-multiplier",
                json={
                    "type": "advertiser.bid_multiplier",
                    "v": 1,
                    "campaign_id": lines[BRANDS[slug].name],
                    "multiplier": "3",
                    "seq": 1,
                },
            )
            assert response.status_code < 300, response.text
        agent.send_text(_snapshot(clock.now_utc(), _scene(slugs)).model_dump_json())
        while (message := console.receive_json()).get("type") != "console.auction":
            pass
        auction = ConsoleAuction.model_validate(message)
        assert auction.winners, auction.exclusions
        while (message := player.receive_json()).get("type") != "creative.ready":
            pass
        creative = CreativeReady.model_validate(message)

    names = {BRANDS[slug].name for slug in slugs}
    assert {winner.brand_name for winner in auction.winners} == names
    assert {slot.brand_name for slot in creative.spec.slots} == names

    duo = next(label for party, label, *_ in basket.tiers if party == 2)
    assert auction.offer is not None and auction.offer.label == duo

    teaser = next(stage.copy_text for stage in creative.spec.stages if stage.name == "teaser")
    assert teaser
    rivals = {b.name for b in CHILE.brands if b.slug not in slugs}
    assert not [rival for rival in rivals if rival in teaser], teaser

    found = media_for(
        load_campaign_media(), tuple((slug, BRANDS[slug].category) for slug in sorted(slugs))
    )
    assert found is not None
    photos = found[1].photo_urls
    assert photos[0].rsplit("/", 1)[-1].startswith("cl-"), photos  # a real Chilean lead
    assert creative.spec.slots[0].hero_image_url in photos
