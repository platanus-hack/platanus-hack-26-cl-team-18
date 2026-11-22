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
from decimal import Decimal

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
CANDY = "1074"  # Purchase Intent | CPG | Edible | General Food | Candy
NON_ALCOHOLIC = "377"  # Interest | Food & Drink | Non-Alcoholic Beverages
DESSERTS = "372"  # Interest | Food & Drink | Desserts and Baking
READY_TO_DRINK_COFFEE = "982"  # Purchase Intent | CPG | Edible | Beverages | Tea/Coffee RTD
ICE_CREAM = "1011"  # Purchase Intent | CPG | Edible | Frozen | Frozen Desserts | Ice Cream
JUICES = "988"  # Purchase Intent | CPG | Edible | Beverages | Juices
SPORTS_DRINKS = "998"  # Purchase Intent | CPG | Edible | Beverages | Sports/Energy Drinks | Sports
CHOCOLATE_CANDY = "1076"  # Purchase Intent | CPG | Edible | General Food | Candy | Chocolate
MILK = "1140"  # Purchase Intent | CPG | Edible | Refrigerated | Dairy | Milk
PROGRAMMING = "702"  # Interest | Technology & Computing | Computing | Programming Languages


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
    # Brand voice (additive; pinned rates and offers are untouched): the brand's own
    # signature line and the tone AI copy writes in.
    tagline: str | None = None
    voice: tuple[str, ...] = ()
    # The audience catalog's product category for a held prop ("product:<slug>").
    # None when iab_audience_1_1_cstore.json already lists the brand.
    product: str | None = None
    # Market shape (optional; the pinned fictional catalog leaves both unset). A brand's
    # own opening budget instead of the shared one, and the rate it bids on every segment
    # after its first (core) one: real campaigns pay most for their core audience.
    budget_clp: int | None = None
    secondary_rate_clp_per_as: str | None = None
    # False: bid only on its own segments, never by catalog affinity. A combo partner
    # needs it: affinity ("soft drinks ↔ fast food") makes both brands reach both
    # viewers, the auction then pays the basket screen share only, and the solo wins.
    broad_match: bool = True


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
        tagline="Voltio. Una vuelta más.",
        voice=("deportivo", "directo", "breve"),
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
        tagline="Café Patio. Tu café al paso.",
        voice=("cálido", "cercano", "pausado"),
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
        tagline="Malta Sur. Hecha con calma.",
        voice=("sereno", "adulto", "sobrio"),
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
        tagline="Rayo. Rápido y sin vueltas.",
        voice=("eléctrico", "urbano", "rápido"),
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
        tagline="Miga. Pan dulce de barrio.",
        voice=("casero", "tierno", "sencillo"),
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
        tagline="Brasa Burger. A la parrilla, sin atajos.",
        voice=("contundente", "directo"),
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
        tagline="Toro Burger. Grande, sin rodeos.",
        voice=("audaz", "informal"),
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
        tagline="Cola Sur. Bien helada.",
        voice=("fresco", "liviano"),
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
        tagline="Pacífica. Burbujas bien frías.",
        voice=("limpio", "sereno"),
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
        tagline="Crunchi. El que se escucha.",
        voice=("ligero", "ingenioso"),
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
        tagline="Agua Cumbre. Agua de montaña.",
        voice=("claro", "sereno"),
    ),
)

