"""Spanish (es-CL) word lists the guard and the scorer share. Pure data.

All entries are *normalized* (casefolded, accents stripped) and matched on
word boundaries by `contains_term`, so "Café" and "cafe" are the same token.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from functools import lru_cache

from app.modules.creative.domain.angle import Angle


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip()


@lru_cache(maxsize=512)
def _term_pattern(term: str) -> re.Pattern[str] | None:
    """Cache bounded matching rules, never the copy being checked or its verdict."""
    needle = normalize(term.rstrip("*"))
    if not needle:
        return None
    tail = r"\w*" if term.endswith("*") else r"(?!\w)"
    return re.compile(rf"(?<!\w){re.escape(needle)}{tail}")


def contains_term(normalized_text: str, term: str) -> bool:
    """Word-boundary match of a normalized term (a prefix if it ends in `*`)."""
    pattern = _term_pattern(term)
    return pattern is not None and pattern.search(normalized_text) is not None


def any_term(normalized_text: str, terms: Iterable[str]) -> str | None:
    return next((t for t in terms if contains_term(normalized_text, t)), None)


# Words that make an angle obvious to the viewer (scorer: "does the copy answer the moment?").
ANGLE_KEYWORDS: Mapping[Angle, tuple[str, ...]] = {
    Angle.HUNGER: ("hambre", "comer", "antojo*", "picar", "algo rico", "almuerzo", "bajon"),
    Angle.ENERGY: ("energia", "recarga*", "despierta", "power", "pila*", "rendir"),
    Angle.COFFEE_BREAK: ("pausa", "cafe*", "cafecito", "break", "relaja*"),
    Angle.POST_WORKOUT: ("entren*", "post-entreno", "gym", "recupera*", "recarga*"),
    Angle.SOCIAL: ("panorama", "juntos", "brindis", "celebra*", "after"),
    Angle.QUEUE: ("fila", "esperando", "al pagar", "en caja", "mientras"),
    Angle.COMPLETE_BASKET: ("tu ", "pide", "completa*", "acompana*", "combina*"),
    Angle.GENERIC: (),
}

# Age-restricted vocabulary: never on screen when a minor is likely (invariant 2).
RESTRICTED_FOR_MINORS: tuple[str, ...] = (
    "cerveza*",
    "chela*",
    "alcohol*",
    "vino*",
    "pisco*",
    "copete*",
    "trago*",
    "apuesta*",
    "casino*",
    "brindis",
    "birra*",
    "cervez*",
    "whisk*",
    "vodka*",
    "ron",
    "gin",
    "tequila*",
    "fernet*",
    "piscola*",
    "beer*",
    "wine*",
    "bar",
)

# Child appeal and pester power: blocked when a minor is likely (invariant 2).
# Ley 20.606 + DS 13/2015: children's language, toys or characters make an "alto en"
# ad count as aimed at under-14s. CONAR (CChEP 2024): never exploit children's
# credulity or push them to ask their parents. No bare "papa": it also means potato.
CHILD_APPEAL: tuple[str, ...] = (
    "peque*",
    "nino*",
    "nina*",
    "infantil*",
    "juguete*",
    "sorpresa*",
    "colegio*",
    "recreo",
    "dibujito*",
    "monito*",
    "cajita*",
    "pidele*",
    "tu mama",
    "tu papa",
    "tus papas",
)

# Real brands the copy must never name unless they are in the basket (competitors,
# conquest or comparisons). The seed's own brands come from the brief's avoid list.
KNOWN_BRANDS: tuple[str, ...] = (
    "Red Bull",
    "Monster",
    "Rockstar",
    "Gatorade",
    "Powerade",
    "Coca-Cola",
    "Coca Cola",
    "Pepsi",
    "Fanta",
    "Sprite",
    "Starbucks",
    "Juan Valdez",
    "Nescafé",
    "Dunkin",
    "McDonald's",
    "Burger King",
    "KFC",
    "Subway",
    "Doggis",
    "Cristal",
    "Escudo",
    "Kunstmann",
    "Heineken",
    "Corona",
    "Copec",
    "Shell",
    "Oxxo",
    "Unimarc",
    "Lider",
    "Jumbo",
)

# Claims no brand kit approves by default (only approved claims may appear).
FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "garantizad*",
    "el mejor",
    "la mejor",
    "lo mejor",
    "los mejores",
    "#1",
    "sano*",
    "mitad",
    "oferta especial",
    "numero 1",
    "n°1",
    "nº1",
    "100%",
    "cura*",
    "previene",
    "adelgaz*",
    "saludable",
    "sin calorias",
    "medic*",
    "gratis",
    "regalo",
    "descuento*",
    "dcto*",
    "ahorra*",
    "rebaj*",
    "liquidacion",
    "off",
    # CONAR (CChEP 2024): claims must be truthful and verifiable; no superlatives,
    # endorsements or performance/health promises the brand kit didn't approve.
    "mas rico",
    "mas rica",
    "mas sabros*",
    "recomendad*",
    "nutricionista*",
    "cientific*",
    "comprobad*",
    "mas fuerte*",
    "te hace mas*",
    # Ley 21.363: alcohol copy must not encourage harmful drinking (or drink-driving).
    "tomense todas",
    "tomate todas",
    "toma todas",
    "sin limite*",
    "al seco",
    "hasta caer",
    "para el camino",
)

# Copy must never describe or infer things about the viewers (invariant 3) or sound like surveillance.
SENSITIVE_OR_CREEPY: tuple[str, ...] = (
    "embaraz*",
    "religi*",
    "catolic*",
    "evangelic*",
    "musulman*",
    "judi*",
    "gay",
    "lesbian*",
    "politic*",
    "enferm*",
    "diabet*",
    "gordit*",
    "flac*",
    "raza",
    "moren*",
    "rubi*",
    "anos tienes",
    "tu edad",
    "te vemos",
    "te estamos",
    "te reconoc*",
    "sabemos que",
    "tu cara",
    "camara",
    "te veo",
    "los veo",
    "las veo",
    "los vemos",
    "las vemos",
    "se nota",
    "se te nota",
    "parece que",
    "pareces",
    "parecen",
)

# Marketing filler that reads machine-written (anti-slop, docs/creative-generation.md §12).
# A line built on these says nothing a viewer can act on; stock copy stays on air instead.
CLICHES: tuple[str, ...] = (
    "momento perfecto",
    "complemento perfecto",
    "combinacion perfecta",
    "dupla perfecta",
    "match perfecto",
    "experiencia*",
    "descubre",
    "descubri",
    "eleva*",
    "sumergete",
    "deleit*",
    "irresistible*",
    "inigualable*",
    "sin igual",
    "imperdible*",
    "no te lo pierdas",
    "no te pierdas",
    "increible*",
    "espectacular*",
    "siguiente nivel",
    "otro nivel",
    "explosion de sabor*",
    "magia",
    "magic*",
    "epic*",
    "desata*",
    "revoluciona*",
    "sinergia",
    "tu momento",
    "este momento",
    "hecho para ti",
    "pensado para ti",
    "te lo mereces",
    "date un gusto",
    "vive la",
    "disfruta*",
)

# "Everyone is covered" claims: false next to an offer smaller than the group (SERNAC,
# Ley 19.496 art. 28: advertising must not mislead on the quantity offered).
COVERS_EVERYONE: tuple[str, ...] = (
    "todo el grupo",
    "todos ustedes",
    "para todos",
    "para todas",
    "cada uno",
    "cada una",
)

NUMBER_WORDS: Mapping[str, int] = {
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
}

# Group nouns that imply a party size.
GROUP_WORDS: Mapping[str, int] = {"duo": 2, "pareja": 2, "trio": 3, "cuarteto": 4}
