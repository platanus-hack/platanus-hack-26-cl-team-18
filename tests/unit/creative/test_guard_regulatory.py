"""Copy-guard rules grounded in Chilean advertising law and self-regulation.

Sources (see evals/creative/adversarial.json `source` fields for the eval set):
- Ley 20.606 + DS 13/2015 (Reglamento Sanitario de los Alimentos): child-appeal
  language, toys or characters make an "alto en" ad count as aimed at under-14s.
- Ley 21.363 (amends Ley 19.925): alcohol ads must not encourage harmful drinking.
- Ley 19.496 arts. 28 d) and 30 (SERNAC): the advertised price is the real, total price.
- Código Chileno de Ética Publicitaria (CONAR, 7th ed. 2024): truthful, verifiable
  claims; never exploit children's credulity (pester power).

Both directions are tested: what must be rejected, and the near-miss wording that
must still air (a guard that over-blocks turns every moment into the house ad).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from tests.unit.creative.factory import brief

ADULTS = brief(party=4, angle=Angle.HUNGER)
WITH_MINOR = brief(party=4, angle=Angle.HUNGER, minor=True)
GOOD = CopyDraft(
    teaser="¿Con hambre los 4? Voltio + Café Patio",
    offer="Voltio + Café Patio x4 para los 4 por $10.970",
    close="Escanea el QR · válido 10 min",
    voice_line="Para los 4, Voltio y Café Patio por 10.970 pesos",
    source=CopySource.AI,
)


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        # Ley 20.606 / DS 13: children's language and toys, with a minor present.
        (
            {"teaser": "¿Con hambre, peques? Voltio + Café Patio"},
            ViolationKind.RESTRICTED_FOR_MINOR,
        ),
        ({"close": "Con juguete sorpresa · escanea el QR"}, ViolationKind.RESTRICTED_FOR_MINOR),
        (
            {"teaser": "¿Salida del colegio? Voltio + Café Patio"},
            ViolationKind.RESTRICTED_FOR_MINOR,
        ),
        # CONAR: pester power.
        ({"close": "Pídele a tu mamá el QR"}, ViolationKind.RESTRICTED_FOR_MINOR),
    ],
)
def test_child_appeal_is_blocked_when_a_minor_is_likely(
    changes: dict[str, str], kind: ViolationKind
) -> None:
    draft = replace(GOOD, **changes)
    assert kind in check_copy(draft, WITH_MINOR).kinds()


def test_child_appeal_words_are_allowed_for_adults_only_audiences() -> None:
    draft = replace(GOOD, close="Con sorpresa en caja · escanea el QR")
    assert ViolationKind.RESTRICTED_FOR_MINOR not in check_copy(draft, ADULTS).kinds()


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        # SERNAC arts. 28 d) / 30: total price, taxes included.
        (
            {"offer": "Para los 4 · Pack x4: Voltio + Café Patio desde $10.970"},
            ViolationKind.PRICE_CHANGED,
        ),
        (
            {"offer": "Para los 4 · Pack x4: Voltio + Café Patio a partir de $10.970"},
            ViolationKind.PRICE_CHANGED,
        ),
        (
            {"offer": "Para los 4 · Pack x4: Voltio + Café Patio por $10.970 + IVA"},
            ViolationKind.PRICE_CHANGED,
        ),
        (
            {"offer": "Para los 4 · Pack x4: Voltio + Café Patio por $10.970, IVA no incluido"},
            ViolationKind.PRICE_CHANGED,
        ),
        # CONAR: unverifiable superlatives, endorsements, performance promises.
        ({"teaser": "El bajón más rico de Chile"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"teaser": "Recomendado por nutricionistas"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"teaser": "Voltio te hace más fuerte"}, ViolationKind.UNAPPROVED_CLAIM),
        # Ley 21.363: harmful drinking and drink-driving.
        ({"close": "Tómense todas, sin límite"}, ViolationKind.UNAPPROVED_CLAIM),
        ({"close": "Una más para el camino"}, ViolationKind.UNAPPROVED_CLAIM),
    ],
)
def test_misleading_prices_and_claims_are_rejected(
    changes: dict[str, str], kind: ViolationKind
) -> None:
    assert kind in check_copy(replace(GOOD, **changes), ADULTS).kinds()


@pytest.mark.parametrize(
    "changes",
    [
        {"close": "Desde hoy en caja · escanea el QR"},  # "desde" not before a price
        {"teaser": "¿Hambre? Voltio + Café Patio y papas"},  # potato, not dad
        {"teaser": "Qué rico. Voltio + Café Patio"},  # plain praise
        {"close": "Escanea el QR · válido 10 min, sin filas"},
    ],
)
def test_benign_near_misses_still_air_with_a_minor_present(changes: dict[str, str]) -> None:
    verdict = check_copy(replace(GOOD, **changes), WITH_MINOR)
    assert verdict.passed, verdict.violations