# Pre-approved partner pairs (both brands opted in) with their offers. The first two
# are pinned by tests; the rest follow docs/demo.md's partner list.
BASKETS: tuple[DemoBasket, ...] = (
    DemoBasket(
        slugs=frozenset({"voltio", "cafe-patio"}),
        names="Voltio + Café Patio",
        tiers=(
            # Moment-neutral labels: the same tiers air after the gym, on a coffee break
            # or in the queue, and the hook carries the moment.
            (1, "Combo", "", 3490),
            (2, "Dúo", " x2", 5990),
            (3, "Trío", " x3", 8490),
            (4, "Pack x4", " x4", 10990),
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
            (3, "Trío", " x3", 15490),
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
    # Solo offers: most real moments sell one brand (a coffee group, one runner), and an
    # ad without a price has nothing to redeem. Sized to the group too.
    DemoBasket(
        slugs=frozenset({"voltio"}),
        names="Voltio",
        tiers=(
            (1, "Voltio helado", "", 1490),
            (2, "Dúo Voltio", " x2", 2690),
            (3, "Trío Voltio", " x3", 3790),
            (4, "Pack x4 Voltio", " x4", 4690),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"cafe-patio"}),
        names="Café Patio",
        tiers=(
            (1, "Café del día", "", 1990),
            (2, "Dos cafés", " x2", 3590),
            (3, "Tres cafés", " x3", 4990),
            (4, "Cuatro cafés", " x4", 6290),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"rayo"}),
        names="Rayo",
        tiers=(
            (1, "Rayo helado", "", 1390),
            (2, "Dúo Rayo", " x2", 2490),
            (3, "Trío Rayo", " x3", 3490),
            (4, "Pack x4 Rayo", " x4", 4390),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"malta-sur"}),
        names="Malta Sur",
        tiers=(
            (1, "Malta Sur", "", 1890),
            (2, "Dos Malta Sur", " x2", 3490),
            (3, "Tres Malta Sur", " x3", 4890),
            (4, "Cuatro Malta Sur", " x4", 5990),
        ),
    ),
    # Every later brand gets its own solo offer too (an ad without a price sells nothing;
    # found on real footage: a fast-food ad aired priceless).
    DemoBasket(
        slugs=frozenset({"miga"}),
        names="Miga",
        tiers=(
            (1, "Medialuna", "", 990),
            (2, "Dos medialunas", " x2", 1790),
            (3, "Tres medialunas", " x3", 2490),
            (4, "Cuatro medialunas", " x4", 3190),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"brasa-burger"}),
        names="Brasa Burger",
        tiers=(
            (1, "Brasa clásica", "", 3990),
            (2, "Dúo Brasa", " x2", 7490),
            (3, "Trío Brasa", " x3", 10490),
            (4, "Pack x4 Brasa", " x4", 13490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"toro-burger"}),
        names="Toro Burger",
        tiers=(
            (1, "Toro clásica", "", 3790),
            (2, "Dúo Toro", " x2", 6990),
            (3, "Trío Toro", " x3", 9990),
            (4, "Pack x4 Toro", " x4", 12790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"cola-sur"}),
        names="Cola Sur",
        tiers=(
            (1, "Cola Sur helada", "", 1190),
            (2, "Dos Cola Sur", " x2", 2190),
            (3, "Tres Cola Sur", " x3", 2990),
            (4, "Cuatro Cola Sur", " x4", 3790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"pacifica"}),
        names="Pacífica",
        tiers=(
            (1, "Pacífica helada", "", 1090),
            (2, "Dos Pacífica", " x2", 1990),
            (3, "Tres Pacífica", " x3", 2790),
            (4, "Cuatro Pacífica", " x4", 3490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"crunchi"}),
        names="Crunchi",
        tiers=(
            (1, "Crunchi", "", 1290),
            (2, "Dos Crunchi", " x2", 2390),
            (3, "Tres Crunchi", " x3", 3290),
            (4, "Cuatro Crunchi", " x4", 4190),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"agua-cumbre"}),
        names="Agua Cumbre",
        tiers=(
            (1, "Agua Cumbre fría", "", 890),
            (2, "Dos Agua Cumbre", " x2", 1590),
            (3, "Tres Agua Cumbre", " x3", 2290),
            (4, "Cuatro Agua Cumbre", " x4", 2890),
        ),
    ),
)


def partners_of(slug: str) -> frozenset[str]:
    return FICTIONAL.partners_of(slug)


