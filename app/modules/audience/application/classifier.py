"""Audience classifier: perception cues and VLM tags in, IAB segments out.

Hot path (`classify`): catalog lookups and pure fusion, no IO, called from the
decision path for every snapshot. Warm path (`observe_keyframe`): the keyframe
goes to the `AudienceTagger` on the `WarmScheduler`, never awaited; its
findings are kept per screen for a few seconds and `on_findings` asks the
caller to re-decide with them.

Privacy: keyframe bytes live only in the closure of their warm job and are
never stored or logged. Held products describe objects, never people.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import anyio

from app.contract.messages import AudienceKeyframe, AudienceSnapshot
from app.modules.audience.application.ports import (
    AudienceTagger,
    KeyframeTags,
    TaggedSegment,
    WarmScheduler,
)
from app.modules.audience.domain.catalog import SegmentCatalog
from app.modules.audience.domain.classification import (
    Classification,
    ClassifierSettings,
    KeyframeFindings,
    classify,
)
from app.modules.audience.domain.value_objects.evidence import HeldProduct, SegmentEvidence
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

logger = logging.getLogger(__name__)

# ADR 0008 D2: a prop's ArUco marker travels as DetectedObject("product:<slug>").
HELD_LABEL_PREFIX = "product:"
VLM_TRUST = Percentage.create(90)


class DegradedSignal(StrEnum):
    VLM = "vlm"


class WarmSignalFailed(RuntimeError):
    """A warm provider call failed; carries the error type only, never its text."""


type FindingsListener = Callable[[str], None]
type DegradationListener = Callable[[str, DegradedSignal, bool], None]


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceTtl:
    # A snapshot is the full current reading; this only bounds a single fact's life.
    perception: timedelta = timedelta(seconds=3)
    vlm: timedelta = timedelta(seconds=6)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceReliability:
    """How far a model's self-reported confidence is trusted."""

    vlm: Percentage = VLM_TRUST


@dataclass(slots=True)
class _Screen:
    lock: threading.Lock = field(default_factory=threading.Lock)
    keyframe: KeyframeFindings | None = None
    biddable: frozenset[SegmentId] = frozenset()
    looking: int = 0


def _ratio(value: float | Decimal) -> Decimal:
    ratio = value if isinstance(value, Decimal) else Decimal(str(value))
    return min(max(ratio, Decimal(0)), Decimal(1))


def _percent(value: float | Decimal) -> Percentage:
    return Percentage.create(_ratio(value) * 100)


