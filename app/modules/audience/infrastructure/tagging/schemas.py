"""Structured-output schemas built from the catalog.

Segment IDs, categories and brand refs are `Literal` enums, so constrained
decoding cannot produce a value outside the catalog. There is no field for any
attribute of a person (age, gender, emotion, ethnicity...): the model has
nowhere to put a forbidden inference.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model

from app.modules.audience.domain.catalog import SegmentCatalog


def _literal(values: tuple[str, ...]) -> Any:
    if not values:
        raise ValueError("a catalog enum needs at least one value")
    return Literal[values]  # type: ignore[valid-type]


def keyframe_schema(catalog: SegmentCatalog) -> type[BaseModel]:
    config = ConfigDict(extra="forbid")
    segment = create_model(
        "KeyframeSegment",
        __config__=config,
        iab_id=(_literal(tuple(s.segment_id.value for s in catalog.segments)), ...),
        viewer_count=(int, ...),
        confidence=(float, ...),
    )
    brands = catalog.brand_refs
    product = create_model(
        "HeldProductTag",
        __config__=config,
        category=(_literal(catalog.categories), ...),
        brand_ref=((_literal(brands) | None) if brands else None, ...),
        confidence=(float, ...),
    )
    return create_model(
        "KeyframeTagging",
        __config__=config,
        segments=(list[segment], ...),  # type: ignore[valid-type]
        held_products=(list[product], ...),  # type: ignore[valid-type]
        minor_likely=(bool, ...),
        minor_likely_confidence=(float, ...),
        reasoning=(str, ...),
    )
