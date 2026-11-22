"""The real-brand catalog for a Pronto Copec highway shop (`DEMO_CATALOG=chile`).

Same contract as the fictional catalog (demo_catalog.py): rivals in each category so
VCG prices are real, mutual partner baskets with offers sized to the group, and a solo
offer per brand. Baskets follow real distribution lines where we know them (CCU sells
Red Bull, Pepsi and Cachantun; the Coca-Cola bottlers sell Coca-Cola, Monster and
Benedictino). No beer, spirits or Loto: Chile bans alcohol sales at gas stations
(Ley 21.363). The one beer brand is Guinness 0.0, a non-alcoholic beer the shop can
sell; Ley 21.363 still treats an alcohol brand's 0.0 line as alcohol advertising, so it
is age-restricted like the "ALTO EN" products (Ley 20.606 bans their advertising to
children under 14): snacks, candy, flavoured milk, nectars and ice cream. Sugared energy
drinks (Score, Red Bull, Monster) carry the "ALTO EN AZÚCARES" seal too (Score's own ad
shows it), so they are restricted the same way: with a child in the group the hero
basket gives way to the coffee. Sports drinks stay open (check their seals before the
demo).

These are real trademarks used for a local demo without affiliation; the fictional
catalog stays the default for tests, evals and the public URL. Logos are official
files in assets/campaigns/logos/<slug>.* (never AI-drawn, invariant 4); a brand
without one falls back to a plain wordmark until its press-kit file is added.

Copy rules (docs/creative-generation.md): product and price first, sizes only where
known, no invented slogans for real brands, at most one Chilean idiom per line.
Prices are 2026 shelf anchors in CLP; check them against a real Pronto before demoing.
"""

from __future__ import annotations

from dataclasses import replace

