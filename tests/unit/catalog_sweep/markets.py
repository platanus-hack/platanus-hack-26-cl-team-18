"""Both demo catalogs seeded once, the way the live loop seeds them (shared by the sweeps).

The sweeps in this package are table-driven over the real catalog data
(app/bootstrap/demo_catalog.py, demo_catalog_chile.py), so a catalog edit that
silently degrades a paid moment (stock copy the guard rejects, a basket without a
tier, a prop no basket can fill) turns a test red instead of airing the house ad.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from app.bootstrap.demo import LiveMarket, seed_market
from app.bootstrap.demo_catalog import Catalog, catalog_named
from app.modules.campaigns.domain.book import Bidder, BrandId
from app.modules.common.application.id_generator import IdGenerator
from app.modules.creative.application.briefing import CreativeCatalog, assemble_brief
from app.modules.creative.domain.brief import CreativeBrief

CATALOGS = ("fictional", "chile")


@dataclass(frozen=True, slots=True)
class SeededMarket:
    catalog: Catalog
    market: LiveMarket
    creative: CreativeCatalog
    bidders: dict[BrandId, Bidder]
    slug_of: dict[BrandId, str]

    def basket_orders(self) -> list[tuple[BrandId, ...]]:
        """Every approved basket (solo offers included) in every slot order."""
        orders: list[tuple[BrandId, ...]] = []
        for basket in sorted(self.market.offers.baskets(), key=self.label):
            ids = tuple(sorted(basket, key=lambda b: self.slug_of[b]))
            orders.append(ids)
            if len(ids) == 2:
                orders.append((ids[1], ids[0]))
        return orders

    def label(self, brands: frozenset[BrandId] | tuple[BrandId, ...]) -> str:
        ordered = brands if isinstance(brands, tuple) else sorted(brands, key=self.slug_of.get)
        return "+".join(self.slug_of[b] for b in ordered)

    def brief(
        self,
        order: tuple[BrandId, ...],
        *,
        party: int,
        minor: bool = False,
    ) -> CreativeBrief:
        return assemble_brief(
            basket=tuple((b, self.bidders[b].category) for b in order),
            layout="duo" if len(order) == 2 else "solo",
            segments=(),
            looking=party,
            people=party,
            minor_likely=minor,
            catalog=self.creative,
        )


@cache
def seeded(name: str) -> SeededMarket:
    catalog = catalog_named(name)
    market = seed_market(ids=IdGenerator(), budget_clp=50_000, catalog=catalog)
    slug_of = {
        kit.brand_id: brand.slug for brand, kit in zip(catalog.brands, market.kits, strict=True)
    }
    return SeededMarket(
        catalog=catalog,
        market=market,
        creative=CreativeCatalog(kits={k.brand_id: k for k in market.kits}, offers=market.offers),
        bidders={b.brand_id: b for b in market.bidders},
        slug_of=slug_of,
    )


def basket_cases() -> list[tuple[str, str]]:
    """(catalog, basket label in slot order) for every approved basket of both catalogs."""
    return [
        (name, seeded(name).label(order))
        for name in CATALOGS
        for order in seeded(name).basket_orders()
    ]


def order_for(name: str, label: str) -> tuple[BrandId, ...]:
    market = seeded(name)
    by_slug = {slug: brand for brand, slug in market.slug_of.items()}
    return tuple(by_slug[slug] for slug in label.split("+"))
