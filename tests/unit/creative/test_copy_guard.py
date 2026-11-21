"""The copy guard, one table row per rule (what may air)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import CopyGuard, ViolationKind, check_copy
from tests.unit.creative.factory import MIGA, PATIO, brief

GROUP = brief(party=4, angle=Angle.HUNGER)
GOOD = CopyDraft(
    teaser="¿Con hambre los 4? Voltio + Café Patio",
    offer="Para los 4 · Pack x4: Voltio + Café Patio por $10.970",
    close="Escanea el QR · válido 10 min",
    voice_line="Para los 4, Voltio y Café Patio por 10.970 pesos",
    source=CopySource.AI,
)


def test_good_copy_passes() -> None:
    verdict = check_copy(GOOD, GROUP)
    assert verdict.passed, verdict.violations
    assert CopyGuard().is_satisfied_by((GOOD, GROUP))


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"offer": "Para los 4 · Voltio + Café Patio por $9.990"}, ViolationKind.PRICE_CHANGED),
        ({"offer": "Para los 4 · Voltio + Café Patio, ¡aprovecha!"}, ViolationKind.PRICE_MISSING),
        ({"teaser": "¿Hambre? Mejor que Rayo"}, ViolationKind.COMPETITOR_NAME),
        ({"teaser": "¿Hambre? Voltio + Miga"}, ViolationKind.COMPETITOR_NAME),
        (
            {"teaser": "¿Hambre? Voltio", "offer": "Para los 4 · Pack x4 por $10.970"},
            ViolationKind.BRAND_MISSING,
        ),
        ({"teaser": "¿Hambre los 3? Voltio + Café Patio"}, ViolationKind.PARTY_MISMATCH),
        ({"close": "Dos personas, un QR"}, ViolationKind.PARTY_MISMATCH),
        ({"teaser": "El mejor café, Voltio + Café Patio"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "50% off con el QR"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "¡Gratis con el QR!"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"teaser": "Te vemos con hambre, Voltio"}, ViolationKind.SENSITIVE_INFERENCE),
        ({"teaser": "¿Embarazada? Voltio + Café Patio"}, ViolationKind.SENSITIVE_INFERENCE),
        ({"close": "Pide en voltio.cl"}, ViolationKind.CONTACT_OR_LINK),
        ({"close": "Llama al +56 9 1234 5678"}, ViolationKind.CONTACT_OR_LINK),
        ({"close": ""}, ViolationKind.EMPTY),
        # Price evasions: spelled out, slang, bare numbers, a reference price.
        ({"voice_line": "Para los 4, por diez mil pesos"}, ViolationKind.PRICE_CHANGED),
        ({"close": "Ahorra 2 lucas con el QR"}, ViolationKind.PRICE_CHANGED),
        ({"close": "Antes 12990, hoy con el QR"}, ViolationKind.PRICE_CHANGED),
        ({"close": "Antes 12.990 pesos · QR"}, ViolationKind.PRICE_CHANGED),
        ({"close": "Con el QR, 2x1"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "Con el QR, descuento extra"}, ViolationKind.UNAPPROVED_CLAIM),
        # Hidden or look-alike characters that would dodge the name check.
        ({"teaser": "¿Hambre? Ra\u200byo + Voltio + Café Patio"}, ViolationKind.UNSAFE_CHARACTERS),
        ({"teaser": "¿Hambre? R\u0430yo, Voltio + Café Patio"}, ViolationKind.UNSAFE_CHARACTERS),
        ({"close": "Escanea el QR 🍔"}, ViolationKind.UNSAFE_CHARACTERS),
        # Found by the independent review: real brands, split names, claims, creepy lines.
        ({"teaser": "Más rico que Red Bull: Voltio + Café Patio"}, ViolationKind.COMPETITOR_NAME),
        ({"close": "Olvídate de Monster · QR"}, ViolationKind.COMPETITOR_NAME),
        ({"close": "Mejor que Starbucks · QR"}, ViolationKind.COMPETITOR_NAME),
        ({"close": "Más que los Rayos · QR"}, ViolationKind.COMPETITOR_NAME),
        ({"close": "Ni R-a-y-o · QR"}, ViolationKind.COMPETITOR_NAME),
        ({"close": "A mitad de precio con el QR"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "Lo mejor de Chile · QR"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "El #1 del barrio · QR"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "Súper sano · QR"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "Por 9k con el QR"}, ViolationKind.PRICE_CHANGED),
        ({"close": "Los veo cansados · QR"}, ViolationKind.SENSITIVE_INFERENCE),
        ({"close": "Se nota que entrenaron · QR"}, ViolationKind.SENSITIVE_INFERENCE),
        ({"close": "Síguenos en instagram voltio"}, ViolationKind.CONTACT_OR_LINK),
        (
            {"teaser": "uno dos tres cuatro cinco seis siete ocho nueve diez"},
            ViolationKind.TOO_LONG,
        ),
    ],
)
def test_each_rule_rejects(changes: dict[str, str], kind: ViolationKind) -> None:
    verdict = check_copy(replace(GOOD, **changes), GROUP)
    assert kind in verdict.kinds(), verdict.violations


def test_correct_amount_with_pesos_and_small_numbers_pass() -> None:
    """The approved price in any digit form is fine; minutes and party sizes are not prices."""
    draft = replace(
        GOOD,
        close="Listo en 2 minutos · válido 10 min",
        voice_line="Para los 4, Voltio y Café Patio por 10970 pesos",
    )
    assert check_copy(draft, GROUP).passed, check_copy(draft, GROUP).violations


@pytest.mark.parametrize("word", ["birra", "whisky", "ron", "cervezas", "vodka", "beer"])
def test_more_alcohol_words_are_blocked_with_a_minor(word: str) -> None:
    draft = replace(GOOD, close=f"Y una {word} después · QR")
    minor = brief(party=4, angle=Angle.HUNGER, minor=True)
    assert ViolationKind.RESTRICTED_FOR_MINOR in check_copy(draft, minor).kinds()


def test_a_group_bigger_than_the_top_tier_is_never_counted() -> None:
    """5 looking, the best offer is a 4-pack: "Para los 5" or "los 4" would both mislead."""
    five = brief(party=5, angle=Angle.HUNGER)
    assert five.offer is not None and five.offer.min_party == 4
    assert five.addressed_party is None
    stock = StockCopywriter().compose(five)
    assert "todo el grupo" in stock.offer and check_copy(stock, five).passed
    for line in (
        "Para los 5 · Pack x4: Voltio + Café Patio por $10.970",
        "Para los 4 · Pack x4: Voltio + Café Patio por $10.970",
    ):
        assert ViolationKind.PARTY_MISMATCH in check_copy(replace(stock, offer=line), five).kinds()


def test_alcohol_words_only_blocked_when_a_minor_is_likely() -> None:
    draft = replace(GOOD, close="Para el after, una cerveza · QR")
    assert (
        ViolationKind.RESTRICTED_FOR_MINOR
        in check_copy(draft, brief(party=4, angle=Angle.HUNGER, minor=True)).kinds()
    )
    assert ViolationKind.RESTRICTED_FOR_MINOR not in check_copy(draft, GROUP).kinds()


def test_approved_offer_words_are_allowed() -> None:
    """A party number or discount that the brand itself approved is not a violation."""
    solo = brief(party=1, angle=Angle.HUNGER)
    draft = CopyDraft(
        teaser="¿Hambre? Voltio + Café Patio",
        offer="Combo post-entreno: Voltio + Café Patio por $3.490",
        close="Escanea el QR · válido 10 min",
        voice_line="Combo post-entreno, Voltio y Café Patio",
        source=CopySource.STOCK,
    )
    assert check_copy(draft, solo).passed


@pytest.mark.parametrize("party", [0, 1, 2, 3, 4, 5, 6, 8])
@pytest.mark.parametrize("angle", list(Angle))
@pytest.mark.parametrize("minor", [False, True])
@pytest.mark.parametrize("basket", ["duo", "duo-food", "solo-no-offer"])
def test_stock_copy_is_always_guard_clean(
    party: int, angle: Angle, minor: bool, basket: str
) -> None:
    """The fallback can never be the thing that breaks an invariant."""
    held = "Café Patio" if angle is Angle.COMPLETE_BASKET else None
    if basket == "duo":
        b = brief(party=party, people=max(party, 1), angle=angle, minor=minor, held=held)
    elif basket == "duo-food":
        b = brief(
            PATIO, MIGA, party=party, people=max(party, 1), angle=angle, minor=minor, held=held
        )
    else:
        b = brief(
            PATIO,
            party=party,
            people=max(party, 1),
            angle=angle,
            minor=minor,
            held=held,
            with_offer=False,
        )
    draft = StockCopywriter().compose(b)
    verdict = check_copy(draft, b)
    assert verdict.passed, (draft, verdict.violations)
