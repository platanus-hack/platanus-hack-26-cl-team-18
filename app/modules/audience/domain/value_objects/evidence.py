"""One source's claim about a segment, and the branded objects people hold."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

_REF = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
ZERO = Decimal(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentEvidence(ValueObject):
    """A source says `viewer_count` looking viewers match a segment.

    Confidence already folds in the cue weight or the source's reliability.
    Speech evidence fades linearly until it expires; every other source holds
    its confidence until it expires.
    """

    segment_id: SegmentId
    source: SegmentSource
    confidence: Percentage
    viewer_count: int
    observed_at: UtcTimestamp
    expires_at: UtcTimestamp
    speech_intent: Percentage | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(
        cls,
        *,
        segment_id: SegmentId,
        source: SegmentSource,
        confidence: Percentage,
        viewer_count: int,
        observed_at: UtcTimestamp,
        expires_at: UtcTimestamp,
        speech_intent: Percentage | None = None,
        model_version: str | None = None,
    ) -> Self:
        return cls(
            segment_id=segment_id,
            source=source,
            confidence=confidence,
            viewer_count=viewer_count,
            observed_at=observed_at,
            expires_at=expires_at,
            speech_intent=speech_intent,
            model_version=model_version,
        )

    def is_live(self, now: UtcTimestamp) -> bool:
        return now.value < self.expires_at.value

    def weight_at(self, now: UtcTimestamp) -> Decimal:
        """Confidence as a ratio in [0, 1] at `now`; zero once expired."""
        if not self.is_live(now):
            return ZERO
        ratio = self.confidence.value / 100
        if self.source is not SegmentSource.SPEECH:
            return ratio
        lifetime = (self.expires_at.value - self.observed_at.value).total_seconds()
        remaining = (self.expires_at.value - max(now.value, self.observed_at.value)).total_seconds()
        return ratio * Decimal(str(remaining)) / Decimal(str(lifetime))

    def _enforce_types(self) -> None:
        if not isinstance(self.segment_id, SegmentId):
            raise TypeError("segment_id must be a SegmentId")
        if not isinstance(self.source, SegmentSource):
            raise TypeError("source must be a SegmentSource")
        if not isinstance(self.confidence, Percentage):
            raise TypeError("confidence must be a Percentage")
        if type(self.viewer_count) is not int or self.viewer_count < 1:
            raise ValueError("evidence must name at least one viewer")
        if not isinstance(self.observed_at, UtcTimestamp) or not isinstance(
            self.expires_at, UtcTimestamp
        ):
            raise TypeError("evidence instants must be UtcTimestamp values")
        if self.expires_at.value <= self.observed_at.value:
            raise ValueError("evidence must expire after it was observed")
        if self.speech_intent is not None:
            if not isinstance(self.speech_intent, Percentage):
                raise TypeError("speech_intent must be a Percentage")
            if self.source is not SegmentSource.SPEECH:
                raise ValueError("speech_intent requires speech evidence")
        if self.model_version is not None and (
            not isinstance(self.model_version, str) or not self.model_version.strip()
        ):
            raise ValueError("model_version must be a non-empty string")


@dataclass(frozen=True, slots=True, kw_only=True)
class HeldProduct(ValueObject):
    """A product someone holds, read from its label. Describes the object only."""

    category: str
    brand_ref: str | None
    confidence: Percentage

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(cls, *, category: str, brand_ref: str | None, confidence: Percentage) -> Self:
        return cls(category=category, brand_ref=brand_ref, confidence=confidence)

    @property
    def key(self) -> str:
        return f"{self.category}:{self.brand_ref or '*'}"

    def _enforce_types(self) -> None:
        if not isinstance(self.category, str) or not _REF.fullmatch(self.category):
            raise ValueError("category must be a catalog reference")
        if self.brand_ref is not None and (
            not isinstance(self.brand_ref, str) or not _REF.fullmatch(self.brand_ref)
        ):
            raise ValueError("brand_ref must be a catalog reference")
        if not isinstance(self.confidence, Percentage):
            raise TypeError("confidence must be a Percentage")
