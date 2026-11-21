"""Targeted stock copy: deterministic templates per angle, daypart and party (hot path, < 1 ms).

This is the floor of quality and the guarantee of targeting: it already answers
the moment ("Entrenaste. Ahora recarga.", "para los 3", "A tu Café Patio le falta
Voltio.") with the brand's approved offer, so AI copy can only make it better.

House style (docs/creative-generation.md §12): one idea per line, a statement
rather than a question, concrete over clever, no superlatives or filler. The
teaser is the hook (≤ 6 words), the offer restates the approved offer text and
its price exactly, the close says the one next step. Every template is
guard-clean and cliché-free by construction (tests/unit/creative/test_stock_copy.py).
"""

from __future__ import annotations

from app.modules.creative.application.ports.copywriter import WrittenCopy
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief, Daypart
from app.modules.creative.domain.draft import CopyDraft, CopySource

# (solo, group) hooks for when the hour or the head count can't sharpen them. The group
# form never counts: a counted group gets _GROUP_TEASERS, an undersized offer stays general.
_TEASERS: dict[Angle, tuple[str, str]] = {
    Angle.HUNGER: ("El hambre se resuelve aquí.", "Hambre en grupo, resuelta aquí."),
    Angle.ENERGY: ("Pilas bajas. Recarga aquí.", "Pilas bajas. Recarguen aquí."),
    Angle.COFFEE_BREAK: ("Pausa corta. Café largo.", "Pausa corta. Café al paso."),
    Angle.POST_WORKOUT: ("Entrenaste. Ahora recarga.", "Entrenaron. Ahora recarguen."),
    Angle.SOCIAL: ("El panorama empieza aquí.", "El panorama empieza aquí."),
    Angle.QUEUE: ("Mientras avanza la fila, pide aquí.", "Mientras avanza la fila, pidan aquí."),
    Angle.COMPLETE_BASKET: ("A tu {held} le falta {partner}.", "A tu {held} le falta {partner}."),
    Angle.GENERIC: ("{names}, aquí al lado.", "{names}, aquí al lado."),
}

