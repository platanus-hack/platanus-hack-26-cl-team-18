"""Warm tags from a face-blurred keyframe: objects and held branded products only.

CLAUDE.md: never describe a person when recognizing a held product. The tagger is
asked about objects and readable product brands, never about people. Its output is
restricted to labels and brand names we give it (anti-corruption), so free text
never reaches the auction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameTags:
    objects: tuple[tuple[str, Decimal], ...] = ()  # (catalog label, confidence 0..1)
    held_brands: tuple[tuple[str, Decimal], ...] = ()  # (brand name, confidence 0..1)
    model_version: str = ""
    reasoning: tuple[str, ...] = field(default_factory=tuple)  # short visible evidence


class KeyframeTagger(Protocol):
    async def tag(
        self, jpeg: bytes, *, object_labels: Sequence[str], brand_names: Sequence[str]
    ) -> FrameTags: ...


MIN_TAG_CONFIDENCE = Decimal("0.6")


def accepted_tags(
    tags: FrameTags, *, object_labels: Sequence[str], brand_names: Sequence[str]
) -> FrameTags:
    """Keep only known labels/brands with enough confidence (low confidence → safe fallback)."""
    labels, brands = set(object_labels), set(brand_names)
    return FrameTags(
        objects=tuple(
            (label, confidence)
            for label, confidence in tags.objects
            if label in labels and confidence >= MIN_TAG_CONFIDENCE
        ),
        held_brands=tuple(
            (brand, confidence)
            for brand, confidence in tags.held_brands
            if brand in brands and confidence >= MIN_TAG_CONFIDENCE
        ),
        model_version=tags.model_version,
        reasoning=tags.reasoning,
    )
