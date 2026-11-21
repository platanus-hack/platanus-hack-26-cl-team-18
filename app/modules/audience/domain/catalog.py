"""The approved segment catalog: the only place a free-text cue becomes a segment.

Perception labels (a COCO class, a sound-scene class, a spoken keyterm) are
translated here and nowhere else. A cue missing from the catalog never reaches
the auction (anti-corruption layer).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self

from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.common.domain.value_objects.percentage import Percentage

CUE_SOURCES = frozenset({SegmentSource.VISION, SegmentSource.SOUND, SegmentSource.SPEECH})


class SegmentTier(StrEnum):
    INTEREST = "interest"
    PURCHASE_INTENT = "purchase_intent"


def normalize_cue(text: str) -> str:
    """Lowercase, strip accents and collapse spaces: 'Bajón ' -> 'bajon'."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.split())


@dataclass(frozen=True, slots=True, kw_only=True)
class CatalogSegment:
    segment_id: SegmentId
    label: str
    path: str
    tier: SegmentTier


@dataclass(frozen=True, slots=True, kw_only=True)
class CueRule:
    """`cue` seen by `source` is evidence for `segment_id`, scaled by `weight`."""

    source: SegmentSource
    cue: str
    segment_id: SegmentId
    weight: Percentage

    def __post_init__(self) -> None:
        if self.source not in CUE_SOURCES:
            raise ValueError("cue rules cover vision, sound and speech only")
        if not self.cue or self.cue != normalize_cue(self.cue):
            raise ValueError("cue must be normalized with normalize_cue")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductRef:
    """A product the VLM may name on a held item: a category and optional brand."""

    category: str
    brand_ref: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentRelation:
    """A curated, symmetric "same need" pair: viewers of one are prospects for the other.

    The auction reads these (with the IAB hierarchy in `path`) to let a bid reach
    neighbouring segments at a discount (auction `SegmentAffinity`).
    """

    left: SegmentId
    right: SegmentId
    weight: Percentage

    def __post_init__(self) -> None:
        if self.left == self.right:
            raise ValueError("a segment is not related to itself")
        if not 0 < self.weight.value < 100:
            raise ValueError("a related-segment weight must be strictly between 0 and 100")


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentCatalog:
    version: str
    segments: tuple[CatalogSegment, ...]
    cues: tuple[CueRule, ...] = ()
    products: tuple[ProductRef, ...] = ()
    relations: tuple[SegmentRelation, ...] = ()
    _by_id: dict[SegmentId, CatalogSegment] = field(init=False, repr=False, compare=False)
    _by_cue: dict[tuple[SegmentSource, str], tuple[CueRule, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        by_id = {segment.segment_id: segment for segment in self.segments}
        if len(by_id) != len(self.segments):
            raise ValueError("catalog segment IDs must be unique")
        by_cue: dict[tuple[SegmentSource, str], list[CueRule]] = {}
        for rule in self.cues:
            if rule.segment_id not in by_id:
                raise ValueError(f"cue {rule.cue!r} points outside the catalog")
            by_cue.setdefault((rule.source, rule.cue), []).append(rule)
        if any(len({rule.segment_id for rule in rules}) != len(rules) for rules in by_cue.values()):
            raise ValueError("a cue may map to each segment once")
        pairs = [frozenset({item.left, item.right}) for item in self.relations]
        if any(item.left not in by_id or item.right not in by_id for item in self.relations):
            raise ValueError("a segment relation points outside the catalog")
        if len(set(pairs)) != len(pairs):
            raise ValueError("each pair of segments is related at most once")
        object.__setattr__(self, "_by_id", by_id)
        object.__setattr__(self, "_by_cue", {key: tuple(rules) for key, rules in by_cue.items()})

    def __contains__(self, segment_id: object) -> bool:
        return segment_id in self._by_id

    def segment(self, segment_id: SegmentId) -> CatalogSegment | None:
        return self._by_id.get(segment_id)

    def label(self, segment_id: SegmentId) -> str:
        segment = self._by_id.get(segment_id)
        return segment.label if segment is not None else segment_id.value

    def rules_for(self, source: SegmentSource, cue: str) -> tuple[CueRule, ...]:
        return self._by_cue.get((source, normalize_cue(cue)), ())

    def speech_cues(self) -> tuple[str, ...]:
        return tuple(
            sorted({rule.cue for rule in self.cues if rule.source is SegmentSource.SPEECH})
        )

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({product.category for product in self.products}))

    @property
    def brand_refs(self) -> tuple[str, ...]:
        return tuple(sorted({product.brand_ref for product in self.products if product.brand_ref}))

    def with_cues(self, cues: Iterable[CueRule]) -> Self:
        """The same segments and products with a different cue table."""
        return type(self)(
            version=self.version,
            segments=self.segments,
            cues=tuple(cues),
            products=self.products,
            relations=self.relations,
        )
