"""DEMO_CATALOG=chile, speech mode: what a shopper says reaches the aisle that sells it.

Agent keyterms (app/contract/speech_matching.py, the hot path, no model) → the market's
keyterm segments (Catalog.cue_segments) → auction → the screen. Each phrase names a
product; the brand that airs sells it and its hook fits ("tengo sed" is water, never
"Pilas bajas"). Words with another meaning in Chilean Spanish ("está helado", "mis
papás", "qué dulce eres") never sell anything. Beer is answered with Guinness 0.0, never
alcohol (Ley 21.363), and never to a group with a child.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import AudienceSnapshot, AudienceUtterance, CreativeReady, MinorLikely
from app.contract.speech_matching import DEFAULT_KEYTERMS
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

AT = datetime(2026, 11, 21, 16, tzinfo=UTC)  # 1 p.m. at the venue


def _aired(
    text: str, tmp_path: Path, *, minor: bool = False
) -> tuple[tuple[str, ...], list[str], str]:
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        demo_catalog="chile",
        media_dir=tmp_path,
        speech_mode_screens=["demo-01"],
        anthropic_api_key=None,
        elevenlabs_api_key=None,
        fal_key=None,
        voiceovers_enabled=False,
        vlm_enabled=False,
    )
    clock = FixedClock(AT)
    container = build_container(settings, clock=clock)
    hits = DEFAULT_KEYTERMS.understand(text).wanted
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/demo-01") as player,
        client.websocket_connect("/ws/agent") as agent,
    ):
        player.receive_json()
        agent.send_text(
            AudienceSnapshot(
                screen_id="demo-01",
                seq=1,
                measured_at=AT,
                people=1,
                looking=1,
                minor_likely=MinorLikely(value=minor, confidence=1),
            ).model_dump_json()
        )
        agent.send_text(
            AudienceUtterance(
                screen_id="demo-01",
                seq=1,
                measured_at=AT,
                text=text,
                language="es",
                keyterms_hit=list(hits),
                utterance_id="u1",
            ).model_dump_json()
        )
        time.sleep(0.3)
        assert client.portal is not None
        client.portal.call(container.warm.drain)
        with client.websocket_connect("/ws/screen/demo-01") as reconnected:
            shown = CreativeReady.model_validate(reconnected.receive_json())
    brands = [slot.brand_name for slot in shown.spec.slots] if shown.auction_id else []
    teaser = next((s.copy_text for s in shown.spec.stages if s.name == "teaser"), "")
    return hits, brands, teaser


@pytest.mark.parametrize(
    ("text", "keyterm", "brand", "hook_word"),
    [
        ("tengo sed", "sed", "Cachantun", "Sed"),
        ("quiero agua", "sed", "Cachantun", "Sed"),
        ("algo isotónico", "isotónica", "Gatorade", "Hidrátate"),
        ("estoy muy cansado", "energía", "Score", "Pilas"),
        ("un cafecito", "café", "Juan Valdez", "Café"),
        ("tengo hambre", "hambre", "Pronto Copec", "Hambre"),
        ("quiero un completo", "comida", "Pronto Copec", "Hambre"),
        ("una bebida", "bebida", "Coca-Cola", ""),
        ("quiero un helado", "helado", "Savory", "frío"),
        ("unas papitas", "papas", "Lay's", ""),
        ("quiero un chocolate", "chocolate", "Super 8", "dulce"),
        ("algo dulce", "dulce", "Ambrosoli", "dulce"),
        ("un jugo", "jugo", "Watt's", "rico"),
        ("una chocolatada", "leche", "Colun", "chocolate"),
    ],
)
def test_a_spoken_product_airs_the_aisle_that_sells_it(
    tmp_path: Path, text: str, keyterm: str, brand: str, hook_word: str
) -> None:
    hits, brands, teaser = _aired(text, tmp_path)
    assert keyterm in hits
    assert brands == [brand]
    assert hook_word in teaser
    assert "Pilas" not in teaser or keyterm == "energía"


@pytest.mark.parametrize(
    "text",
    ["está helado afuera", "mis papás vienen", "qué dulce eres", "no quiero helado"],
)
def test_another_meaning_or_a_no_sells_nothing(tmp_path: Path, text: str) -> None:
    hits, brands, _ = _aired(text, tmp_path)
    assert hits == ()
    assert brands == []


def test_a_beer_at_a_gas_station_is_answered_with_its_zero_alcohol_line(tmp_path: Path) -> None:
    # Ley 21.363: no alcohol sold here. "Una cerveza" airs Guinness 0.0, and so does
    # asking for one without alcohol (the beer itself is rejected in that clause).
    for text, keyterm in (("una cerveza", "cerveza"), ("una cerveza sin alcohol", "sin alcohol")):
        hits, brands, teaser = _aired(text, tmp_path)
        assert keyterm in hits
        assert brands == ["Guinness 0.0"]
        assert "cero alcohol" in teaser


def test_a_child_in_the_group_blocks_the_zero_alcohol_beer(tmp_path: Path) -> None:
    hits, brands, _ = _aired("una cerveza", tmp_path, minor=True)
    assert hits == ("cerveza",)
    assert "Guinness 0.0" not in brands
