"""The copy guard: what may air (product invariants 1, 2, 3, 4). A pure specification.

AI copy never changes an offer's price or claims. Every draft, stock or AI,
passes through `CopyGuard.check` before it can air; a rejected AI draft falls
back to stock copy and the verdict is kept for the console and the eval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.common.domain.primitives.specification import Specification
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft, Stage
from app.modules.creative.domain.lexicon import (
    FORBIDDEN_CLAIMS,
    GROUP_WORDS,
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
            [*(b.stock_copy for b in brief.brands)]
            + ([offer.label, offer.text, *offer.claims] if offer else [])
        )
    )
    approved_price = _digits(str(offer.price.amount)) if offer else None

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
        for name in sorted(brief.avoid_names):
            if contains_term(norm, name):
                out.append(Violation(kind=ViolationKind.COMPETITOR_NAME, where=where, detail=name))
        if brief.minor_likely and (term := any_term(norm, RESTRICTED_FOR_MINORS)):
            out.append(Violation(kind=ViolationKind.RESTRICTED_FOR_MINOR, where=where, detail=term))
        if (term := any_term(norm, FORBIDDEN_CLAIMS)) and not contains_term(approved_text, term):
            out.append(Violation(kind=ViolationKind.UNAPPROVED_CLAIM, where=where, detail=term))
        if term := any_term(norm, SENSITIVE_OR_CREEPY):
            out.append(Violation(kind=ViolationKind.SENSITIVE_INFERENCE, where=where, detail=term))
        allowed_party = {brief.named_party or brief.party} | party_mentions(approved_text)
        if offer is not None:
            allowed_party.add(offer.min_party)
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

    visible = normalize(" ".join((draft.teaser, draft.offer, draft.close)))
    for name in brief.names:
        if not contains_term(visible, name):
            out.append(Violation(kind=ViolationKind.BRAND_MISSING, where="all", detail=name))

    return GuardVerdict(violations=tuple(out))
