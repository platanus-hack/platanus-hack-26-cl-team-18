from __future__ import annotations

import pytest

from app.contract.messages import CreativeOffer, CreativeSlot, CreativeSpec, MoneyDTO
from app.modules.creative.application.speech_text import (
    offer_line,
    spanish_number,
    speakable,
    spoken_line_for,
)


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "cero"),
        (1, "uno"),
        (16, "dieciséis"),
        (21, "veintiuno"),
        (30, "treinta"),
        (45, "cuarenta y cinco"),
        (100, "cien"),
        (101, "ciento uno"),
        (490, "cuatrocientos noventa"),
        (1000, "mil"),
        (3490, "tres mil cuatrocientos noventa"),
        (5990, "cinco mil novecientos noventa"),
        (21000, "veintiuno mil"),
        (1_000_000, "un millón"),
        (2_500_000, "dos millones quinientos mil"),
    ],
)
def test_spanish_number(n: int, words: str) -> None:
    assert spanish_number(n) == words


def test_spanish_number_range() -> None:
    with pytest.raises(ValueError):
        spanish_number(-1)


def test_prices_counts_and_symbols_are_spoken_naturally() -> None:
    assert speakable("Voltio + Café Patio por $3.490") == (
        "Voltio y Café Patio por tres mil cuatrocientos noventa pesos"
    )
    assert speakable("Voltio + Café Patio x2 por $5.990") == (
        "Voltio y Café Patio para dos por cinco mil novecientos noventa pesos"
    )
    assert speakable("$21.000") == "veintiún mil pesos"
    assert speakable("$1") == "un peso"
    assert speakable("$100") == "cien pesos"
    assert speakable("Voltio · Energía") == "Voltio. Energía"
    assert speakable("2 combos") == "dos combos"


def test_offer_line_uses_the_approved_fields() -> None:
    assert offer_line("Dúo post-entreno", "2 combos Voltio + Café Patio", "5990") == (
        "Dúo post-entreno: dos combos Voltio y Café Patio por cinco mil novecientos noventa pesos"
    )


def spec(offer: CreativeOffer | None, copy: str = "IA: ¡algo distinto cada vez!") -> CreativeSpec:
    return CreativeSpec(
        layout_id="duo",
        slots=[CreativeSlot(brand_id="b", brand_name="Voltio", logo_url="/l.svg", copy_text=copy)],
        duration_ms=8000,
        offer=offer,
    )


def test_spoken_line_prefers_the_stable_offer_over_varying_ai_copy() -> None:
    offer = CreativeOffer(
        label="Dúo post-entreno",
        text="2 combos Voltio + Café Patio",
        price=MoneyDTO(amount="5990", currency="CLP"),
        party_size=2,
    )
    first = spoken_line_for(spec(offer, "IA: versión uno"))
    second = spoken_line_for(spec(offer, "IA: versión dos, distinta"))
    assert first == second  # same offer -> same line -> one cached file
    assert "pesos" in first and "$" not in first


def test_without_an_offer_the_lead_copy_is_spoken() -> None:
    assert spoken_line_for(spec(None, "Voltio · Energía para tu entreno.")) == (
        "Voltio. Energía para tu entreno."
    )