from app.bootstrap.demo_catalog import (
    BEER,
    CANDY,
    CHOCOLATE_CANDY,
    COFFEE,
    DESSERTS,
    ENERGY_DRINKS,
    FAST_FOOD,
    FITNESS,
    ICE_CREAM,
    JUICES,
    MILK,
    NON_ALCOHOLIC,
    PASTRY,
    PROGRAMMING,
    READY_TO_DRINK_COFFEE,
    SALTY_SNACKS,
    SNACKS,
    SOFT_DRINKS,
    SPORTS_DRINKS,
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
        age_restricted=True,  # "ALTO EN AZÚCARES" (its own ad)
        segments=(FITNESS, ENERGY_DRINKS),
        rate_clp_per_as="8",
        color="#f5c400",
        stock_copy="Score · Guaraná 500 ml, helada para el turno largo.",  # as on the can
        tagline="Score. Máxima potencia.",  # its own site: "Bebida Energética con Máxima Potencia"
        voice=("directo", "chileno", "sin adornos"),
        product="energy_drink",
        # Exact match only: bidding 8 on fitness, affinity (fitness <-> water, <-> sports
        # drinks) would put "Pilas bajas" on every thirsty shopper and water bottle.
        broad_match=False,
    ),
    DemoBrand(
        slug="red-bull",
        name="Red Bull",
        category="energy_drink",
        age_restricted=True,  # sugared energy drink: "ALTO EN AZÚCARES"
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
        age_restricted=True,  # sugared energy drink: "ALTO EN AZÚCARES"
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
        segments=(COFFEE,),
        rate_clp_per_as="5",
        color="#c8102e",
        stock_copy="Juan Valdez · Café colombiano, hecho al momento.",
        tagline="Juan Valdez. Compartimos lo mejor que hacemos.",  # its 2025 global platform
        voice=("cálido", "sereno"),
        product="coffee",
    ),
    DemoBrand(
        slug="nescafe",
        name="Nescafé",
        category="coffee",
        age_restricted=False,
        segments=(COFFEE, READY_TO_DRINK_COFFEE),
        rate_clp_per_as="3.5",
        color="#e2231a",
        stock_copy="Nescafé · Un café rápido antes de seguir.",
        tagline="Todo empieza con un Nescafé.",  # its worldwide line since 2014
        voice=("cotidiano", "simple"),
        product="coffee",
    ),
    # --- the shop's own counter (retail media; doubles as the house brand) ----------
    DemoBrand(
        slug="pronto-copec",
        name="Pronto Copec",
        category="pastry",
        age_restricted=False,
        # The completo is the counter's core product, medialunas next. Partners bid only
        # their own product's audience: a basket wins full value only when its brands
        # reach different viewers (auction clearing: same audience → the solo wins).
        segments=(FAST_FOOD, PASTRY),
        rate_clp_per_as="5",
        color="#e1251b",
        stock_copy="Pronto Copec · Completos y medialunas en el mesón.",
        voice=("cercano", "práctico"),
        broad_match=False,  # the completo + bebida combo (DemoBrand.broad_match)
        product="pastry",
    ),
    # --- soft drinks (sugar-free lines carry no ALTO EN seal) ------------------------
    DemoBrand(
        slug="coca-cola",
        name="Coca-Cola",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS,),
        rate_clp_per_as="5",
        color="#f40009",
        stock_copy="Coca-Cola · Zero Azúcar 591 ml, helada.",
        voice=("alegre", "simple"),
        broad_match=False,  # the completo + bebida combo (DemoBrand.broad_match)
        product="soft_drink",
    ),
    DemoBrand(
        slug="pepsi",
        name="Pepsi",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS,),
        rate_clp_per_as="4",
        color="#2f7de1",
        stock_copy="Pepsi · Pepsi Zero, helada.",
        voice=("juvenil", "directo"),
        broad_match=False,  # the completo + bebida combo (DemoBrand.broad_match)
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
        tagline="Benedictino. Vive a fondo.",  # its own line, on its official mark
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
        segments=(CHOCOLATE_CANDY, CANDY),
        rate_clp_per_as="3",
        color="#e30613",
        stock_copy="Super 8 · Para acompañar el café.",
        voice=("cercano", "breve"),
        product="candy",
    ),
    DemoBrand(
        slug="ambrosoli",
        name="Ambrosoli",
        category="candy",
        age_restricted=True,
        segments=(CANDY,),
        rate_clp_per_as="3.1",  # clears the duo reserve alone: the checkout impulse buy
        color="#d4213d",
        stock_copy="Ambrosoli · Dulces para el viaje.",
        tagline="Ambrosoli. La vida es dulce.",  # its own line: "...la vida es dulce"
        voice=("familiar", "tranquilo"),
        product="candy",
    ),
    # --- Chile's own soda, rival to Coca-Cola for the completo (CCU) ----------------
    DemoBrand(
        slug="bilz-y-pap",
        name="Bilz y Pap",
        category="cola",
        age_restricted=False,
        segments=(SOFT_DRINKS,),
        rate_clp_per_as="4.2",
        color="#f4c300",
        stock_copy="Bilz y Pap · Zero 600 ml, bien heladas para el completo.",
        tagline="Bilz y Pap, el sabor de la diversión.",  # CCU's 2026 campaign
        voice=("nostálgico", "alegre"),
        broad_match=False,  # the completo + bebida combo (DemoBrand.broad_match)
        product="soft_drink",
    ),
    # --- sports drinks (Coca-Cola system vs PepsiCo) ---------------------------------
    DemoBrand(
        slug="powerade",
        name="Powerade",
        category="sports_drink",
        age_restricted=False,
        segments=(SPORTS_DRINKS, FITNESS, WATER),
        rate_clp_per_as="3.8",
        color="#1d6fd6",
        stock_copy="Powerade · Isotónica, helada.",
        voice=("deportivo", "directo"),
        product="sports_drink",
    ),
    DemoBrand(
        slug="gatorade",
        name="Gatorade",
        category="sports_drink",
        age_restricted=False,
        segments=(SPORTS_DRINKS, FITNESS, WATER),
        rate_clp_per_as="4",
        color="#f26522",
        stock_copy="Gatorade · Isotónica, helada.",
        tagline="Gatorade. ¿Está en ti?",  # its 2026 global football platform
        voice=("deportivo", "seguro"),
        product="sports_drink",
    ),
    # --- flavoured milk and nectars usually carry ALTO EN AZÚCARES (Ley 20.606) ------
    DemoBrand(
        slug="colun",
        name="Colun",
        category="dairy",
        age_restricted=True,
        segments=(MILK,),
        rate_clp_per_as="3.2",
        color="#2a6ebb",
        stock_copy="Colun · Leche chocolatada de 200 ml, hecha en el sur.",
        tagline="Colun, toda la magia del sur.",
        voice=("campestre", "cercano"),
        product="dairy",
    ),
    DemoBrand(
        slug="soprole",
        name="Soprole",
        category="dairy",
        age_restricted=True,
        segments=(MILK,),
        rate_clp_per_as="3",
        color="#e30613",
        stock_copy="Soprole · Leche chocolatada, fría.",
        voice=("familiar", "simple"),
        product="dairy",
    ),
    DemoBrand(
        slug="watts",
        name="Watt's",
        category="juice",
        age_restricted=True,
        segments=(JUICES,),
        rate_clp_per_as="3",
        color="#e2001a",
        stock_copy="Watt's · Néctar frío para el desayuno.",
        tagline="Es Watt's, es fruta.",  # its current line, ccu.cl
        voice=("familiar", "fresco"),
        product="juice",
    ),
    DemoBrand(
        slug="andina-del-valle",
        name="Andina del Valle",
        category="juice",
        age_restricted=True,
        segments=(JUICES,),
        rate_clp_per_as="2.9",
        color="#f7a800",
        stock_copy="Andina del Valle · Néctar frío, para el camino.",
        voice=("fresco", "simple"),
        product="juice",
    ),
    # --- ice cream: summer on the highway (ALTO EN, so never with a child) ----------
    DemoBrand(
        slug="savory",
        name="Savory",
        category="ice_cream",
        age_restricted=True,
        segments=(ICE_CREAM, DESSERTS),
        rate_clp_per_as="3.4",
        color="#e4002b",
        stock_copy="Savory · Helado para el calor de la ruta.",
        tagline="Alarma de helado.",
        voice=("veraniego", "alegre"),
        product="ice_cream",
    ),
    DemoBrand(
        slug="bresler",
        name="Bresler",
        category="ice_cream",
        age_restricted=True,
        segments=(ICE_CREAM, DESSERTS),
        rate_clp_per_as="3.3",
        color="#ff3b30",
        stock_copy="Bresler · Helado para el viaje.",
        tagline="Bresler. Vuelve a sorprenderte.",  # its 2020 relaunch line
        voice=("veraniego", "cercano"),
        product="ice_cream",
    ),
    # --- added from the team's campaign references (assets/campaigns/photos/SOURCES.md)
    # Starbucks fights Juan Valdez for the coffee slot: the cold, afternoon cup.
    DemoBrand(
        slug="starbucks",
        name="Starbucks",
        category="coffee",
        age_restricted=False,
        segments=(COFFEE, READY_TO_DRINK_COFFEE),
        rate_clp_per_as="4.8",
        color="#00704a",
        stock_copy="Starbucks · Frappuccino helado, preparado al momento.",
        voice=("cálido", "cercano"),
        product="coffee",
    ),
    # Dominó, the Santiago fuente de soda (since 1952), fights Pronto for the completo.
    DemoBrand(
        slug="domino",
        name="Dominó",
        category="pastry",  # the counter-food aisle: Pronto's rival, never its partner
        age_restricted=False,
        segments=(FAST_FOOD,),
        rate_clp_per_as="4.6",
        color="#d52b1e",
        stock_copy="Dominó · El completo de fuente de soda, de siempre.",
        tagline="Dominó. La fuente de soda de Chile.",  # printed on its own cups
        voice=("cercano", "chileno"),
        broad_match=False,  # the completo + bebida combo (DemoBrand.broad_match)
        product="pastry",
    ),
    # Vital de Chanqueahue (CCU), mineral water: Cachantun's and Benedictino's rival.
    DemoBrand(
        slug="vital",
        name="Vital",
        category="water",
        age_restricted=False,
        segments=(WATER,),
        rate_clp_per_as="2.9",
        color="#1f3c88",
        stock_copy="Vital · Agua mineral de Chanqueahue, bien fría.",
        voice=("fresco", "simple"),
        product="water",
    ),
    # Non-alcoholic, so the shop may sell it; an alcohol brand's 0.0 line is still
    # alcohol advertising (Ley 21.363): age-restricted, never shown with a minor.
    DemoBrand(
        slug="guinness-0-0",
        name="Guinness 0.0",
        category="beer_zero",
        age_restricted=True,
        segments=(BEER, NON_ALCOHOLIC),
        rate_clp_per_as="5.5",
        color="#1a1a1a",
        stock_copy="Guinness 0.0 · Toda la cremosidad, cero alcohol.",
        tagline="Lo bueno se hace esperar.",  # its "Good things come to those who wait"
        voice=("sereno", "nocturno"),
        product="beer_zero",
    ),
    # The demo's own moment (Platanus Hack): say "hackathon" or "Platanus" and Monster's
    # six-pack promo takes the screen. Its own aisle (energy_pack) and one audience no
    # camera label reaches, programming, so laptops on the demo table never trigger it:
    # only speech, or the promo's printed marker as demo insurance.
    DemoBrand(
        slug="monster-energy",
        name="Monster Energy",
        category="energy_pack",
        age_restricted=True,  # sugared energy drink: "ALTO EN AZÚCARES"
        segments=(PROGRAMMING,),
        # Saying it must always win the screen: above any basket the camera can build
        # (the hero Score + Juan Valdez peaks at ~17.5 with the night boost). Exact match
        # on an audience only speech or its marker reach, so it never outbids elsewhere.
        rate_clp_per_as="25",
        color="#000000",
        stock_copy="Monster Energy · Six pack 473 ml para la noche de código.",
        voice=("directo", "nocturno"),
        broad_match=False,  # exact match only: the promo never leaks into other moments
        product="energy_drink",
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


def _pack(name: str, prices: tuple[int, int, int, int]) -> tuple[tuple[int, str, str, int], ...]:
    """A named Chilean pack sized to the group ("Pack ruta", "Pack ruta para 3"), so
    baskets don't all read Combo/Dúo/Trío. One idiom at most, and only in the name."""
    return tuple(
        (party, name if party == 1 else f"{name} para {party}", suffix, price)
        for party, suffix, price in zip(
            (1, 2, 3, 4), ("", " x2", " x3", " x4"), prices, strict=True
        )
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
        bonus_clp_per_as="1.8",  # the bidding war swaps a seat, the basket stays
    ),
    DemoBasket(
        slugs=frozenset({"red-bull", "juan-valdez"}),
        names="Red Bull + Juan Valdez",
        tiers=_sized(4290, 7990, 10990, 13990),
        bonus_clp_per_as="1.8",  # the bidding war swaps a seat, the basket stays
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
        bonus_clp_per_as="1.8",
        tiers=(
            (1, "Completo palta + bebida", "", 3990),
            (2, "Dúo completo", " x2", 7490),
            (3, "Trío completo", " x3", 10490),
            (4, "Pack x4 completo", " x4", 13490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"monster", "pronto-copec"}),
        names="Monster + Pronto Copec",
        tiers=_pack("Pack turno noche", (4490, 8490, 11990, 15490)),
    ),
    # Group in the car: chips and a cold drink (both PepsiCo brands).
    DemoBasket(
        slugs=frozenset({"pepsi", "lays"}),
        names="Lay's + Pepsi",
        tiers=_pack("Pack carrete", (3490, 6490, 8990, 11490)),
    ),
    DemoBasket(
        slugs=frozenset({"cachantun", "marco-polo"}),
        names="Cachantun + Marco Polo",
        tiers=_pack("Pack ruta", (2990, 5490, 7790, 9990)),
    ),
    DemoBasket(
        slugs=frozenset({"super-8", "juan-valdez"}),
        names="Juan Valdez + Super 8",
        tiers=_pack("Café + Super 8", (3290, 5990, 8490, 10990)),
    ),
    # The completo's soda slot: Bilz y Pap fights Coca-Cola for it.
    DemoBasket(
        slugs=frozenset({"pronto-copec", "bilz-y-pap"}),
        names="Pronto Copec + Bilz y Pap",
        bonus_clp_per_as="1.8",
        tiers=(
            (1, "Completo palta + bebida", "", 3790),
            (2, "Dúo completo", " x2", 7290),
            (3, "Trío completo", " x3", 10190),
            (4, "Pack x4 completo", " x4", 13190),
        ),
    ),
    # Summer on the highway: ice cream and cold water, Savory vs Bresler.
    DemoBasket(
        slugs=frozenset({"savory", "cachantun"}),
        names="Cachantun + Savory",
        tiers=(
            (1, "Pack verano", "", 2990),
            (2, "Pack verano para 2", " x2", 5590),
            (3, "Pack verano para 3", " x3", 7990),
            (4, "Pack verano para 4", " x4", 9990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"bresler", "cachantun"}),
        names="Cachantun + Bresler",
        tiers=(
            (1, "Pack verano", "", 2890),
            (2, "Pack verano para 2", " x2", 5390),
            (3, "Pack verano para 3", " x3", 7790),
            (4, "Pack verano para 4", " x4", 9790),
        ),
    ),
    # Breakfast at the counter: medialuna with chocolate milk or a nectar.
    DemoBasket(
        slugs=frozenset({"colun", "pronto-copec"}),
        names="Pronto Copec + Colun",
        tiers=(
            (1, "Medialuna + chocolatada", "", 2290),
            (2, "Medialuna + chocolatada para 2", " x2", 4290),
            (3, "Medialuna + chocolatada para 3", " x3", 5990),
            (4, "Medialuna + chocolatada para 4", " x4", 7690),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"soprole", "pronto-copec"}),
        names="Pronto Copec + Soprole",
        tiers=(
            (1, "Medialuna + chocolatada", "", 2190),
            (2, "Medialuna + chocolatada para 2", " x2", 4190),
            (3, "Medialuna + chocolatada para 3", " x3", 5890),
            (4, "Medialuna + chocolatada para 4", " x4", 7490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"watts", "pronto-copec"}),
        names="Pronto Copec + Watt's",
        tiers=(
            (1, "Medialuna + néctar", "", 2390),
            (2, "Medialuna + néctar para 2", " x2", 4490),
            (3, "Medialuna + néctar para 3", " x3", 6290),
            (4, "Medialuna + néctar para 4", " x4", 7990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"andina-del-valle", "pronto-copec"}),
        names="Pronto Copec + Andina del Valle",
        tiers=(
            (1, "Medialuna + néctar", "", 2290),
            (2, "Medialuna + néctar para 2", " x2", 4390),
            (3, "Medialuna + néctar para 3", " x3", 6190),
            (4, "Medialuna + néctar para 4", " x4", 7890),
        ),
    ),
    # Road snacks with a sports drink (PepsiCo with PepsiCo; Powerade with Marco Polo).
    DemoBasket(
        slugs=frozenset({"gatorade", "lays"}),
        names="Lay's + Gatorade",
        tiers=_pack("Pack partido", (3790, 6990, 9790, 12490)),
    ),
    DemoBasket(
        slugs=frozenset({"powerade", "marco-polo"}),
        names="Marco Polo + Powerade",
        tiers=_sized(3590, 6690, 9390, 11990),
    ),
    # Something sweet with the coffee: Ambrosoli vs Super 8.
    DemoBasket(
        slugs=frozenset({"ambrosoli", "juan-valdez"}),
        names="Juan Valdez + Ambrosoli",
        tiers=_sized(3190, 5790, 8290, 10690),
    ),
    # The team's references: Dominó's completo with a bebida or a Guinness 0.0 (the fuente
    # de soda classic, completo + schop, made legal for a gas station), a Frappuccino
    # with a medialuna, and a picnic: Vital and chips.
    DemoBasket(
        slugs=frozenset({"domino", "coca-cola"}),
        names="Dominó + Coca-Cola",
        bonus_clp_per_as="1.8",
        tiers=(
            (1, "Completo Dominó + bebida", "", 4990),
            (2, "Dúo Dominó", " x2", 9590),
            (3, "Trío Dominó", " x3", 13990),
            (4, "Pack x4 Dominó", " x4", 18290),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"domino", "guinness-0-0"}),
        names="Dominó + Guinness 0.0",
        tiers=_pack("Completo + Guinness 0.0", (5990, 11490, 16790, 21990)),
    ),
    DemoBasket(
        slugs=frozenset({"starbucks", "pronto-copec"}),
        names="Starbucks + Pronto Copec",
        tiers=_pack("Frappuccino + medialuna", (5490, 10490, 15290, 19990)),
    ),
    DemoBasket(
        slugs=frozenset({"vital", "lays"}),
        names="Vital + Lay's",
        tiers=_pack("Pack picnic", (3690, 6890, 9790, 12490)),
    ),
    # Solo offers: every brand sells alone too, sized to the group.
    DemoBasket(
        slugs=frozenset({"score"}),
        names="Score",
        tiers=_solo(
            ("Score Guaraná 500 ml", "Dos Score", "Tres Score", "Cuatro Score"),
            (1590, 2890, 3990, 4990),
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
            # The counter's own star: the completo palta (Copec app, nov 2026: $2.500).
            ("Completo palta", "Dos completos", "Tres completos", "Cuatro completos"),
            (2490, 4790, 6990, 8990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"coca-cola"}),
        names="Coca-Cola",
        tiers=_solo(
            ("Coca-Cola Zero Azúcar 591 ml", "Dos Coca-Cola", "Tres Coca-Cola", "Cuatro Coca-Cola"),
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
    DemoBasket(
        slugs=frozenset({"ambrosoli"}),
        names="Ambrosoli",
        tiers=_solo(
            ("Dulces Ambrosoli", "Dos Ambrosoli", "Tres Ambrosoli", "Cuatro Ambrosoli"),
            (990, 1790, 2490, 3190),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"bilz-y-pap"}),
        names="Bilz y Pap",
        tiers=_solo(
            # CCU runs a fixed $1.000 price on 600 ml PET sodas (ccu.cl bases legales 2026).
            ("Bilz o Pap Zero 600 ml", "Dos Bilz y Pap", "Tres Bilz y Pap", "Cuatro Bilz y Pap"),
            (990, 1890, 2690, 3490),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"powerade"}),
        names="Powerade",
        tiers=_solo(
            ("Powerade helado", "Dos Powerade", "Tres Powerade", "Cuatro Powerade"),
            (1490, 2690, 3790, 4790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"gatorade"}),
        names="Gatorade",
        tiers=_solo(
            ("Gatorade helado", "Dos Gatorade", "Tres Gatorade", "Cuatro Gatorade"),
            (1590, 2890, 3990, 4990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"colun"}),
        names="Colun",
        tiers=_solo(
            ("Chocolatada Colun", "Dos chocolatadas", "Tres chocolatadas", "Cuatro chocolatadas"),
            (1190, 2190, 2990, 3790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"soprole"}),
        names="Soprole",
        tiers=_solo(
            ("Chocolatada Soprole", "Dos chocolatadas", "Tres chocolatadas", "Cuatro chocolatadas"),
            (1090, 1990, 2890, 3590),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"watts"}),
        names="Watt's",
        tiers=_solo(
            ("Néctar Watt's", "Dos néctares", "Tres néctares", "Cuatro néctares"),
            (1190, 2190, 2990, 3790),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"andina-del-valle"}),
        names="Andina del Valle",
        tiers=_solo(
            ("Néctar Andina del Valle", "Dos néctares", "Tres néctares", "Cuatro néctares"),
            (1090, 1990, 2890, 3590),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"savory"}),
        names="Savory",
        tiers=_solo(
            ("Savory Mega Chocolate Naranja", "Dos helados", "Tres helados", "Cuatro helados"),
            (1490, 2790, 3990, 4990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"bresler"}),
        names="Bresler",
        tiers=_solo(
            ("Helado Bresler", "Dos helados", "Tres helados", "Cuatro helados"),
            (1390, 2590, 3690, 4690),
        ),
    ),
    # One six-pack for the whole group, whatever its size (a demo promo price).
    DemoBasket(
        slugs=frozenset({"monster-energy"}),
        names="Monster Energy",
        tiers=((1, "6x Monster", " six pack", 1990),),
    ),
    DemoBasket(
        slugs=frozenset({"starbucks"}),
        names="Starbucks",
        tiers=_solo(
            ("Frappuccino Tall", "Dos Frappuccino", "Tres Frappuccino", "Cuatro Frappuccino"),
            (4490, 8490, 12290, 15990),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"domino"}),
        names="Dominó",
        tiers=_solo(
            ("Completo italiano Dominó", "Dos completos", "Tres completos", "Cuatro completos"),
            (3890, 7490, 10990, 14290),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"vital"}),
        names="Vital",
        tiers=_solo(
            ("Vital 1,6 L", "Dos Vital", "Tres Vital", "Cuatro Vital"),
            (1290, 2390, 3390, 4290),
        ),
    ),
    DemoBasket(
        slugs=frozenset({"guinness-0-0"}),
        names="Guinness 0.0",
        tiers=_solo(
            ("Guinness 0.0 440 ml", "Dos Guinness 0.0", "Tres Guinness 0.0", "Cuatro Guinness 0.0"),
            (2190, 3990, 5690, 7290),
        ),
    ),
)

