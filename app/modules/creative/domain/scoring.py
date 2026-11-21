"""Deterministic quality score for a draft (0..1). Used at runtime and in the eval.

At runtime an AI draft replaces the stock draft only if it passes the guard
and scores at least as high, so a bad sample can never lower what airs. The
eval reports the same score, so "quality" means one thing everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.creative.domain.angle import Angle
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft
from app.modules.creative.domain.guard import GuardVerdict, check_copy, party_mentions
from app.modules.creative.domain.lexicon import ANGLE_KEYWORDS, any_term, contains_term, normalize

WEIGHTS: dict[str, Decimal] = {
    "angle": Decimal("0.25"),
    "party": Decimal("0.20"),
    "brands": Decimal("0.15"),
    "price": Decimal("0.10"),
    "readability": Decimal("0.15"),
    "distinct": Decimal("0.10"),
    "held": Decimal("0.05"),
}
ONE = Decimal(1)
ZERO = Decimal(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityScore:
    total: Decimal
    parts: dict[str, Decimal]
    verdict: GuardVerdict


def _readability(words: int, limit: int) -> Decimal:
    """1.0 up to 70 % of the limit, then down to 0.5 at the limit (the guard rejects beyond)."""
    if limit <= 0:
        return ZERO
    ratio = Decimal(words) / Decimal(limit)
    if ratio <= Decimal("0.7"):
        return ONE
    if ratio >= ONE:
        return Decimal("0.5")
    return ONE - (ratio - Decimal("0.7")) / Decimal("0.6")


def score_draft(draft: CopyDraft, brief: CreativeBrief) -> QualityScore:
    verdict = check_copy(draft, brief)
    teaser, offer = normalize(draft.teaser), normalize(draft.offer)
    headline = f"{teaser} {offer}"
    parts: dict[str, Decimal] = {}

    keywords = ANGLE_KEYWORDS.get(brief.angle.angle, ())
    if brief.angle.angle is Angle.COMPLETE_BASKET and brief.held_brand:
        keywords = (*keywords, brief.held_brand)
    parts["angle"] = ONE if not keywords or any_term(headline, keywords) else ZERO

    mentions = party_mentions(headline)
    if brief.is_group:
        addressed = bool(mentions) or any_term(headline, ("grupo", "todos", "ustedes")) is not None
        parts["party"] = ONE if addressed else ZERO
    else:
        parts["party"] = ONE if not mentions - {1} else ZERO

    named = sum(1 for name in brief.names if contains_term(offer, name))
    parts["brands"] = Decimal(named) / Decimal(len(brief.names))

    parts["price"] = (
        ONE
        if brief.offer is None
        or brief.offer.price_text.replace(".", "") in draft.offer.replace(".", "")
        else ZERO
    )

    limits = brief.limits
    parts["readability"] = (
        _readability(len(draft.teaser.split()), limits.teaser_words)
        + _readability(len(draft.offer.split()), limits.offer_words)
        + _readability(len(draft.close.split()), limits.close_words)
    ) / Decimal(3)

    lines = [normalize(t) for t in (draft.teaser, draft.offer, draft.close)]
    parts["distinct"] = Decimal(len(set(lines))) / Decimal(len(lines))

    parts["held"] = (
        ONE if not brief.held_brand or contains_term(headline, brief.held_brand) else ZERO
    )

    total = sum((WEIGHTS[k] * v for k, v in parts.items()), ZERO)
    if not verdict.passed:
        total = ZERO
    return QualityScore(total=total.quantize(Decimal("0.0001")), parts=parts, verdict=verdict)
