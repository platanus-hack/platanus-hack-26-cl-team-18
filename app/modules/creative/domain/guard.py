"""The copy guard: what may air (product invariants 1, 2, 3, 4). A pure specification.

AI copy never changes an offer's price or claims. Every draft, stock or AI,
passes through `CopyGuard.check` before it can air; a rejected AI draft falls
back to stock copy and the verdict is kept for the console and the eval.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from app.modules.common.domain.primitives.specification import Specification
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft, Stage
from app.modules.creative.domain.lexicon import (
    CHILD_APPEAL,
    CLICHES,
    COVERS_EVERYONE,
    FORBIDDEN_CLAIMS,
    GROUP_WORDS,
    KNOWN_BRANDS,
    NUMBER_WORDS,
    RESTRICTED_FOR_MINORS,
    SENSITIVE_OR_CREEPY,
    any_term,
    contains_term,
    normalize,
)


class ViolationKind(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    PRICE_MISSING = "price_missing"
    PRICE_CHANGED = "price_changed"
    COMPETITOR_NAME = "competitor_name"
    BRAND_MISSING = "brand_missing"
    PARTY_MISMATCH = "party_mismatch"
    RESTRICTED_FOR_MINOR = "restricted_for_minor"
    UNAPPROVED_CLAIM = "unapproved_claim"
    SENSITIVE_INFERENCE = "sensitive_inference"
    CONTACT_OR_LINK = "contact_or_link"
    UNSAFE_CHARACTERS = "unsafe_characters"  # hidden/look-alike characters that dodge the rules
    OFFER_CHANGED = "offer_changed"  # the offer line rewords what the basket contains
    CLICHE = "cliche"  # filler phrases and shouting punctuation (anti-slop house style)


@dataclass(frozen=True, slots=True, kw_only=True)
class Violation:
    kind: ViolationKind
    where: str  # a stage name, "voice" or "all"
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardVerdict:
    violations: tuple[Violation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def kinds(self) -> frozenset[ViolationKind]:
        return frozenset(v.kind for v in self.violations)


_PRICE = re.compile(r"\$\s?\d[\d.,]*|\b\d{1,3}(?:\.\d{3})+\s?(?:pesos|clp)\b", re.IGNORECASE)
_PERCENT = re.compile(r"\d+\s?%")
# Any amount-looking number (>= 100) must be the approved price: "9990 pesos", "12.990".
_AMOUNT = re.compile(r"(?<![\w.,])\d[\d.,]*\d(?![\w])|(?<![\w.,])\d{3,}(?![\w])")
# Money said in words or slang dodges the digit check: never allowed unless approved.
_SPELLED_MONEY = ("mil", "millon*", "luca*", "luka*", "cientos", "quinientos")
# Promo mechanics (2x1, 3x2) are claims: only if the brand approved them.
_PROMO = re.compile(r"\b\d\s?x\s?\d\b")
_THOUSANDS = re.compile(r"\b\d+\s?k\b|\b\d+\s?lucas?\b")  # "9k", "9 lucas"
# SERNAC (Ley 19.496 arts. 28 d, 30): an advertised price is the real, total price.
# "desde $X" / "a partir de $X" hide the total, and "+ IVA" excludes taxes.
_PRICE_QUALIFIER = re.compile(
    r"\b(?:desde|a partir de)\s*\$|\+\s*iva\b|\bmas iva\b|\biva no incluido\b|\bsin iva\b"
)
# Shouting and trailing-off punctuation: house style states, it never exclaims.
_LOUD_PUNCTUATION = re.compile(r"[!¡…—]|\.\.\.")
_SOCIAL = re.compile(r"\b(?:instagram|insta|tiktok|facebook|whatsapp|twitter|youtube)\b")


@lru_cache(maxsize=256)
def _brand_pattern(name: str) -> re.Pattern[str] | None:
    letters = [ch for ch in normalize(name) if ch.isalnum()]
    if not letters:
        return None
    pattern = r"[\W_]*".join(re.escape(ch) for ch in letters)
    return re.compile(rf"(?<![a-z0-9]){pattern}")


def _letters(normalized: str) -> str:
    """A normalized name without separators: "coca-cola" and "coca cola" are one brand."""
    return re.sub(r"[\W_]+", "", normalized)


def brand_mentioned(normalized: str, name: str) -> bool:
    """A brand at a word start, letters possibly split by separators ("R-a-y-o", "Rayos")."""
    pattern = _brand_pattern(name)
    return pattern is not None and pattern.search(normalized) is not None


_LINK = re.compile(r"https?://|www\.|\.(?:cl|com|net)\b|@\w|\+?\d[\d\s-]{7,}\d")
_PARTY_NUMBER = r"(\d{1,2}|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho)"
_PARTY_PATTERNS = (
    re.compile(rf"\bpara (?:los |las |ustedes )?{_PARTY_NUMBER}\b(?! ?min)"),
    re.compile(rf"\b{_PARTY_NUMBER} personas\b"),
    re.compile(rf"\b(?:los|las) {_PARTY_NUMBER}\b(?! ?min)"),
    re.compile(rf"\b(?:somos|son) {_PARTY_NUMBER}\b"),
)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _party_value(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def party_mentions(normalized: str) -> set[int]:
    """Party sizes the copy claims ("para los 4", "tres personas", "Trío")."""
    found = {_party_value(m.group(1)) for p in _PARTY_PATTERNS for m in p.finditer(normalized)}
    found |= {n for word, n in GROUP_WORDS.items() if contains_term(normalized, word)}
    return found


class CopyGuard(Specification[tuple[CopyDraft, CreativeBrief]]):
    """Satisfied when the draft can air for this brief."""

    def is_satisfied_by(self, candidate: tuple[CopyDraft, CreativeBrief]) -> bool:
        draft, brief = candidate
        return check_copy(draft, brief).passed


_JOINER = re.compile(r"\s*(?:\+|\by\b|\bmas\b|,)\s*")


def _carries(line: str, approved: str) -> bool:
    """`approved` appears in `line`, ignoring case, accents, spacing and how items are joined
    ("Rayo + Café Patio" = "Rayo y Café Patio"); quantities and items must stay as approved."""

    def squash(text: str) -> str:
        return re.sub(r"\s+", "", _JOINER.sub("+", normalize(text)))

    return squash(approved) in squash(line)


def _unsafe_characters(text: str) -> str | None:
    """Invisible (Cf/Cc) or non-Latin letters: zero-width tricks, Cyrillic look-alikes, emoji."""
    for ch in text:
        category = unicodedata.category(ch)
        if category in {"Cf", "Cc", "Co", "Cs"} and ch not in "\n":
            return f"U+{ord(ch):04X}"
        if category.startswith("L") and not unicodedata.name(ch, "").startswith("LATIN"):
            return ch
        if category == "So":  # emoji and pictographs
            return ch
    return None


def check_copy(draft: CopyDraft, brief: CreativeBrief) -> GuardVerdict:
    out: list[Violation] = []
    limits = brief.limits
    word_limits = {
        Stage.TEASER.value: limits.teaser_words,
        Stage.OFFER.value: limits.offer_words,
        Stage.CLOSE.value: limits.close_words,
        "voice": limits.voice_words,
    }
    lines = {stage.value: text for stage, text in draft.stages()} | {"voice": draft.voice_line}

    for where, text in lines.items():
        if not text.strip():
            out.append(Violation(kind=ViolationKind.EMPTY, where=where))
            continue
        words = len(text.split())
        if words > word_limits[where] or len(text) > limits.max_chars:
            out.append(
                Violation(
                    kind=ViolationKind.TOO_LONG,
                    where=where,
                    detail=f"{words} words / {len(text)} chars",
                )
            )

    offer = brief.offer
    approved_text = normalize(
        " ".join(
            [*(b.stock_copy for b in brief.brands), *(b.tagline or "" for b in brief.brands)]
            + ([offer.label, offer.text, *offer.claims] if offer else [])
        )
    )
    approved_price = _digits(str(offer.price.amount)) if offer else None
    basket = {normalize(name) for name in brief.names}
    own_letters = {_letters(name) for name in basket}
    # A basket brand's own name may contain a known brand ("Pronto Copec" ⊃ "Copec"),
    # or spell it with other separators ("Coca-Cola" = "Coca Cola"), the same way
    # brand_mentioned ignores them: naming your own brand is never naming a competitor.
    competitors = tuple(
        name
        for name in sorted(brief.avoid_names | set(KNOWN_BRANDS))
        if _letters(normalize(name)) not in own_letters
        and not any(contains_term(own, normalize(name)) for own in basket)
    )
    allowed_party = party_mentions(approved_text)
    if brief.addressed_party is not None:
        allowed_party.add(brief.addressed_party)

    for where, text in lines.items():
        norm = normalize(text)
        for match in _PRICE.finditer(text):
            if approved_price is None or _digits(match.group(0)) != approved_price:
                out.append(
                    Violation(kind=ViolationKind.PRICE_CHANGED, where=where, detail=match.group(0))
                )
        for match in _PERCENT.finditer(text):
            if match.group(0).replace(" ", "") not in approved_text.replace(" ", ""):
                out.append(
                    Violation(
                        kind=ViolationKind.UNAPPROVED_CLAIM, where=where, detail=match.group(0)
                    )
                )
        if _LINK.search(text):
            out.append(Violation(kind=ViolationKind.CONTACT_OR_LINK, where=where))
        if (bad := _unsafe_characters(text)) is not None:
            out.append(Violation(kind=ViolationKind.UNSAFE_CHARACTERS, where=where, detail=bad))
        priced = {m.span() for m in _PRICE.finditer(text)}
        for match in _AMOUNT.finditer(text):
            if any(a <= match.start() < b for a, b in priced):
                continue  # already checked as a $ price
            amount = _digits(match.group(0))
            if len(amount) >= 3 and amount != approved_price:
                out.append(
                    Violation(kind=ViolationKind.PRICE_CHANGED, where=where, detail=match.group(0))
                )
        if (term := any_term(norm, _SPELLED_MONEY)) and not contains_term(approved_text, term):
            out.append(Violation(kind=ViolationKind.PRICE_CHANGED, where=where, detail=term))
        for match in _PROMO.finditer(norm):
            if match.group(0).replace(" ", "") not in approved_text.replace(" ", ""):
                out.append(
                    Violation(
                        kind=ViolationKind.UNAPPROVED_CLAIM, where=where, detail=match.group(0)
                    )
                )
        for name in competitors:
            if brand_mentioned(norm, name):
                out.append(Violation(kind=ViolationKind.COMPETITOR_NAME, where=where, detail=name))
        if _SOCIAL.search(norm):
            out.append(Violation(kind=ViolationKind.CONTACT_OR_LINK, where=where, detail="social"))
        if (m := _THOUSANDS.search(norm)) and not contains_term(approved_text, m.group(0)):
            out.append(Violation(kind=ViolationKind.PRICE_CHANGED, where=where, detail=m.group(0)))
        if brief.minor_likely and (term := any_term(norm, RESTRICTED_FOR_MINORS)):
            out.append(Violation(kind=ViolationKind.RESTRICTED_FOR_MINOR, where=where, detail=term))
        if brief.minor_likely and (term := any_term(norm, CHILD_APPEAL)):
            out.append(Violation(kind=ViolationKind.RESTRICTED_FOR_MINOR, where=where, detail=term))
        if (m := _PRICE_QUALIFIER.search(norm)) and not contains_term(approved_text, m.group(0)):
            out.append(Violation(kind=ViolationKind.PRICE_CHANGED, where=where, detail=m.group(0)))
        if (term := any_term(norm, FORBIDDEN_CLAIMS)) and not contains_term(approved_text, term):
            out.append(Violation(kind=ViolationKind.UNAPPROVED_CLAIM, where=where, detail=term))
        if brief.offer_undersized and (term := any_term(norm, COVERS_EVERYONE)):
            out.append(
                Violation(kind=ViolationKind.PARTY_MISMATCH, where=where, detail=f"covers: {term}")
            )
        if (term := any_term(norm, CLICHES)) and not contains_term(approved_text, term):
            out.append(Violation(kind=ViolationKind.CLICHE, where=where, detail=term))
        if (m := _LOUD_PUNCTUATION.search(text)) is not None:
            out.append(Violation(kind=ViolationKind.CLICHE, where=where, detail=m.group(0)))
        if term := any_term(norm, SENSITIVE_OR_CREEPY):
            out.append(Violation(kind=ViolationKind.SENSITIVE_INFERENCE, where=where, detail=term))
        # Only the exact group the offer is priced for may be named (or numbers the brand
        # itself put in the offer label/text): never "Para los 5" next to a 4-pack price.
        for size in sorted(party_mentions(norm) - allowed_party):
            out.append(
                Violation(kind=ViolationKind.PARTY_MISMATCH, where=where, detail=f"says {size}")
            )

    if offer is not None and approved_price is not None:
        offer_prices = {_digits(m.group(0)) for m in _PRICE.finditer(draft.offer)}
        if approved_price not in offer_prices:
            out.append(
                Violation(
                    kind=ViolationKind.PRICE_MISSING,
                    where=Stage.OFFER.value,
                    detail=offer.price_text,
                )
            )

    # The offer text is what the customer gets ("Voltio + Café Patio x3"): quantities and
    # items are claims, so the offer line carries it (or the approved label, "Trío
    # post-entreno") verbatim. "Voltio más dos Café Patio" rewords the basket: rejected.
    if offer is not None and not (
        _carries(draft.offer, offer.text) or _carries(draft.offer, offer.label)
    ):
        out.append(
            Violation(kind=ViolationKind.OFFER_CHANGED, where=Stage.OFFER.value, detail=offer.text)
        )

    visible = normalize(" ".join((draft.teaser, draft.offer, draft.close)))
    for name in brief.names:
        if not contains_term(visible, name):
            out.append(Violation(kind=ViolationKind.BRAND_MISSING, where="all", detail=name))

    return GuardVerdict(violations=tuple(out))
