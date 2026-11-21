"""Warm-path ports of the audience context: keyframe tagging and speech topics."""

from app.modules.audience.application.ports.tagger import (
    AudienceTagger,
    KeyframeTags,
    TaggedProduct,
    TaggedSegment,
    WarmScheduler,
)

__all__ = ["AudienceTagger", "KeyframeTags", "TaggedProduct", "TaggedSegment", "WarmScheduler"]
