"""Stock copy is the floor: guard-clean, cliché-free and on-angle for every brief shape."""

from dataclasses import replace

import pytest

from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import Daypart
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from app.modules.creative.domain.lexicon import ANGLE_KEYWORDS, any_term, normalize
from app.modules.creative.domain.scoring import score_draft
from tests.unit.creative.factory import MIGA, PATIO, VOLTIO, brief

WRITER = StockCopywriter()
ANGLES = [a for a in Angle if a is not Angle.SOCIAL]  # SOCIAL needs an alcohol basket


@pytest.mark.parametrize("angle", ANGLES)
@pytest.mark.parametrize("party", [1, 2, 3, 4, 9])
@pytest.mark.parametrize("daypart", [None, *Daypart])
def test_every_template_airs_and_answers_its_angle(
    angle: Angle, party: int, daypart: Daypart | None
) -> None:
    held = "Café Patio" if angle is Angle.COMPLETE_BASKET else None
    b = replace(brief(VOLTIO, PATIO, party=party, angle=angle, held=held), daypart=daypart)
    draft = WRITER.compose(b)
    score = score_draft(draft, b)
    assert score.verdict.passed, score.verdict.violations
    assert score.parts["angle"] == 1 and score.parts["party"] == 1, (draft.teaser, draft.offer)
    template_words = draft.teaser
    for name in b.names:
        template_words = template_words.replace(name, "")
    assert len(template_words.split()) <= 6  # a hook reads in one glance from 3 m
    assert "?" not in draft.teaser  # statements, not "¿Hambre?" openers


def test_the_offer_line_restates_the_approved_offer_and_ends_on_its_price() -> None:
    b = brief(VOLTIO, PATIO, party=3, angle=Angle.POST_WORKOUT)
    draft = WRITER.compose(b)
    assert b.offer is not None
    assert draft.offer == f"{b.offer.text} para los 3 por {b.offer.price_text}."
    assert draft.teaser == "Entrenaron los tres. Ahora recarguen."


def test_the_hook_changes_when_the_group_grows() -> None:
    """Demo beat 2: the third person changes the teaser on screen, not only the price."""
    duo = WRITER.compose(brief(VOLTIO, PATIO, party=2, angle=Angle.COFFEE_BREAK))
    trio = WRITER.compose(brief(VOLTIO, PATIO, party=3, angle=Angle.COFFEE_BREAK))
    assert (duo.teaser, trio.teaser) == (
        "Pausa corta. Café para los dos.",
        "Pausa corta. Café para los tres.",
    )


def test_daypart_sharpens_the_hook() -> None:
    morning = replace(brief(PATIO, MIGA, angle=Angle.COFFEE_BREAK), daypart=Daypart.MORNING)
    assert WRITER.compose(morning).teaser == "El primer café del día."


def test_teasers_avoid_the_angle_words_of_other_needs() -> None:
    """A hunger hook shouldn't read as a coffee break: the keyword sets stay distinct."""
    hunger = WRITER.compose(brief(PATIO, MIGA, angle=Angle.HUNGER))
    assert any_term(normalize(hunger.teaser), ANGLE_KEYWORDS[Angle.HUNGER])


GOOD = CopyDraft(
    teaser="Entrenaron. Ahora recarguen.",
    offer="Voltio + Café Patio x4 para los 4 por $10.970.",
    close="Escanea y canjea en caja.",
    voice_line="Voltio + Café Patio x4 para los 4 por $10.970.",
    source=CopySource.AI,
)
GROUP = brief(VOLTIO, PATIO, party=4, angle=Angle.POST_WORKOUT)


def test_house_style_copy_passes() -> None:
    assert check_copy(GOOD, GROUP).passed


@pytest.mark.parametrize(
    "changes",
    [
        {"teaser": "El complemento perfecto para entrenar."},
        {"teaser": "Descubre la energía de Voltio."},
        {"teaser": "Una experiencia post-entreno."},
        {"close": "No te lo pierdas: escanea en caja."},
        {"teaser": "¡Entrenaron! Ahora recarguen."},
        {"teaser": "Entrenaron... ahora recarguen."},
        {"teaser": "Entrenaron — ahora recarguen."},
    ],
)
def test_marketing_filler_and_shouting_never_air(changes: dict[str, str]) -> None:
    assert ViolationKind.CLICHE in check_copy(replace(GOOD, **changes), GROUP).kinds()


@pytest.mark.parametrize(
    "offer",
    [
        "Voltio más dos Café Patio para los 4 por $10.970.",
        "Cuatro Voltio para los 4 por $10.970.",
        "Voltio + Café Patio x2 para los 4 por $10.970.",
    ],
)
def test_rewording_what_the_basket_contains_never_airs(offer: str) -> None:
    kinds = check_copy(replace(GOOD, offer=offer), GROUP).kinds()
    assert ViolationKind.OFFER_CHANGED in kinds


@pytest.mark.parametrize(
    "offer",
    [
        "Voltio y Café Patio x4 para los 4 por $10.970.",  # joined differently, same basket
        "Pack x4 post-entreno: Voltio + Café Patio, $10.970.",  # the approved label
    ],
)
def test_approved_offer_phrasings_still_air(offer: str) -> None:
    verdict = check_copy(replace(GOOD, offer=offer, voice_line=offer), GROUP)
    assert verdict.passed, verdict.violations
