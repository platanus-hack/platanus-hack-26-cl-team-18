"""The real-brand catalog for a Pronto Copec highway shop (`DEMO_CATALOG=chile`).

Same contract as the fictional catalog (demo_catalog.py): rivals in each category so
VCG prices are real, mutual partner baskets with offers sized to the group, and a solo
offer per brand. Baskets follow real distribution lines where we know them (CCU sells
Red Bull, Pepsi and Cachantun; the Coca-Cola bottlers sell Coca-Cola, Monster and
Benedictino). No beer, spirits or Loto: Chile bans alcohol sales at gas stations
(Ley 21.363), so the minor-safety beat here blocks the "ALTO EN" products instead
(Ley 20.606 bans their advertising to children under 14).

These are real trademarks used for a local demo without affiliation; the fictional
catalog stays the default for tests, evals and the public URL. Logos are official
files in assets/campaigns/logos/<slug>.* (never AI-drawn, invariant 4); a brand
without one falls back to a plain wordmark until its press-kit file is added.

Copy rules (docs/creative-generation.md): product and price first, sizes only where
known, no invented slogans for real brands, at most one Chilean idiom per line.
Prices are 2026 shelf anchors in CLP; check them against a real Pronto before demoing.
"""

from __future__ import annotations

from app.bootstrap.demo_catalog import (
    CANDY,
    COFFEE,
    ENERGY_DRINKS,
    FAST_FOOD,
    FITNESS,
    PASTRY,
    SALTY_SNACKS,
    SNACKS,
    SOFT_DRINKS,
    WATER,
    Catalog,
    DemoBasket,
    DemoBrand,
)

BRANDS: tuple[DemoBrand, ...] = (
    # --- energy drinks: the bidding war (Score is the Chilean challenger) ----------
    DemoBrand(
        slug="score",
        name="Score",
        category="energy_drink",
        age_restricted=False,
        segments=(FITNESS, ENERGY_DRINKS),
        rate_clp_per_as="8",
        color="#f5c400",
        stock_copy="Score · Helada, para el turno largo.",
        voice=("directo", "chileno", "sin adornos"),
        product="energy_drink",
    ),
    DemoBrand(
        slug="red-bull",
        name="Red Bull",
        category="energy_drink",
        age_restricted=False,
        segments=(ENERGY_DRINKS, FITNESS),
        rate_clp_per_as="7",
        color="#db0a40",
        stock_copy="Red Bull · Lata de 250 ml, helada.",
        tagline="Red Bull te da alas.",
        voice=("seguro", "breve"),
        product="energy_drink",
    ),
    DemoBrand(
        slug="monster",
        name="Monster",
        category="energy_drink",
        age_restricted=False,
        segments=(ENERGY_DRINKS, FITNESS),
        rate_clp_per_as="6",
        color="#95d600",
        stock_copy="Monster · Lata de 473 ml, helada.",
        voice=("intenso", "breve"),
        product="energy_drink",
    ),
    # --- coffee --------------------------------------------------------------------
    DemoBrand(
        slug="juan-valdez",
        name="Juan Valdez",
        category="coffee",
        age_restricted=False,
        segments=(COFFEE, PASTRY),
        rate_clp_per_as="5",
        color="#c8102e",
        stock_copy="Juan Valdez · Café colombiano, hecho al momento.",
        voice=("cálido", "sereno"),
        product="coffee",
    ),
    DemoBrand(
        slug="nescafe",
        name="Nescafé",
        category="coffee",
        age_restricted=False,
        segments=(COFFEE,),
        rate_clp_per_as="3.5",
        color="#e2231a",
        stock_copy="Nescafé · Un café rápido antes de seguir.",
        voice=("cotidiano", "simple"),
        product="coffee",
    ),
    # --- the shop's own counter (retail media; doubles as the house brand) ----------
    DemoBrand(
        slug="pronto-copec",
        name="Pronto Copec",
        category="pastry",
        age_restricted=False,
        segments=(PASTRY, FAST_FOOD, COFFEE),
        rate_clp_per_as="4",
        color="#e1251b",
        stock_copy="Pronto Copec · Medialunas y completos en el mesón.",
        voice=("cercano", "práctico"),
        product="pastry",
    ),
    # --- soft drinks (sugar-free lines carry no ALTO EN seal) ------------------------
    DemoBrand(
        slug="coca-cola",
        name="Coca-Cola",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS, FAST_FOOD),
        rate_clp_per_as="4.5",
        color="#f40009",
        stock_copy="Coca-Cola · Sin Azúcar 591 ml, helada.",
        voice=("alegre", "simple"),
        product="soft_drink",
    ),
    DemoBrand(
        slug="pepsi",
        name="Pepsi",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS, SALTY_SNACKS),
        rate_clp_per_as="4",
        color="#2f7de1",
        stock_copy="Pepsi · Pepsi Zero, helada.",
        voice=("juvenil", "directo"),
        product="soft_drink",
    ),
    # --- water -----------------------------------------------------------------------
    DemoBrand(
        slug="cachantun",
        name="Cachantun",
        category="water",
        age_restricted=False,
        segments=(WATER, FITNESS),
        rate_clp_per_as="3",
        color="#3a8dde",
        stock_copy="Cachantun · Agua mineral, fría.",
        voice=("claro", "tranquilo"),
        product="water",
    ),
    DemoBrand(
        slug="benedictino",
        name="Benedictino",
        category="water",
        age_restricted=False,
        segments=(WATER,),
        rate_clp_per_as="2.8",
        color="#7fcbe8",
        stock_copy="Benedictino · Agua fría para el camino.",
        voice=("simple", "tranquilo"),
        product="water",
    ),
    # --- ALTO EN products: never advertised to a group with a child (Ley 20.606) ----
    DemoBrand(
        slug="lays",
        name="Lay's",
        category="snacks",
        age_restricted=True,
        segments=(SALTY_SNACKS, SNACKS),
        rate_clp_per_as="3.5",
        color="#ffd100",
        stock_copy="Lay's · Bolsa de 170 g para compartir.",
        voice=("relajado", "social"),
        product="snack",
    ),
    DemoBrand(
        slug="marco-polo",
        name="Marco Polo",
        category="snacks",
        age_restricted=True,
        segments=(SALTY_SNACKS, SNACKS),
        rate_clp_per_as="3.2",
        color="#f08a24",
        stock_copy="Marco Polo · Rústicas para el viaje.",
        voice=("familiar", "sencillo"),
        product="snack",
    ),
    DemoBrand(
        slug="super-8",
        name="Super 8",
        category="candy",
        age_restricted=True,
        segments=(CANDY, SNACKS),
        rate_clp_per_as="3",
        color="#e30613",
        stock_copy="Super 8 · Para acompañar el café.",
        voice=("cercano", "breve"),
        product="candy",
    ),
)


