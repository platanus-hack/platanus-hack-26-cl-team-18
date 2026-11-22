"""Warm-path ports of the audience context: keyframe tagging and speech topics."""

from app.modules.audience.application.ports.tagger import (
    AudienceTagger,
    KeyframeRedactionCheck,
    KeyframeTags,
    TaggedProduct,
    TaggedSegment,
    WarmScheduler,
    reject_every_keyframe,
)

__all__ = [
    "AudienceTagger",
    "KeyframeRedactionCheck",
    "KeyframeTags",
    "TaggedProduct",
    "TaggedSegment",
    "WarmScheduler",
    "reject_every_keyframe",
]
