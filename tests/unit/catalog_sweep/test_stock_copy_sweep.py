"""Every approved basket airs its stock copy: party × angle × hook × minor × daypart.

Stock copy is the hot path's floor. If the guard rejects it, the moment was sold
and the house ad airs instead, quietly (5a09bf9: "Coca Cola" flagged as a rival in
Coca-Cola's own ad). This sweeps the real catalogs, both slot orders of every basket.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.creative.application.stock_copy import StockCopywriter, hook_options
from app.modules.creative.domain.angle import Angle, AngleChoice, AngleReason, serves
from app.modules.creative.domain.brief import MAX_NAMED_PARTY, CreativeBrief, Daypart
from app.modules.creative.domain.guard import check_copy, party_mentions
from app.modules.creative.domain.lexicon import normalize
from tests.unit.catalog_sweep.markets import basket_cases, order_for, seeded

PARTIES = range(1, MAX_NAMED_PARTY + 2)  # every named party and one beyond (and beyond 4 tiers)
DAYPART_ANGLES = frozenset({Angle.COFFEE_BREAK, Angle.HUNGER, Angle.ENERGY})
WRITER = StockCopywriter()


def _briefs(base: CreativeBrief) -> list[CreativeBrief]:
    out: list[CreativeBrief] = []
    for angle in Angle:
        if angle is Angle.SOCIAL and base.minor_likely:
            continue
        if not serves(angle, base.categories):
            continue
        if angle is Angle.COMPLETE_BASKET and len(base.brands) < 2:
            continue
        reasons = (
            (AngleReason.HELD_PRODUCT,)
            if angle is Angle.COMPLETE_BASKET
            else (AngleReason.SEGMENT, AngleReason.CATEGORY)  # strong vs weak (tagline) signal
        )
        dayparts = (None, *Daypart) if angle in DAYPART_ANGLES else (None,)
        for reason in reasons:
            for daypart in dayparts:
                out.append(
                    replace(
                        base,
                        angle=AngleChoice(angle=angle, reason=reason),
                        held_brand=base.names[0] if angle is Angle.COMPLETE_BASKET else None,
                        daypart=daypart,
                    )
                )
    return out


@pytest.mark.parametrize(("catalog", "basket"), basket_cases())
def test_every_stock_hook_passes_the_guard(catalog: str, basket: str) -> None:
    market = seeded(catalog)
    order = order_for(catalog, basket)
    restricted = any(market.bidders[b].age_restricted for b in order)
    failures: list[str] = []
    checked: set[tuple[object, ...]] = set()
    for party in PARTIES:
        for minor in (False,) if restricted else (False, True):
            for brief in _briefs(market.brief(order, party=party, minor=minor)):
                for variant in range(len(hook_options(brief))):
                    draft = WRITER.compose(brief, variant=variant)
                    # The verdict reads the lines and these brief facts only: skip repeats.
                    key = (
                        draft.teaser,
                        draft.offer,
                        draft.close,
                        draft.voice_line,
                        brief.offer,
                        brief.addressed_party,
                        brief.offer_undersized,
                        brief.minor_likely,
                    )
                    if key in checked:
                        continue
                    checked.add(key)
                    for v in check_copy(draft, brief).violations:
                        failures.append(
                            f"party={party} minor={minor} "
                            f"angle={brief.angle.angle.value}/{brief.angle.reason.value} "
                            f"daypart={brief.daypart} hook={variant}: {v.kind.value}@{v.where} "
                            f"{v.detail!r} teaser={draft.teaser!r} offer={draft.offer!r}"
                        )
    assert not failures, "\n".join(failures[:20])


@pytest.mark.parametrize(("catalog", "basket"), basket_cases())
def test_the_offer_line_names_only_the_party_its_tier_is_priced_for(
    catalog: str, basket: str
) -> None:
    """No "para los 5" over a 4-pack price: the named group is the tier's group."""
    market = seeded(catalog)
    order = order_for(catalog, basket)
    for party in PARTIES:
        brief = market.brief(order, party=party)
        assert brief.offer is not None, f"{basket} has no offer for a party of {party}"
        assert brief.offer.min_party <= party
        said = party_mentions(normalize(WRITER.compose(brief).offer))
        allowed = party_mentions(normalize(f"{brief.offer.label} {brief.offer.text}"))
        assert said <= allowed | {brief.offer.min_party}, (party, said, brief.offer)
