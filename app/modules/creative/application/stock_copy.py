"""Targeted stock copy: deterministic templates per angle and party size (hot path, < 1 ms).

This is the floor of quality and the guarantee of targeting: it already answers
the moment ("¿Hambre?", "Para los 4", "Tu Café Patio pide…") with the brand's
approved offer, so AI copy can only make it better. Every template is
guard-clean by construction (tests/unit/creative/test_stock_copy.py).
"""

from __future__ import annotations

from app.modules.creative.application.ports.copywriter import WrittenCopy
from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft, CopySource

_TEASERS: dict[Angle, str] = {
    Angle.HUNGER: "¿Hambre? {names}",
    Angle.ENERGY: "¿Te falta energía? {names}",
    Angle.COFFEE_BREAK: "¿Una pausa? {names}",
    Angle.POST_WORKOUT: "¿Post-entreno? Recarga con {names}",
    Angle.SOCIAL: "¿Panorama? {names}",
    Angle.QUEUE: "¿En la fila? Pide {names}",
    Angle.COMPLETE_BASKET: "Tu {held} pide {partner}",
    Angle.GENERIC: "{names}, aquí al lado",
}

_GROUP_TEASERS: dict[Angle, str] = {
    Angle.HUNGER: "¿Con hambre los {n}? {names}",
    Angle.ENERGY: "¿Energía para los {n}? {names}",
    Angle.COFFEE_BREAK: "¿Una pausa los {n}? {names}",
    Angle.POST_WORKOUT: "¿Post-entreno los {n}? {names}",
    Angle.SOCIAL: "¿Panorama para los {n}? {names}",
}

_CLOSES: dict[Angle, str] = {
    Angle.QUEUE: "Escanea el QR: listo al pagar",
    Angle.COMPLETE_BASKET: "Escanea el QR · válido 10 min",
}
_DEFAULT_CLOSE = "Escanea el QR · válido 10 min"


def _join(names: tuple[str, ...]) -> str:
    return " + ".join(names)


class StockCopywriter:
    name = "stock"

    def compose(self, brief: CreativeBrief) -> CopyDraft:
        names = _join(brief.names)
        angle = brief.angle.angle
        n = brief.addressed_party
        held = brief.held_brand or brief.names[0]
        partners = tuple(name for name in brief.names if name != held) or brief.names
        if angle is Angle.COMPLETE_BASKET and brief.held_brand is None:
            angle = Angle.GENERIC
        template = (
            _GROUP_TEASERS.get(angle, _TEASERS[angle])
            if brief.is_group and n is not None
            else _TEASERS[angle]
        )
        teaser = template.format(names=names, n=n, held=held, partner=_join(partners))

        body = (
            brief.offer.headline
            if brief.offer is not None
            else " · ".join(b.stock_copy for b in brief.brands)
        )
        group = f"Para los {n}" if n is not None else "Para todo el grupo"
        offer = f"{group} · {body}" if brief.is_group else body

        close = _CLOSES.get(angle, _DEFAULT_CLOSE)
        return CopyDraft(
            teaser=teaser,
            offer=offer,
            close=close,
            voice_line=offer,
            source=CopySource.STOCK,
            model_version="stock-templates@1",
            reasoning=f"angle={angle.value} via {brief.angle.reason.value}; party={brief.party}",
        )

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        return WrittenCopy(draft=self.compose(brief))
