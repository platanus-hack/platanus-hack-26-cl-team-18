"""The creative brief: everything a copy generator may know about one moment.

Every generator (stock templates, Claude, a recorded fixture) takes the same
frozen brief, so they are interchangeable and scored by the same eval. The
brief carries no personal data: counts, an angle, brands, an approved offer.
Utterance text never enters it (ADR 0005); only the angle it produced.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.campaigns.domain.book import BrandId
from app.modules.creative.domain.angle import AngleChoice
from app.modules.creative.domain.offer import BasketOffer

MAX_NAMED_PARTY = 6  # "para los 6"; larger groups are addressed as a group


class Daypart(StrEnum):
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


def daypart_for_hour(hour: int) -> Daypart:
    """The venue's local hour → the daypart the hook speaks to (pure; the caller converts)."""
    if not 0 <= hour <= 23:
        raise ValueError("hour must be within [0, 23]")
    if 6 <= hour < 11:
        return Daypart.MORNING
    if 11 <= hour < 15:
        return Daypart.MIDDAY
    if 15 <= hour < 19:
        return Daypart.AFTERNOON
    if 19 <= hour < 22:
        return Daypart.EVENING
    return Daypart.NIGHT


@dataclass(frozen=True, slots=True, kw_only=True)
class StageLimits:
    """Readability at 2–4 m: words per stage, plus a hard character cap."""

    teaser_words: int = 8
    offer_words: int = 18
    close_words: int = 10
    voice_words: int = 20
    max_chars: int = 120


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandBrief:
    brand_id: BrandId
    name: str
    category: str
    stock_copy: str  # approved fallback line from the brand kit
    tagline: str | None = None  # the brand's own signature line (approved, may air verbatim)
    voice: tuple[str, ...] = ()  # tone words for AI copy


@dataclass(frozen=True, slots=True, kw_only=True)
class CreativeBrief:
    brands: tuple[BrandBrief, ...]  # slot order; the lead brand first
    layout: str
    party: int  # people looking (the group the offer is sized to)
    people: int
    angle: AngleChoice
    offer: BasketOffer | None
    avoid_names: frozenset[str] = frozenset()  # competitors and brands outside the basket
    minor_likely: bool = False
    held_brand: str | None = None  # name of a held basket product (complete the basket)
    daypart: Daypart | None = None
    language: str = "es-CL"
    limits: StageLimits = field(default_factory=StageLimits)

    def __post_init__(self) -> None:
        if not self.brands:
            raise ValueError("A brief needs at least one brand")
        if not 0 <= self.party <= self.people:
            raise ValueError("party must satisfy 0 <= party <= people")
        if self.offer is not None and self.offer.brand_ids != self.brand_ids:
            raise ValueError("The offer must belong to exactly the basket's brands")
        names = {b.name.casefold() for b in self.brands}
        if any(n.casefold() in names for n in self.avoid_names):
            raise ValueError("A basket brand cannot be on the avoid list")

    @property
    def brand_ids(self) -> frozenset[BrandId]:
        return frozenset(b.brand_id for b in self.brands)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(b.name for b in self.brands)

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(b.category for b in self.brands)

    @property
    def is_group(self) -> bool:
        return self.party >= 2

    @property
    def named_party(self) -> int | None:
        """The number the copy may say ("para los 4"); None = too many to count aloud."""
        return self.party if self.party <= MAX_NAMED_PARTY else None

    @property
    def addressed_party(self) -> int | None:
        """The number the copy says ("para los 3"): only when the offer is priced for exactly
        this group (or there is no offer). Otherwise the copy says "todo el grupo"."""
        if not self.is_group or self.named_party is None:
            return None
        if self.offer is not None and self.offer.min_party != self.party:
            return None
        return self.party

    @property
    def offer_undersized(self) -> bool:
        """The best approved tier serves fewer people than are looking (a 4-pack for 12):
        the copy may offer it to share, never present it as covering everyone."""
        return self.offer is not None and self.offer.min_party < self.party

    def signature(self) -> str:
        """Cache key: two briefs with the same signature deserve the same copy."""
        material = "\x1f".join(
            (
                ",".join(sorted(str(b) for b in self.brand_ids)),
                self.layout,
                str(self.addressed_party or ("group" if self.is_group else self.party)),
                self.angle.angle.value,
                str(self.offer.min_party) if self.offer else "-",
                self.offer.price_text if self.offer else "-",
                self.held_brand or "-",
                "minor" if self.minor_likely else "-",
                self.daypart.value if self.daypart else "-",
                self.language,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
