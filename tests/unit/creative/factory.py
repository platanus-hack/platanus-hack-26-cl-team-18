"""Deterministic briefs for creative tests (fixed ids, CLP prices)."""

from __future__ import annotations

from app.modules.campaigns.domain.book import BrandId
from app.modules.common.domain.value_objects.money import Money
from app.modules.creative.domain.angle import Angle, AngleChoice, AngleReason
from app.modules.creative.domain.brief import BrandBrief, CreativeBrief
from app.modules.creative.domain.offer import BasketOffer, OfferBook

VOLTIO = BrandId.create("00000000-0000-7000-8000-000000000001")
PATIO = BrandId.create("00000000-0000-7000-8000-000000000002")
RAYO = BrandId.create("00000000-0000-7000-8000-000000000003")
MIGA = BrandId.create("00000000-0000-7000-8000-000000000004")

BRANDS = {
    VOLTIO: BrandBrief(
        brand_id=VOLTIO,
        name="Voltio",
        category="energy_drink",
        stock_copy="Voltio · Energía para tu próximo entrenamiento.",
    ),
    PATIO: BrandBrief(
        brand_id=PATIO,
        name="Café Patio",
        category="coffee",
        stock_copy="Café Patio · Tu pausa después de entrenar.",
    ),
    RAYO: BrandBrief(
        brand_id=RAYO,
        name="Rayo",
        category="energy_drink",
        stock_copy="Rayo · Energía para tu día.",
    ),
    MIGA: BrandBrief(
        brand_id=MIGA, name="Miga", category="pastry", stock_copy="Miga · Recién horneado."
    ),
}
ALL_NAMES = frozenset(b.name for b in BRANDS.values())


def clp(amount: int) -> Money:
    return Money.create(currency="CLP", amount=amount)


def basket_offers(a: BrandId, b: BrandId, label: str, base: int) -> list[BasketOffer]:
    names = f"{BRANDS[a].name} + {BRANDS[b].name}"
    ids = frozenset({a, b})
    return [
        BasketOffer(
            brand_ids=ids, min_party=1, label=f"Combo {label}", text=names, price=clp(base)
        ),
        BasketOffer(
            brand_ids=ids,
            min_party=2,
            label=f"Dúo {label}",
            text=f"{names} x2",
            price=clp(base * 2 - 990),
        ),
        BasketOffer(
            brand_ids=ids,
            min_party=3,
            label=f"Trío {label}",
            text=f"{names} x3",
            price=clp(base * 3 - 1990),
        ),
        BasketOffer(
            brand_ids=ids,
            min_party=4,
            label=f"Pack x4 {label}",
            text=f"{names} x4",
            price=clp(base * 4 - 2990),
        ),
    ]


OFFERS = OfferBook.of(
    [
        *basket_offers(VOLTIO, PATIO, "post-entreno", 3490),
        *basket_offers(PATIO, MIGA, "once", 2990),
    ]
)


def brief(
    *brand_ids: BrandId,
    party: int = 1,
    people: int | None = None,
    angle: Angle = Angle.GENERIC,
    reason: AngleReason = AngleReason.SEGMENT,
    minor: bool = False,
    held: str | None = None,
    with_offer: bool = True,
) -> CreativeBrief:
    ids = brand_ids or (VOLTIO, PATIO)
    basket = frozenset(ids)
    return CreativeBrief(
        brands=tuple(BRANDS[i] for i in ids),
        layout="duo" if len(ids) == 2 else "solo",
        party=party,
        people=people if people is not None else party,
        angle=AngleChoice(angle=angle, reason=reason),
        offer=OFFERS.for_party(basket, party) if with_offer else None,
        avoid_names=frozenset(n for n in ALL_NAMES if n not in {BRANDS[i].name for i in ids}),
        minor_likely=minor,
        held_brand=held,
    )
