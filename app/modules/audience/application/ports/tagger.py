"""Warm-path ports of the audience classifier (keyframe tagging).

Adapters translate model output to catalog IDs (anti-corruption): every
`segment_id`, `category` and `brand_ref` returned here is already a member of
the catalog the adapter was built with. Confidences are ratios in [0, 1].
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.contract.messages import AudienceKeyframe
from app.modules.audience.domain.value_objects.segment import SegmentId


@dataclass(frozen=True, slots=True, kw_only=True)
class TaggedSegment:
    segment_id: SegmentId
    viewer_count: int
    confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class TaggedProduct:
    category: str
    brand_ref: str | None
    confidence: float


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyframeTags:
    segments: tuple[TaggedSegment, ...]
    held_products: tuple[TaggedProduct, ...]
    minor_likely: bool
    minor_likely_confidence: float
    reasoning: str
    model_version: str


class AudienceTagger(Protocol):
    async def tag(self, *, jpeg: bytes, looking: int) -> KeyframeTags:
        """Tag a redacted keyframe. The bytes stay in memory."""
        ...


# Whether a scene keyframe's pixels match its declared allow-list redaction
# (docs/privacy.md G2). The adapter is `keyframe_redaction.allowlist_redaction_holds`.
type KeyframeRedactionCheck = Callable[[AudienceKeyframe, bytes], bool]


def reject_every_keyframe(_header: AudienceKeyframe, _jpeg: bytes) -> bool:
    """The fail-closed default: without a check wired in, nothing reaches the VLM."""
    return False


class WarmScheduler(Protocol):
    """Runs warm jobs off the hot path (`app.realtime.warm_worker.WarmWorker`)."""

    def submit_threadsafe(
        self, provider: str, key: str, job: Callable[[], Awaitable[None]]
    ) -> None: ...
