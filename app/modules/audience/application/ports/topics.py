"""Speech topics from an utterance the keyterm matcher missed (warm path, ADR 0005).

The utterance text goes to the extractor in memory and is never stored or logged.
Only catalog segment keys with an intent strength come back (anti-corruption:
free text never reaches the auction).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedTopic:
    segment_key: str  # a key of the catalog it was given, nothing else
    intent: Decimal  # 0..1: how explicit the intent is ("qué hambre" ≈ 0.9)
    confidence: Decimal  # 0..1


class TopicExtractor(Protocol):
    async def extract(
        self, text: str, *, language: str, catalog: Mapping[str, str]
    ) -> tuple[ExtractedTopic, ...]:
        """`catalog` maps segment key → plain-language description for the model."""
        ...


MIN_TOPIC_CONFIDENCE = Decimal("0.6")


def accepted(
    topics: tuple[ExtractedTopic, ...], catalog: Mapping[str, str]
) -> tuple[ExtractedTopic, ...]:
    """Keep only known segments with enough confidence, strongest first, one per segment."""
    best: dict[str, ExtractedTopic] = {}
    for topic in topics:
        if topic.segment_key not in catalog or topic.confidence < MIN_TOPIC_CONFIDENCE:
            continue
        if not Decimal(0) <= topic.intent <= Decimal(1):
            continue
        current = best.get(topic.segment_key)
        if current is None or topic.intent > current.intent:
            best[topic.segment_key] = topic
    return tuple(sorted(best.values(), key=lambda t: (-t.intent, t.segment_key)))