CHILE = Catalog(
    name="chile",
    brands=BRANDS,
    baskets=BASKETS,
    # A highway shop: water and fitness cues are a road moment ("Queda camino. A
    # recargar."), never "Entrenaste" (the fictional seed's gym story).
    marker_cues=(
        ("super-8", CHOCOLATE_CANDY),
        ("ambrosoli", CANDY),
        ("colun", MILK),
        ("soprole", MILK),
        ("powerade", SPORTS_DRINKS),
        ("gatorade", SPORTS_DRINKS),
        ("monster-energy", PROGRAMMING),  # the hackathon promo, if the room is too loud
    ),
    segment_angles=(
        (WATER, "energy"),
        (FITNESS, "energy"),
        (SPORTS_DRINKS, "energy"),
        # "Una sin alcohol": the Guinness 0.0 moment, with its own "cero alcohol" hook.
        (NON_ALCOHOLIC, "social"),
        (PROGRAMMING, "energy"),  # a night of code: "¿No duermes?"
    ),
    # Every brand airs imagery: its own campaign photos, else its category's scene.
    category_scenes=True,
    # At a highway shop a can or a bottle means a tired driver or a thirsty one, not
    # someone leaving the gym.
    cue_segments=(
        ("energy drink can", ENERGY_DRINKS),
        ("bottle", WATER),
        ("energía", ENERGY_DRINKS),
        # What a shopper says at the counter (app/contract/speech_matching.py keyterms).
        ("sed", WATER),
        ("helado", ICE_CREAM),
        ("papas", SALTY_SNACKS),
        ("chocolate", CHOCOLATE_CANDY),
        ("dulce", CANDY),
        ("jugo", JUICES),
        ("leche", MILK),
        ("bebida", SOFT_DRINKS),
        ("isotónica", SPORTS_DRINKS),
        ("sin alcohol", NON_ALCOHOLIC),
        ("hackathon", PROGRAMMING),  # "hackatón", "Platanus" (the demo's own moment)
    ),
    daypart_boosts=(
        # Highway dayparts (venue local time, creative.domain.brief.daypart_for_hour):
        # breakfast before the road, completo + bebida at noon, cold drinks in the
        # afternoon heat, snacks for the evening drive, energy and coffee for the
        # night shift. Within [0.2, 5] (auction CategoryMultiplier).
        ("morning", "coffee", "1.4"),
        ("morning", "pastry", "1.3"),
        ("morning", "dairy", "1.25"),
        ("morning", "juice", "1.2"),
        ("morning", "ice_cream", "0.6"),
        ("morning", "snacks", "0.8"),
        ("midday", "pastry", "1.25"),
        ("midday", "cola", "1.3"),
        ("midday", "water", "1.1"),
        ("afternoon", "water", "1.3"),
        ("afternoon", "sports_drink", "1.3"),
        ("afternoon", "ice_cream", "1.5"),
        ("afternoon", "cola", "1.15"),
        ("afternoon", "coffee", "0.85"),
        ("afternoon", "pastry", "1.15"),  # la once: sándwich or medialuna at 5 p.m.
        ("evening", "snacks", "1.25"),
        ("evening", "candy", "1.15"),
        ("evening", "cola", "1.1"),
        ("evening", "pastry", "1.2"),  # Pronto's counter: the completo after work
        ("night", "energy_drink", "1.4"),
        ("night", "coffee", "1.25"),
        ("night", "pastry", "1.2"),  # the trucker's completo
        ("night", "dairy", "0.7"),
        ("night", "ice_cream", "0.6"),
    ),
    weather_boosts=(
        # Santiago summer (30 °C+): cold drinks and ice cream at every hour; a hot
        # coffee loses its pull. Winter or rain: coffee, la once and something warm.
        ("hot", "ice_cream", "1.6"),
        ("hot", "water", "1.4"),
        ("hot", "sports_drink", "1.35"),
        ("hot", "cola", "1.2"),
        ("hot", "beer_zero", "1.2"),
        ("hot", "juice", "1.15"),
        ("hot", "coffee", "0.8"),
        ("warm", "ice_cream", "1.25"),
        ("warm", "water", "1.15"),
        ("warm", "sports_drink", "1.1"),
        ("cold", "coffee", "1.35"),
        ("cold", "pastry", "1.2"),
        ("cold", "dairy", "1.1"),
        ("cold", "ice_cream", "0.6"),
        ("cold", "water", "0.85"),
        ("rain", "coffee", "1.2"),
        ("rain", "pastry", "1.15"),
        ("rain", "snacks", "1.1"),
        ("rain", "ice_cream", "0.8"),
    ),
)

