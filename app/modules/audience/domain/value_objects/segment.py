from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from app.modules.common.domain.primitives.value_object import ValueObject
from app.modules.common.domain.value_objects.percentage import Percentage


class SegmentSource(StrEnum):
    VISION = "vision"
    VLM = "vlm"
    SPEECH = "speech"
    SOUND = "sound"


@dataclass(frozen=True, slots=True)
class SegmentId(ValueObject):
    """Canonical IAB Audience Taxonomy ID selected by the tagging adapter."""

    value: str

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(cls, value: str) -> Self:
        return cls(value=value)

    def _enforce_types(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("SegmentId.value must be str")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.value) is None:
            raise ValueError("SegmentId.value must have the canonical segment ID format")


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentEstimate(ValueObject):
    """Estimated looking viewers matching one segment at the time of the moment."""

    segment_id: SegmentId
    viewer_count: int
    confidence: Percentage
    sources: frozenset[SegmentSource]
    speech_intent: Percentage | None = None

    def __post_init__(self) -> None:
        self._enforce_types()

    @classmethod
    def create(
        cls,
        *,
        segment_id: SegmentId,
        viewer_count: int,
        confidence: Percentage,
        sources: frozenset[SegmentSource],
        speech_intent: Percentage | None = None,
    ) -> Self:
        return cls(
            segment_id=segment_id,
            viewer_count=viewer_count,
            confidence=confidence,
            sources=sources,
            speech_intent=speech_intent,
        )

    def _enforce_types(self) -> None:
        if not isinstance(self.segment_id, SegmentId):
            raise TypeError("segment_id must be a SegmentId")
        if type(self.viewer_count) is not int:
            raise TypeError("viewer_count must be an int")
        if self.viewer_count < 0:
            raise ValueError("viewer_count cannot be negative")
        if not isinstance(self.confidence, Percentage):
            raise TypeError("confidence must be a Percentage")
        if not isinstance(self.sources, frozenset) or any(
            not isinstance(source, SegmentSource) for source in self.sources
        ):
            raise TypeError("sources must be a frozenset of SegmentSource values")
        if not self.sources:
            raise ValueError("at least one segment source is required")
        if self.speech_intent is not None:
            if not isinstance(self.speech_intent, Percentage):
                raise TypeError("speech_intent must be a Percentage")
            if SegmentSource.SPEECH not in self.sources:
                raise ValueError("speech_intent requires a speech source")