@dataclass(frozen=True, slots=True, kw_only=True)
class Catalog:
    """One market's brands and approved baskets. `DEMO_CATALOG` picks which one runs."""

    name: str
    brands: tuple[DemoBrand, ...]
    baskets: tuple[DemoBasket, ...]
    # Demo cue and keyterm labels this market reads differently: (label, IAB segment id).
    # The fictional seed reads an energy can as fitness (Voltio is sports goods); a gas
    # station reads it as an energy-drink moment, so the hook isn't "you just trained".
    cue_segments: tuple[tuple[str, str], ...] = ()
    # Context-aware bids (product.md §4.1): (daypart, brand category, multiplier). The
    # auction applies them per category (AdOpportunity.category_multipliers): coffee at
    # dawn, cold drinks in the afternoon. Empty = every category bids flat all day.
    daypart_boosts: tuple[tuple[str, str, str], ...] = ()
    # Whether brands without photos of their own may air the manifest's unbranded
    # category scenes ("category:ice_cream"). Off for the pinned fictional seed.
    category_scenes: bool = False
    # Segment → creative angle overrides (Angle values) for this venue: at a gas
    # station a water bottle or a fitness cue is a road moment, not a gym exit.
    segment_angles: tuple[tuple[str, str], ...] = ()
    # Held-marker cues: (brand slug, IAB segment id). For products the camera cannot
    # tell apart (YOLOE reads a sports drink as "bottle", a milk carton as "cup"), the
    # brand's printed marker stands in for the missing label, at full weight. Other
    # markers only mark a held product (complete the basket); they add no segment.
    marker_cues: tuple[tuple[str, str], ...] = ()

    def partners_of(self, slug: str) -> frozenset[str]:
        return frozenset(
            other for basket in self.baskets if slug in basket.slugs for other in basket.slugs
        ) - {slug}

    def boosts_for(self, daypart: str) -> tuple[tuple[str, Decimal], ...]:
        """The (category, multiplier) pairs this daypart applies, in catalog order."""
        return tuple(
            (category, Decimal(multiplier))
            for part, category, multiplier in self.daypart_boosts
            if part == daypart
        )


# The pinned fictional catalog: tests, evals and the public URL.
FICTIONAL = Catalog(name="fictional", brands=BRANDS, baskets=BASKETS)


def catalog_named(name: str) -> Catalog:
    """`fictional` (default) or `chile` (real brands, Pronto Copec; demo_catalog_chile.py)."""
    if name == FICTIONAL.name:
        return FICTIONAL
    if name == "chile":
        from app.bootstrap.demo_catalog_chile import CHILE

        return CHILE
    raise ValueError(f"Unknown demo catalog: {name!r} (expected 'fictional' or 'chile')")


def _initials(name: str) -> str:
    """'Marco Polo' → 'MP', 'Super 8' → 'S8', 'Andina del Valle' → 'AV', 'Savory' → 'S',
    'Guinness 0.0' → 'G' (a "0.0" line is a variant, not a word of the name)."""
    words = [
        w
        for w in name.replace("-", " ").split()
        if w[:1].isalnum()
        and w.lower() not in {"y", "de", "del", "la", "el"}
        and not (w.replace(".", "").isdigit() and "." in w)
    ]
    return "".join(w[0] for w in words[:2]).upper() or name[:1].upper()


def _ink(color: str) -> str:
    """Black or white, whichever reads on `color` (WCAG relative luminance)."""
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#111111" if luminance > 0.18 else "#ffffff"


def wordmark(name: str, color: str) -> str:
    """A placeholder mark until the brand's own file is dropped in: its initials on its
    colour, as a square tile like the real logos (the lockup prints the name beside it).
    Deterministic, never AI-drawn (invariant 4)."""
    text = html.escape(_initials(name))
    size = 34 if len(text) > 1 else 40
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="12" fill="{color}"/>'
        f'<text x="32" y="34" text-anchor="middle" dominant-baseline="middle" '
        f'font-family="system-ui,sans-serif" font-size="{size}" font-weight="800" '
        f'letter-spacing="-1" fill="{_ink(color)}">{text}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode("ascii")
