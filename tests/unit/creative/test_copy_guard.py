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
        (
            {"teaser": "uno dos tres cuatro cinco seis siete ocho nueve diez"},
            ViolationKind.TOO_LONG,
        ),
    ],
)
def test_each_rule_rejects(changes: dict[str, str], kind: ViolationKind) -> None:
    verdict = check_copy(replace(GOOD, **changes), GROUP)
    assert kind in verdict.kinds(), verdict.violations


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
