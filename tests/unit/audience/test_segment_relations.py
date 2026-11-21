"""Curated same-need segment pairs in the catalog (read by the auction's SegmentAffinity)."""

from __future__ import annotations

import pytest

from app.modules.audience.domain.catalog import (
    CatalogSegment,
    SegmentCatalog,
    SegmentRelation,
    SegmentTier,
)
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.common.domain.value_objects.percentage import Percentage

COFFEE, RTD, WATER = (SegmentId.create(key) for key in ("977", "982", "1000"))
SEGMENTS = tuple(
    CatalogSegment(
        segment_id=segment, label=segment.value, path=segment.value, tier=SegmentTier.INTEREST
    )
    for segment in (COFFEE, RTD)
)


def relation(left: SegmentId, right: SegmentId, weight: int = 50) -> SegmentRelation:
    return SegmentRelation(left=left, right=right, weight=Percentage.create(weight))


@pytest.mark.parametrize(
    "relations",
    [
        (relation(COFFEE, WATER),),  # outside the catalog
        (relation(COFFEE, RTD), relation(RTD, COFFEE)),  # the same pair twice
    ],
)
def test_invalid_relations_are_rejected_by_the_catalog(
    relations: tuple[SegmentRelation, ...],
) -> None:
    with pytest.raises(ValueError):
        SegmentCatalog(version="t", segments=SEGMENTS, relations=relations)


@pytest.mark.parametrize("weight", [0, 100])
def test_a_relation_weight_is_strictly_partial(weight: int) -> None:
    with pytest.raises(ValueError):
        relation(COFFEE, RTD, weight)


def test_a_segment_is_not_related_to_itself() -> None:
    with pytest.raises(ValueError):
        relation(COFFEE, COFFEE)


def test_relations_survive_a_cue_table_swap() -> None:
    catalog = SegmentCatalog(version="t", segments=SEGMENTS, relations=(relation(COFFEE, RTD),))
    assert catalog.with_cues(()).relations == catalog.relations


def test_shipped_catalog_loads_its_curated_pairs() -> None:
    catalog = load_catalog().catalog
    assert catalog.relations
    assert relation(COFFEE, RTD, 70) in catalog.relations
