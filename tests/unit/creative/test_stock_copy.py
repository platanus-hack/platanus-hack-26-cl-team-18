"""Stock copy is the floor: guard-clean, cliché-free and on-angle for every brief shape."""

from dataclasses import replace

import pytest

from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.angle import Angle, AngleReason
from app.modules.creative.domain.brief import CreativeBrief, Daypart, daypart_for_hour
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from app.modules.creative.domain.lexicon import ANGLE_KEYWORDS, any_term, normalize
from app.modules.creative.domain.scoring import score_draft
from tests.unit.creative.factory import MIGA, PATIO, RAYO, VOLTIO, brief

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
    assert len(template_words.split()) <= b.limits.teaser_words  # one glance from 3 m
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


@pytest.mark.parametrize(
    ("hour", "daypart"),
    [
        (0, Daypart.NIGHT),
        (6, Daypart.MORNING),
        (11, Daypart.MIDDAY),
        (15, Daypart.AFTERNOON),
        (19, Daypart.EVENING),
        (22, Daypart.NIGHT),
    ],
)
def test_the_venue_hour_maps_to_a_daypart(hour: int, daypart: Daypart) -> None:
    assert daypart_for_hour(hour) is daypart


def test_the_hour_and_the_head_count_combine_instead_of_overriding_each_other() -> None:
    trio = replace(brief(PATIO, MIGA, party=3, angle=Angle.COFFEE_BREAK), daypart=Daypart.AFTERNOON)
    assert WRITER.compose(trio).teaser == "Café de media tarde para los tres."
    solo = replace(brief(PATIO, MIGA, angle=Angle.COFFEE_BREAK), daypart=Daypart.AFTERNOON)
    assert WRITER.compose(solo).teaser == "Café de media tarde."


def test_an_offer_smaller_than_the_group_is_offered_to_share() -> None:
    crowd = brief(VOLTIO, PATIO, party=12, angle=Angle.QUEUE)
    assert crowd.offer_undersized
    draft = WRITER.compose(crowd)
    assert "para compartir" in draft.offer and "todo el grupo" not in draft.all_text()
    assert check_copy(draft, crowd).passed


def _with_tagline(b: CreativeBrief, tagline: str) -> CreativeBrief:
    lead, *rest = b.brands
    return replace(b, brands=(replace(lead, tagline=tagline, voice=("directo",)), *rest))


def test_with_nothing_specific_known_the_lead_brand_speaks_in_its_own_line() -> None:
    weak = brief(VOLTIO, PATIO, angle=Angle.ENERGY, reason=AngleReason.CATEGORY)
    voltio = _with_tagline(weak, "Voltio. Una vuelta más.")
    rayo = _with_tagline(
        brief(RAYO, PATIO, angle=Angle.ENERGY, reason=AngleReason.CATEGORY),
        "Rayo. Rápido y sin vueltas.",
    )
    assert WRITER.compose(voltio).teaser == "Voltio. Una vuelta más."
    assert (
        WRITER.compose(rayo).teaser == "Rayo. Rápido y sin vueltas."
    )  # rivals no longer sound alike
    assert check_copy(WRITER.compose(voltio), voltio).passed


@pytest.mark.parametrize(
    ("reason", "party"),
    [(AngleReason.SEGMENT, 1), (AngleReason.SPEECH, 1), (AngleReason.CATEGORY, 3)],
)
def test_a_real_signal_or_a_group_keeps_the_moment_hook(reason: AngleReason, party: int) -> None:
    b = _with_tagline(
        brief(VOLTIO, PATIO, party=party, angle=Angle.ENERGY, reason=reason),
        "Voltio. Una vuelta más.",
    )
    assert WRITER.compose(b).teaser != "Voltio. Una vuelta más."
