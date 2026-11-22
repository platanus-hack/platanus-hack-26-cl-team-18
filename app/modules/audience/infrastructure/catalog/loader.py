"""Load the approved IAB segment catalog shipped with the backend."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.modules.audience.application.classifier import SourceReliability
from app.modules.audience.domain.catalog import (
    CatalogSegment,
    CueRule,
    ProductRef,
    SegmentCatalog,
    SegmentRelation,
    SegmentTier,
    normalize_cue,
)
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.common.domain.value_objects.percentage import Percentage

DEFAULT_CATALOG = Path(__file__).with_name("iab_audience_1_1_cstore.json")


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadedCatalog:
    catalog: SegmentCatalog
    reliability: SourceReliability
    source: str


def load_catalog(path: Path = DEFAULT_CATALOG) -> LoadedCatalog:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    segments = tuple(
        CatalogSegment(
            segment_id=SegmentId.create(item["id"]),
            label=item["label"],
            label_es=item.get("label_es"),
            path=item["path"],
            tier=SegmentTier(item["tier"]),
        )
        for item in raw["segments"]
    )
    cues = tuple(
        CueRule(
            source=SegmentSource(source),
            cue=normalize_cue(rule["cue"]),
            segment_id=SegmentId.create(rule["segment"]),
            weight=Percentage.create(rule["weight"]),
        )
        for source, rules in raw.get("cues", {}).items()
        for rule in rules
    )
    products = tuple(
        ProductRef(category=item["category"], brand_ref=item.get("brand_ref"))
        for item in raw["products"]
    )
    relations = tuple(
        SegmentRelation(
            left=SegmentId.create(item["segments"][0]),
            right=SegmentId.create(item["segments"][1]),
            weight=Percentage.create(item["weight"]),
        )
        for item in raw.get("related", [])
    )
    reliability = raw.get("reliability", {})
    return LoadedCatalog(
        catalog=SegmentCatalog(
            version=f"iab-audience-{raw['taxonomy_version']}/{raw['catalog_version']}",
            segments=segments,
            cues=cues,
            products=products,
            relations=relations,
        ),
        reliability=SourceReliability(
            vlm=Percentage.create(reliability.get("vlm", 90)),
        ),
        source=raw["source"],
    )