# A counted group hears itself in the hook: the teaser changes the moment a third
# person walks up, not only the offer. Words, not digits (the offer line carries "3").
_GROUP_WORDS = {2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis"}
_GROUP_TEASERS: dict[Angle, str] = {
    Angle.HUNGER: "Hambre para los {group}, resuelta aquí.",
    Angle.ENERGY: "Pilas bajas. Recarguen los {group}.",
    Angle.COFFEE_BREAK: "Pausa corta. Café para los {group}.",
    Angle.POST_WORKOUT: "Entrenaron los {group}. Ahora recarguen.",
    Angle.SOCIAL: "Los {group}, el panorama empieza aquí.",
}

# The hour sharpens the hook, and a counted group still hears itself: (solo, counted group).
# Every variant of an angle keeps its keyword (café, hambre, pilas/recarga) so the viewer
# recognizes the need whatever the time of day.
_DAYPART_TEASERS: dict[tuple[Angle, Daypart], tuple[str, str]] = {
    (Angle.COFFEE_BREAK, Daypart.MORNING): (
        "El primer café del día.",
        "El primer café del día, para los {group}.",
    ),
    (Angle.COFFEE_BREAK, Daypart.MIDDAY): (
        "Café después de almorzar.",
        "Café para los {group} después de almorzar.",
    ),
    (Angle.COFFEE_BREAK, Daypart.AFTERNOON): (
        "Café de media tarde.",
        "Café de media tarde para los {group}.",
    ),
    (Angle.COFFEE_BREAK, Daypart.EVENING): (
        "Un café para cerrar el día.",
        "Los {group}, un café para cerrar el día.",
    ),
    (Angle.COFFEE_BREAK, Daypart.NIGHT): (
        "Un café antes de seguir.",
        "Un café para los {group} antes de seguir.",
    ),
    (Angle.HUNGER, Daypart.MORNING): (
        "Hambre temprano, resuelta aquí.",
        "Hambre temprano para los {group}, resuelta.",
    ),
    (Angle.HUNGER, Daypart.MIDDAY): (
        "Hambre de mediodía, resuelta aquí.",
        "Hambre de mediodía para los {group}.",
    ),
    (Angle.HUNGER, Daypart.AFTERNOON): (
        "Hambre de media tarde, resuelta.",
        "Hambre de media tarde para los {group}.",
    ),
    (Angle.HUNGER, Daypart.EVENING): (
        "Hambre después del trabajo, resuelta.",
        "Hambre para los {group} después del trabajo.",
    ),
    (Angle.HUNGER, Daypart.NIGHT): (
        "Hambre nocturna, resuelta aquí.",
        "Hambre nocturna para los {group}.",
    ),
    (Angle.ENERGY, Daypart.MORNING): (
        "Mañana larga. A recargar.",
        "Mañana larga. A recargar, los {group}.",
    ),
    (Angle.ENERGY, Daypart.AFTERNOON): (
        "Media tarde, pilas bajas. A recargar.",
        "Media tarde, pilas bajas para los {group}.",
    ),
    (Angle.ENERGY, Daypart.EVENING): (
        "Queda camino. A recargar.",
        "Queda camino para los {group}. A recargar.",
    ),
    (Angle.ENERGY, Daypart.NIGHT): (
        "Queda camino. A recargar.",
        "Queda camino para los {group}. A recargar.",
    ),
}

_CLOSES: dict[Angle, str] = {
    Angle.QUEUE: "Escanea ahora y canjea al pagar.",
    Angle.COMPLETE_BASKET: "Escanea y súmalo a tu compra.",
}
_DEFAULT_CLOSE = "Escanea y canjea en caja."


def _join(names: tuple[str, ...]) -> str:
    return " + ".join(names)


def teaser_for(brief: CreativeBrief, angle: Angle) -> str:
    """Hour first (with the head count when it can be said), then the counted group, then
    the angle's general hook."""
    held = brief.held_brand or brief.names[0]
    partners = tuple(name for name in brief.names if name != held) or brief.names
    count = _GROUP_WORDS.get(brief.addressed_party or 0)
    by_hour = _DAYPART_TEASERS.get((angle, brief.daypart)) if brief.daypart else None
    if by_hour is not None:
        solo_hook, counted_hook = by_hour
        template = counted_hook if count is not None else solo_hook
    elif count is not None and angle in _GROUP_TEASERS:
        template = _GROUP_TEASERS[angle]
    else:
        solo_hook, group_hook = _TEASERS[angle]
        template = group_hook if brief.is_group else solo_hook
    return template.format(
        names=_join(brief.names), held=held, partner=_join(partners), group=count or ""
    )


def offer_for(brief: CreativeBrief) -> str:
    """The approved offer text and price, addressed to the group. Ends with the price, so
    the player can set the price apart without rewording (web: splitOfferHeadline)."""
    n = brief.addressed_party
    if brief.offer_undersized:
        audience = " para compartir"  # a 4-pack for 12 is offered to share, never to everyone
    elif n is not None:
        audience = f" para los {n}"
    else:
        audience = " para todo el grupo"
    if brief.offer is None:
        body = " · ".join(b.stock_copy for b in brief.brands)
        return f"Para los {n} · {body}" if n is not None and brief.is_group else body
    who = audience if brief.is_group else ""
    return f"{brief.offer.text}{who} por {brief.offer.price_text}."


class StockCopywriter:
    name = "stock"

    def compose(self, brief: CreativeBrief) -> CopyDraft:
        angle = brief.angle.angle
        if angle is Angle.COMPLETE_BASKET and brief.held_brand is None:
            angle = Angle.GENERIC
        teaser = teaser_for(brief, angle)
        offer = offer_for(brief)
        close = _CLOSES.get(angle, _DEFAULT_CLOSE)
        spoken = f"{teaser} {offer}"
        voice = spoken if len(spoken.split()) <= brief.limits.voice_words else offer
        return CopyDraft(
            teaser=teaser,
            offer=offer,
            close=close,
            voice_line=voice,
            source=CopySource.STOCK,
            model_version="stock-templates@2",
            reasoning=(
                f"angle={angle.value} via {brief.angle.reason.value}; party={brief.party}; "
                f"daypart={brief.daypart.value if brief.daypart else '-'}"
            ),
        )

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        return WrittenCopy(draft=self.compose(brief))
