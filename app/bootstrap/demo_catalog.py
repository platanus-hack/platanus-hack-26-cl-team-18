"""The demo's brand catalog (docs/demo.md: one catalog, ADR 0008 D9).

Fictional brands in complementary convenience-store categories, with in-category
rivals so prices are real (auction plan D6), mutual partner pairs with an approved
basket offer per group size, and stable slugs (prop markers, video loops). Logos
are simple SVG wordmarks drawn here from the brand's name and colour: real assets,
never AI-generated (invariant 4).

The first four brands and their rates and offers are pinned by tests and evals:
add brands after them, never change them.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass

# Real IAB Audience Taxonomy 1.1 ids (app/modules/audience/infrastructure/catalog).
FITNESS = "408"  # Interest | Healthy Living | Fitness and Exercise
COFFEE = "977"  # Purchase Intent | CPG | Edible | Beverages | Coffee
BEER = "369"  # Interest | Food & Drink | Alcoholic Beverages
FAST_FOOD = "1404"  # Purchase Intent | Food and Beverage Services | Fast Food
PASTRY = "1045"  # Purchase Intent | CPG | Edible | Pastry/Doughnuts
SOFT_DRINKS = "975"  # Purchase Intent | CPG | Edible | Beverages | Carbonated Soft Drinks
SNACKS = "1115"  # Purchase Intent | CPG | Edible | Snacks
SALTY_SNACKS = "1122"  # Purchase Intent | CPG | Edible | Salty Snacks
WATER = "1000"  # Purchase Intent | CPG | Edible | Beverages | Bottled Water
ENERGY_DRINKS = "997"  # Purchase Intent | CPG | Edible | Beverages | Energy Drinks


@dataclass(frozen=True, slots=True, kw_only=True)
class DemoBrand:
    slug: str
    name: str
    category: str
    age_restricted: bool
    segments: tuple[str, ...]
    rate_clp_per_as: str
    color: str
    stock_copy: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DemoBasket:
    slugs: frozenset[str]
    names: str  # how offers name the basket; every brand must appear (copy guard)
    tiers: tuple[tuple[int, str, str, int], ...]  # (min party, label, text suffix, CLP)


BRANDS: tuple[DemoBrand, ...] = (
    # --- pinned (tests, evals): never change -------------------------------------
    DemoBrand(
        slug="voltio",
        name="Voltio",
        category="sports_goods",
        age_restricted=False,
        segments=(FITNESS,),
        rate_clp_per_as="8",
        color="#c6f26b",
        stock_copy="Voltio · Energía para tu próximo entrenamiento.",
    ),
    DemoBrand(
        slug="cafe-patio",
        name="Café Patio",
        category="coffee",
        age_restricted=False,
        segments=(COFFEE, FAST_FOOD),
        rate_clp_per_as="5",
        color="#f5bb84",
        stock_copy="Café Patio · Tu pausa después de entrenar.",
    ),
    DemoBrand(
        slug="malta-sur",
        name="Malta Sur",
        category="alcohol",
        age_restricted=True,
        segments=(BEER,),
        rate_clp_per_as="12",
        color="#f6d15a",
        stock_copy="Malta Sur · Cerveza artesanal.",
    ),
    DemoBrand(
        slug="rayo",
        name="Rayo",
        category="sports_goods",
        age_restricted=False,
        segments=(FITNESS,),
        rate_clp_per_as="6.5",
        color="#b9adff",
        stock_copy="Rayo · Energía para tu día.",
    ),
    # --- docs/demo.md catalog --------------------------------------------------------
    DemoBrand(
        slug="miga",
        name="Miga",
        category="pastry",
        age_restricted=False,
        segments=(PASTRY, COFFEE),
        rate_clp_per_as="4",
        color="#e8a3c0",
        stock_copy="Miga · Recién horneado, para tu café.",
    ),
    DemoBrand(
        slug="brasa-burger",
        name="Brasa Burger",
        category="burger",
        age_restricted=False,
        segments=(FAST_FOOD,),
        rate_clp_per_as="7",
        color="#ff7a45",
        stock_copy="Brasa Burger · A la parrilla, lista en 2 min.",
    ),
    DemoBrand(
        slug="toro-burger",
        name="Toro Burger",
        category="burger",
        age_restricted=False,
        segments=(FAST_FOOD,),
        rate_clp_per_as="6",
        color="#d94f4f",
        stock_copy="Toro Burger · Doble carne, cero espera.",
    ),
    DemoBrand(
        slug="cola-sur",
        name="Cola Sur",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS, FAST_FOOD),
        rate_clp_per_as="4.5",
        color="#8c2f39",
        stock_copy="Cola Sur · Helada, para acompañar.",
    ),
    DemoBrand(
        slug="pacifica",
        name="Pacífica",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS,),
        rate_clp_per_as="4",
        color="#3f88c5",
        stock_copy="Pacífica · Burbujas bien frías.",
    ),
    DemoBrand(
        slug="crunchi",
        name="Crunchi",
        category="snacks",
        age_restricted=False,
        segments=(SNACKS, SALTY_SNACKS),
        rate_clp_per_as="3.5",
        color="#f2c14e",
        stock_copy="Crunchi · El crujido que acompaña.",
    ),
    DemoBrand(
        slug="agua-cumbre",
        name="Agua Cumbre",
        category="water",
        age_restricted=False,
        segments=(WATER, FITNESS),
        rate_clp_per_as="3",
        color="#7fd1e8",
        stock_copy="Agua Cumbre · Hidratación de altura.",
    ),
)

# Pre-approved partner pairs (both brands opted in) with their offers. The first two
# are pinned by tests; the rest follow docs/demo.md's partner list.
BASKETS: tuple[DemoBasket, ...] = (
    DemoBasket(
        slugs=frozenset({"voltio", "cafe-patio"}),
        names="Voltio + Café Patio",
        tiers=(
            (1, "Combo post-entreno", "", 3490),
            (2, "Dúo post-entreno", " x2", 5990),
            (3, "Trío post-entreno", " x3", 8490),
            (4, "Pack x4 post-entreno", " x4", 10990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"rayo", "cafe-patio"}),
        names="Rayo + Café Patio",
        tiers=((1, "Combo", "", 3290), (2, "Dúo", " x2", 5790), (3, "Trío", " x3", 7990)),
    ),
    DemoBasket(
        slugs=frozenset({"cafe-patio", "miga"}),
        names="Café Patio + Miga",
        tiers=(
            (1, "Pausa", "", 2990),
            (2, "Pausa para 2", " x2", 5490),
            (3, "Pausa para 3", " x3", 7790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"voltio", "brasa-burger"}),
        names="Voltio + Brasa Burger",
        tiers=(
            (1, "Combo", "", 5990),
            (2, "Dúo", " x2", 10990),
            (3, "Trío post-gym", " x3", 15490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"voltio", "crunchi"}),
        names="Voltio + Crunchi",
        tiers=((1, "Combo", "", 2790), (2, "Dúo", " x2", 4990), (3, "Trío", " x3", 6990)),
    ),
    DemoBasket(
        slugs=frozenset({"cola-sur", "brasa-burger"}),
        names="Brasa Burger + Cola Sur",
        tiers=((1, "Combo", "", 5490), (2, "Dúo", " x2", 9990), (3, "Trío", " x3", 13990)),
    ),
    DemoBasket(
        slugs=frozenset({"agua-cumbre", "crunchi"}),
        names="Agua Cumbre + Crunchi",
        tiers=((1, "Combo", "", 1990), (2, "Dúo", " x2", 3590), (3, "Trío", " x3", 4990)),
    ),
)


def partners_of(slug: str) -> frozenset[str]:
    return frozenset(
        other for basket in BASKETS if slug in basket.slugs for other in basket.slugs
    ) - {slug}


def wordmark(name: str, color: str) -> str:
    """A plain SVG wordmark (the brand's name on its colour): a real asset, not AI."""
    text = html.escape(name)
    width = max(320, 34 * len(name))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="120" '
        f'viewBox="0 0 {width} 120"><rect width="100%" height="100%" rx="24" fill="{color}"/>'
        f'<text x="50%" y="58%" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="system-ui,sans-serif" font-size="56" font-weight="800" '
        f'fill="#111111">{text}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode("ascii")