# An uneven market, like a real one (docs/demo.md): who spends big, who is a lean
# challenger, whose budget runs out mid-demo, and what each pays outside its core
# audience. slug: (opening budget CLP, rate on non-core segments CLP/attention-second;
# None = the core rate everywhere). The Postgres registry keeps a stored campaign's
# budget, so a changed budget here applies to campaigns opened after it.
MARKET_SHAPE: dict[str, tuple[int, str | None]] = {
    "pronto-copec": (200_000, None),  # the house: its full rate on its own counter
    "coca-cola": (150_000, "3.5"),
    "red-bull": (120_000, "5.5"),  # premium leader
    "score": (90_000, None),  # the challenger bids its core rate everywhere
    "juan-valdez": (80_000, "3.5"),
    "pepsi": (70_000, "3"),
    "monster": (60_000, "4.5"),
    "gatorade": (55_000, "3"),
    "cachantun": (50_000, "2"),
    "lays": (45_000, "2.5"),
    "nescafe": (40_000, "2.5"),
    "powerade": (40_000, "2.8"),
    "bilz-y-pap": (35_000, "3"),
    "savory": (30_000, "2.6"),
    "colun": (25_000, "2.4"),
    "soprole": (25_000, "2.2"),
    "bresler": (22_000, "2.5"),
    "marco-polo": (20_000, "2.4"),
    "watts": (20_000, "2.2"),
    "andina-del-valle": (18_000, "2"),
    "super-8": (15_000, "2.2"),
    "ambrosoli": (12_000, "2"),
    "benedictino": (4_000, None),  # small test budget: paces out during the demo
    "domino": (110_000, "3"),  # Pronto's rival for the completo: the counter bidding war
    "starbucks": (70_000, "3.2"),
    "guinness-0-0": (50_000, "3.5"),
    "vital": (30_000, "2"),
    "monster-energy": (150_000, None),  # the hackathon promo: never paces out mid-demo
}

CHILE = replace(
    CHILE,
    brands=tuple(
        replace(
            brand,
            budget_clp=MARKET_SHAPE[brand.slug][0],
            secondary_rate_clp_per_as=MARKET_SHAPE[brand.slug][1],
        )
        if brand.slug in MARKET_SHAPE
        else brand
        for brand in CHILE.brands
    ),
)
