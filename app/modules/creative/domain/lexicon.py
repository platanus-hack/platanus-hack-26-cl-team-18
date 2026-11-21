"""Spanish (es-CL) word lists the guard and the scorer share. Pure data.

All entries are *normalized* (casefolded, accents stripped) and matched on
word boundaries by `contains_term`, so "Café" and "cafe" are the same token.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping

from app.modules.creative.domain.angle import Angle


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip()


def contains_term(normalized_text: str, term: str) -> bool:
    """Word-boundary match of an already-normalized term (a prefix if it ends in `*`)."""
    needle = normalize(term.rstrip("*"))
    if not needle:
        return False
    tail = r"\w*" if term.endswith("*") else r"(?!\w)"
    return re.search(rf"(?<!\w){re.escape(needle)}{tail}", normalized_text) is not None


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
