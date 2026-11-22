"""What a brand approved may air as written, and its own name in any spelling is still its own.

The AI copywriter is handed each brand's approved lines and offer label
(claude_copywriter.py: approved_lines, offer.label) and echoes them. A guard that
rejects them throws away every warm upgrade for that brand, quietly: stock stays on.
Found here: approved pack sizes ("Original 473 ml", "Bolsa de 170 g") read as changed
prices, and "Coca Cola" / "Lay’s" (typographic apostrophe) read as a missing brand.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace

import pytest

from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.guard import ViolationKind, check_copy
from tests.unit.catalog_sweep.markets import CATALOGS, seeded

WRITER = StockCopywriter()


def _basket_cases() -> list[tuple[str, str]]:
    return [
        (name, seeded(name).label(order))
        for name in CATALOGS
        for order in seeded(name).basket_orders()
        if len(order) == 1 or order == tuple(sorted(order, key=seeded(name).slug_of.get))
    ]


def _order(name: str, label: str) -> tuple:
    by_slug = {slug: brand for brand, slug in seeded(name).slug_of.items()}
    return tuple(by_slug[slug] for slug in label.split("+"))


@pytest.mark.parametrize(("catalog", "basket"), _basket_cases())
def test_every_approved_line_and_offer_headline_airs_verbatim(catalog: str, basket: str) -> None:
    market = seeded(catalog)
    order = _order(catalog, basket)
    failures = []
    for tier in market.market.offers.tiers(frozenset(order)):
        brief = market.brief(order, party=tier.min_party)
        assert brief.offer == tier
        lines = [b.stock_copy for b in brief.brands] + [
            b.tagline for b in brief.brands if b.tagline
        ]
        for line in lines:
            draft = CopyDraft(
                teaser=line,
                offer=tier.headline,  # "Label: text por $price", the approved offer line
                close="Escanea y canjea en caja.",
                voice_line=line,
                source=CopySource.AI,
            )
            failures += [
                f"{tier.label!r} {v.kind.value}@{v.where} {v.detail!r} in {line!r}"
                for v in check_copy(draft, brief).violations
                if v.kind is not ViolationKind.TOO_LONG  # stock_copy is a slot line, not a hook
            ]
    assert not failures, "\n".join(failures)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _spellings(name: str) -> set[str]:
    """How a copywriter (or a model) may write the same brand name."""
    return {
        name.upper(),
        _strip_accents(name),
        name.replace("-", " "),
        name.replace("-", ""),
        name.replace(" ", "-"),
        name.replace("'", ""),
        name.replace("'", "’"),  # typographic apostrophe: Lay’s
        name.replace(" y ", " & "),
    } - {name}


@pytest.mark.parametrize(("catalog", "basket"), _basket_cases())
def test_a_brand_spelled_another_way_is_neither_a_rival_nor_missing(
    catalog: str, basket: str
) -> None:
    market = seeded(catalog)
    brief = market.brief(_order(catalog, basket), party=2)
    stock = WRITER.compose(brief)
    failures = []
    for name in brief.names:
        for spelling in sorted(_spellings(name)):
            teaser = f"{spelling}, bien helado."
            for draft in (
                replace(stock, teaser=teaser, voice_line=teaser),
                replace(stock, offer=stock.offer.replace(name, spelling)),
            ):
                kinds = check_copy(draft, brief).kinds()
                if kinds:
                    failures.append(f"{spelling!r}: {sorted(kinds)} in {draft.offer!r}")
    assert not failures, "\n".join(failures)


def test_an_unapproved_size_or_a_rival_spelled_loosely_is_still_caught() -> None:
    market = seeded("chile")
    by_slug = {slug: brand for brand, slug in market.slug_of.items()}
    brief = market.brief((by_slug["coca-cola"],), party=1)
    stock = WRITER.compose(brief)
    bigger = replace(stock, teaser="Coca-Cola de 1500 ml, helada.")
    assert ViolationKind.PRICE_CHANGED in check_copy(bigger, brief).kinds()
    rival = replace(stock, teaser="Mejor que Bilz & Pap.")
    assert ViolationKind.COMPETITOR_NAME in check_copy(rival, brief).kinds()
    other_basket = replace(stock, offer="Pepsi Zero por $1.690.")
    kinds = check_copy(other_basket, brief).kinds()
    assert {ViolationKind.COMPETITOR_NAME, ViolationKind.OFFER_CHANGED} <= kinds
