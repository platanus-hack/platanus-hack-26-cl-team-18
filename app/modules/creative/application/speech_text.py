"""What a creative says out loud, and how it is written so TTS reads it right.

Two decisions (ADR 0005 amendment, latency):

1. **Speak the approved offer, not the AI copy.** The offer line (label, text,
   price) comes from the brands and is fixed per party-size tier, so the set
   of spoken lines is known in advance: they are pre-generated at startup and
   every play is a cache hit (zero TTS latency, zero credits). AI copy varies
   every time and would be a cache miss on every creative. Without an offer
   (a solo creative), the lead slot's copy is spoken (budget-guarded).
2. **Normalize for speech ourselves.** Flash v2.5 ships with text
   normalization off for latency, so "$5.990" or "x2" would be read badly. We
   spell prices and counts in Spanish words deterministically (no latency, no
   extra provider work).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.contract.messages import CreativeOffer, CreativeSpec

_UNITS = [
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
    "veinte",
    "veintiuno",
    "veintidós",
    "veintitrés",
    "veinticuatro",
    "veinticinco",
    "veintiséis",
    "veintisiete",
    "veintiocho",
    "veintinueve",
]
_TENS = {
    3: "treinta",
    4: "cuarenta",
    5: "cincuenta",
    6: "sesenta",
    7: "setenta",
    8: "ochenta",
    9: "noventa",
}
_HUNDREDS = {
    1: "ciento",
    2: "doscientos",
    3: "trescientos",
    4: "cuatrocientos",
    5: "quinientos",
    6: "seiscientos",
    7: "setecientos",
    8: "ochocientos",
    9: "novecientos",
}


def _below_thousand(n: int) -> str:
    if n < 30:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _TENS[tens] + (f" y {_UNITS[unit]}" if unit else "")
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    return _HUNDREDS[hundreds] + (f" {_below_thousand(rest)}" if rest else "")


def spanish_number(n: int) -> str:
    """Cardinal in Spanish words, 0 ≤ n < 10^9 (`5990` -> `cinco mil novecientos noventa`)."""
    if not 0 <= n < 1_000_000_000:
        raise ValueError("number out of range for speech")
    if n < 1000:
        return _below_thousand(n)
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        head = "mil" if thousands == 1 else f"{_below_thousand(thousands)} mil"
        return head + (f" {_below_thousand(rest)}" if rest else "")
    millions, rest = divmod(n, 1_000_000)
    head = "un millón" if millions == 1 else f"{spanish_number(millions)} millones"
    return head + (f" {spanish_number(rest)}" if rest else "")


def _apocopate(words: str) -> str:
    # "veintiuno mil" -> "veintiún mil", "uno" before a noun -> "un" (we always speak a noun after).
    return re.sub(r"\buno\b(?= mil|$)", "un", words.replace("veintiuno mil", "veintiún mil"))


def _pesos(amount: int) -> str:
    return "un peso" if amount == 1 else f"{_apocopate(spanish_number(amount))} pesos"


_PRICE = re.compile(r"\$\s?(\d{1,3}(?:\.\d{3})+|\d+)")
_TIMES = re.compile(r"(?<![\w])x\s?(\d{1,2})\b", re.IGNORECASE)
_NUMBER = re.compile(r"\b\d{1,9}\b")


def speakable(text: str) -> str:
    """Rewrite symbols, prices and counts the way a Chilean narrator says them."""
    out = _PRICE.sub(lambda m: _pesos(int(m.group(1).replace(".", ""))), text)
    out = _TIMES.sub(lambda m: f"para {_apocopate(spanish_number(int(m.group(1))))}", out)
    out = (
        out.replace(" + ", " y ")
        .replace("+", " y ")
        .replace(" · ", ". ")
        .replace("·", ". ")
        .replace("&", " y ")
    )
    out = _NUMBER.sub(lambda m: spanish_number(int(m.group(0))), out)
    return re.sub(r"\s+", " ", out).strip()


def offer_line(label: str, text: str, price_amount: str) -> str:
    """The spoken form of an approved offer: `Dúo post-entreno: 2 combos … por N pesos`."""
    try:
        amount = int(Decimal(price_amount))
    except InvalidOperation, ValueError:
        return speakable(f"{label}: {text}")
    return speakable(f"{label}: {text} por ${amount}")


def spoken_line_for(spec: CreativeSpec) -> str:
    offer: CreativeOffer | None = spec.offer
    if offer is not None:
        return offer_line(offer.label, offer.text, offer.price.amount)
    return speakable(spec.slots[0].copy_text)