def _sized(one: int, two: int, three: int, four: int) -> tuple[tuple[int, str, str, int], ...]:
    """Basket tiers by party size, with moment-neutral labels (the hook carries the moment)."""
    return (
        (1, "Combo", "", one),
        (2, "Dúo", " x2", two),
        (3, "Trío", " x3", three),
        (4, "Pack x4", " x4", four),
    )


def _solo(
    labels: tuple[str, str, str, str], prices: tuple[int, int, int, int]
) -> tuple[tuple[int, str, str, int], ...]:
    suffixes = ("", " x2", " x3", " x4")
    return tuple(
        (party, label, suffix, price)
        for party, label, suffix, price in zip((1, 2, 3, 4), labels, suffixes, prices, strict=True)
    )


BASKETS: tuple[DemoBasket, ...] = (
    # The hero basket: night shift on the highway. Red Bull fights for Score's slot.
    DemoBasket(
        slugs=frozenset({"score", "juan-valdez"}),
        names="Score + Juan Valdez",
        tiers=_sized(3990, 7490, 9990, 12990),
    ),
    DemoBasket(
        slugs=frozenset({"red-bull", "juan-valdez"}),
        names="Red Bull + Juan Valdez",
        tiers=_sized(4290, 7990, 10990, 13990),
    ),
    # Breakfast before the road (summer trips to the coast).
    DemoBasket(
        slugs=frozenset({"juan-valdez", "pronto-copec"}),
        names="Juan Valdez + Pronto Copec",
        tiers=(
            (1, "Café + medialuna", "", 2990),
            (2, "Café + medialuna para 2", " x2", 5490),
            (3, "Café + medialuna para 3", " x3", 7790),
            (4, "Café + medialuna para 4", " x4", 9990),
        ),
    ),
    # Completo + bebida, the lunch counter classic.
    DemoBasket(
        slugs=frozenset({"pronto-copec", "coca-cola"}),
        names="Pronto Copec + Coca-Cola",
        tiers=(
            (1, "Completo + bebida", "", 3990),
            (2, "Dúo completo", " x2", 7490),
            (3, "Trío completo", " x3", 10490),
            (4, "Pack x4 completo", " x4", 13490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"monster", "pronto-copec"}),
        names="Monster + Pronto Copec",
        tiers=_sized(4490, 8490, 11990, 15490),
    ),
    # Group in the car: chips and a cold drink (both PepsiCo brands).
    DemoBasket(
        slugs=frozenset({"pepsi", "lays"}),
        names="Lay's + Pepsi",
        tiers=_sized(3490, 6490, 8990, 11490),
    ),
    DemoBasket(
        slugs=frozenset({"cachantun", "marco-polo"}),
        names="Cachantun + Marco Polo",
        tiers=_sized(2990, 5490, 7790, 9990),
    ),
    DemoBasket(
        slugs=frozenset({"super-8", "juan-valdez"}),
        names="Juan Valdez + Super 8",
        tiers=_sized(3290, 5990, 8490, 10990),
    ),
    # Solo offers: every brand sells alone too, sized to the group.
    DemoBasket(
        slugs=frozenset({"score"}),
        names="Score",
        tiers=_solo(
            ("Score helada", "Dos Score", "Tres Score", "Cuatro Score"), (1590, 2890, 3990, 4990)
        ),
    ),
    DemoBasket(
        slugs=frozenset({"red-bull"}),
        names="Red Bull",
        tiers=_solo(
            ("Red Bull 250 ml", "Dos Red Bull", "Tres Red Bull", "Cuatro Red Bull"),
            (1890, 3490, 4990, 6290),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"monster"}),
        names="Monster",
        tiers=_solo(
            ("Monster 473 ml", "Dos Monster", "Tres Monster", "Cuatro Monster"),
            (1990, 3690, 5290, 6690),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"juan-valdez"}),
        names="Juan Valdez",
        tiers=_solo(
            ("Café mediano", "Dos cafés", "Tres cafés", "Cuatro cafés"), (2590, 4790, 6890, 8890)
        ),
    ),
    DemoBasket(
        slugs=frozenset({"nescafe"}),
        names="Nescafé",
        tiers=_solo(
            ("Café Nescafé", "Dos Nescafé", "Tres Nescafé", "Cuatro Nescafé"),
            (1990, 3590, 4990, 6290),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"pronto-copec"}),
        names="Pronto Copec",
        tiers=_solo(
            ("Medialuna", "Dos medialunas", "Tres medialunas", "Cuatro medialunas"),
            (1190, 2190, 2990, 3790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"coca-cola"}),
        names="Coca-Cola",
        tiers=_solo(
            ("Coca-Cola Sin Azúcar 591 ml", "Dos Coca-Cola", "Tres Coca-Cola", "Cuatro Coca-Cola"),
            (1690, 2990, 4290, 5490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"pepsi"}),
        names="Pepsi",
        tiers=_solo(
            ("Pepsi Zero", "Dos Pepsi", "Tres Pepsi", "Cuatro Pepsi"), (1490, 2690, 3790, 4790)
        ),
    ),
    DemoBasket(
        slugs=frozenset({"cachantun"}),
        names="Cachantun",
        tiers=_solo(
            ("Cachantun fría", "Dos Cachantun", "Tres Cachantun", "Cuatro Cachantun"),
            (990, 1790, 2490, 3190),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"benedictino"}),
        names="Benedictino",
        tiers=_solo(
            ("Benedictino fría", "Dos Benedictino", "Tres Benedictino", "Cuatro Benedictino"),
            (890, 1590, 2290, 2890),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"lays"}),
        names="Lay's",
        tiers=_solo(
            ("Lay's 170 g", "Dos Lay's", "Tres Lay's", "Cuatro Lay's"), (2490, 4590, 6490, 8290)
        ),
    ),
    DemoBasket(
        slugs=frozenset({"marco-polo"}),
        names="Marco Polo",
        tiers=_solo(
            ("Rústicas Marco Polo", "Dos Marco Polo", "Tres Marco Polo", "Cuatro Marco Polo"),
            (2290, 4190, 5990, 7690),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"super-8"}),
        names="Super 8",
        tiers=_solo(
            ("Super 8", "Dos Super 8", "Tres Super 8", "Cuatro Super 8"), (590, 1090, 1590, 1990)
        ),
    ),
)

CHILE = Catalog(
    name="chile",
    brands=BRANDS,
    baskets=BASKETS,
    # At a highway shop a can or a bottle means a tired driver or a thirsty one, not
    # someone leaving the gym.
    cue_segments=(
        ("energy drink can", ENERGY_DRINKS),
        ("bottle", WATER),
        ("energía", ENERGY_DRINKS),
    ),
)