class AudienceClassifier:
    def __init__(
        self,
        *,
        catalog: SegmentCatalog,
        settings: ClassifierSettings | None = None,
        ttl: EvidenceTtl | None = None,
        reliability: SourceReliability | None = None,
        tagger: AudienceTagger | None = None,
        warm: WarmScheduler | None = None,
        on_findings: FindingsListener | None = None,
        on_degraded: DegradationListener | None = None,
    ) -> None:
        self.catalog = catalog
        self._settings = settings or ClassifierSettings()
        self._ttl = ttl or EvidenceTtl()
        self._reliability = reliability or SourceReliability()
        self._tagger = tagger
        self._warm = warm
        self._on_findings = on_findings
        self._on_degraded = on_degraded
        self._screens: dict[str, _Screen] = {}
        self._brand_categories = {
            product.brand_ref: product.category for product in catalog.products if product.brand_ref
        }

    def _screen(self, wire_screen_id: str) -> _Screen:
        return self._screens.setdefault(wire_screen_id, _Screen())

    # ------------------------------------------------------------ hot path

    def classify(self, snapshot: AudienceSnapshot, *, at: datetime) -> Classification:
        now = UtcTimestamp.create(at)
        expires = UtcTimestamp.create(at + self._ttl.perception)
        cues = [item for item in snapshot.objects if not item.label.startswith(HELD_LABEL_PREFIX)]
        perception = (
            *self._cue_evidence(
                SegmentSource.VISION,
                ((item.label, item.score) for item in cues),
                now=now,
                expires=expires,
                count_per_hit=True,
            ),
            *self._cue_evidence(
                SegmentSource.SOUND,
                ((item.label, item.score) for item in snapshot.sound_scene),
                now=now,
                expires=expires,
                count_per_hit=False,
            ),
        )
        markers = tuple(
            HeldProduct.create(
                category=self._brand_categories[slug],
                brand_ref=slug,
                confidence=_percent(item.score),
            )
            for item in snapshot.objects
            if item.label.startswith(HELD_LABEL_PREFIX)
            and (slug := item.label.removeprefix(HELD_LABEL_PREFIX)) in self._brand_categories
        )
        screen = self._screen(snapshot.screen_id)
        with screen.lock:
            result = classify(
                perception=perception,
                marker_products=markers,
                keyframe=screen.keyframe,
                looking=snapshot.looking,
                minor_likely=snapshot.minor_likely.value,
                minor_likely_confidence=_percent(snapshot.minor_likely.confidence),
                now=now,
                previously_biddable=screen.biddable,
                settings=self._settings,
            )
            screen.biddable = frozenset(segment.segment_id for segment in result.segments)
            screen.looking = snapshot.looking
        return result

    # ------------------------------------------------------------ warm path

    def observe_keyframe(
        self, keyframe: AudienceKeyframe, *, jpeg: bytes, received_at: datetime
    ) -> None:
        wire_id = keyframe.screen_id
        if self._tagger is None or self._warm is None:
            self._degraded(wire_id, True)
            return
        tagger, looking = self._tagger, self._screen(wire_id).looking
        taken_at = UtcTimestamp.create(received_at)

        async def tag() -> None:
            try:
                tags = await tagger.tag(jpeg=jpeg, looking=looking)
            except Exception as exc:
                self._degraded(wire_id, True)
                raise WarmSignalFailed(f"keyframe tagging failed: {type(exc).__name__}") from None
            self._degraded(wire_id, False)
            await anyio.to_thread.run_sync(self._apply_tags, wire_id, taken_at, tags)

        # One tagging call in flight per screen; keyframes arriving meanwhile are dropped.
        self._warm.submit_threadsafe("vlm", f"tag:{wire_id}", tag)

    def _apply_tags(self, wire_id: str, taken_at: UtcTimestamp, tags: KeyframeTags) -> None:
        expires = UtcTimestamp.create(taken_at.value + self._ttl.vlm)
        findings = KeyframeFindings(
            evidence=self._model_evidence(tags.segments, observed_at=taken_at, expires=expires),
            held_products=tuple(
                HeldProduct.create(
                    category=item.category,
                    brand_ref=item.brand_ref,
                    confidence=_percent(item.confidence),
                )
                for item in tags.held_products
                if item.category in self.catalog.categories
                and (item.brand_ref is None or item.brand_ref in self._brand_categories)
            ),
            minor_likely=tags.minor_likely,
            minor_likely_confidence=_percent(tags.minor_likely_confidence),
            measured_at=taken_at,
            expires_at=expires,
            model_version=tags.model_version,
        )
        screen = self._screen(wire_id)
        with screen.lock:
            current = screen.keyframe
            if current is not None and findings.measured_at.value <= current.measured_at.value:
                return  # a newer keyframe already landed
            screen.keyframe = findings
        # Outside our lock: the caller re-decides, which calls classify() again.
        if self._on_findings is not None:
            self._on_findings(wire_id)

    # ------------------------------------------------------------ helpers

    def _cue_evidence(
        self,
        source: SegmentSource,
        observations: Iterable[tuple[str, float]],
        *,
        now: UtcTimestamp,
        expires: UtcTimestamp,
        count_per_hit: bool,
    ) -> tuple[SegmentEvidence, ...]:
        """Catalog cues -> one evidence per segment: strongest hit, hits counted."""
        best: dict[SegmentId, Decimal] = {}
        counts: dict[SegmentId, int] = {}
        for label, score in observations:
            for rule in self.catalog.rules_for(source, label):
                weighted = _ratio(score) * rule.weight.value / 100
                if weighted <= 0:
                    continue
                best[rule.segment_id] = max(best.get(rule.segment_id, Decimal(0)), weighted)
                counts[rule.segment_id] = counts.get(rule.segment_id, 0) + 1
        return tuple(
            SegmentEvidence.create(
                segment_id=segment_id,
                source=source,
                confidence=_percent(best[segment_id]),
                viewer_count=counts[segment_id] if count_per_hit else 1,
                observed_at=now,
                expires_at=expires,
            )
            for segment_id in sorted(best, key=lambda segment_id: segment_id.value)
        )

    def _model_evidence(
        self,
        segments: tuple[TaggedSegment, ...],
        *,
        observed_at: UtcTimestamp,
        expires: UtcTimestamp,
    ) -> tuple[SegmentEvidence, ...]:
        trust = self._reliability.vlm.value / 100
        merged: dict[SegmentId, tuple[Decimal, int]] = {}
        for item in segments:
            if item.segment_id not in self.catalog or item.viewer_count < 1:
                continue
            weighted = _ratio(item.confidence) * trust
            previous = merged.get(item.segment_id, (Decimal(0), 0))
            merged[item.segment_id] = (
                max(previous[0], weighted),
                max(previous[1], item.viewer_count),
            )
        return tuple(
            SegmentEvidence.create(
                segment_id=segment_id,
                source=SegmentSource.VLM,
                confidence=_percent(weight),
                viewer_count=count,
                observed_at=observed_at,
                expires_at=expires,
            )
            for segment_id, (weight, count) in sorted(
                merged.items(), key=lambda entry: entry[0].value
            )
            if weight > 0
        )

    def _degraded(self, wire_id: str, active: bool) -> None:
        if self._on_degraded is not None:
            self._on_degraded(wire_id, DegradedSignal.VLM, active)
