"""Translate loop internals into `dev.*` messages for the `/dev` QA console.

Composition-root helpers only: pure functions from domain/application objects to
contract messages (docs/dev-console-plan.md §4). Nothing here decides anything,
and nothing carries pixels, audio, transcripts or identity. Emitters check
`ConnectionHub.dev_count()` first, so none of this runs when nobody watches.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from app.contract.messages import (
    CopySourceKind,
    DevBrief,
    DevCopy,
    DevJob,
    DevKind,
    DevProviderLoad,
    DevSpeechTopic,
    DevTaggedProduct,
    DevTaggedSegment,
    DevViolation,
    DevVlm,
    MoneyDTO,
)
from app.modules.audience.application.classifier import TagReport
from app.modules.audience.domain.speech_evidence import SpeechTopic
from app.modules.audience.domain.value_objects.segment import SegmentId
from app.modules.creative.application.studio import GenerationRecord
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.draft import CopyDraft
from app.modules.creative.domain.guard import Violation
from app.modules.creative.domain.scoring import QualityScore
from app.realtime.warm_worker import WarmEvent

_KINDS: Mapping[str, DevKind] = {
    "tag": "tag",
    "topics": "topics",
    "copy": "copy",
    "voice": "voice",
    "video": "video",
}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _score(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.001")), "f")


def _ratio(value: float | Decimal) -> float:
    return min(max(float(value), 0.0), 1.0)


def job(event: WarmEvent, *, at: datetime) -> DevJob:
    """`provider:kind:screen:...` keys (warm_worker.submit) -> a typed job row."""
    parts = event.key.split(":")
    kind = _KINDS.get(parts[0], "other")
    screen = parts[1] if len(parts) > 1 and parts[1] else None
    return DevJob(
        screen_id=screen,
        provider=_clip(event.provider, 32),
        key=_clip(event.key, 200),
        kind=kind,
        state=event.state,
        at=at,
        elapsed_ms=event.elapsed_ms,
        error_type=None if event.error_type is None else _clip(event.error_type, 64),
        load=[
            DevProviderLoad(provider=_clip(provider, 32), in_flight=max(count, 0))
            for provider, count in sorted(event.in_flight.items())
        ][:16],
    )


def vlm(report: TagReport, *, finished_at: datetime, labels: Mapping[SegmentId, str]) -> DevVlm:
    tags = report.tags
    return DevVlm(
        screen_id=report.wire_screen_id,
        submitted_at=report.submitted_at,
        finished_at=finished_at,
        latency_ms=max((finished_at - report.submitted_at).total_seconds() * 1000, 0.0),
        outcome=report.outcome,
        model_version=_clip(tags.model_version, 128) if tags else "",
        segments=[
            DevTaggedSegment(
                segment_id=item.segment_id.value,
                label=_clip(labels.get(item.segment_id, ""), 80),
                confidence=_ratio(item.confidence),
                viewer_count=max(item.viewer_count, 0),
                kept=reason is None,
                drop_reason=reason,
            )
            for item, reason in report.segments
        ][:32],
        held_products=[
            DevTaggedProduct(
                category=_clip(item.category, 64),
                brand_ref=None if item.brand_ref is None else _clip(item.brand_ref, 64),
                confidence=_ratio(item.confidence),
                kept=reason is None,
                drop_reason=reason,
            )
            for item, reason in report.products
        ][:16],
        minor_likely=tags.minor_likely if tags else False,
        minor_likely_confidence=_ratio(tags.minor_likely_confidence) if tags else 0.0,
        reasoning=_clip(tags.reasoning, 500) if tags else "",
        error_type=report.error_type,
        expires_at=report.expires_at,
    )


def speech_topics(
    topics: Iterable[SpeechTopic], labels: Mapping[SegmentId, str]
) -> list[DevSpeechTopic]:
    return [
        DevSpeechTopic(
            segment_id=topic.segment_id.value,
            label=_clip(labels.get(topic.segment_id, ""), 80),
            confidence=None if topic.confidence is None else _ratio(topic.confidence.to_ratio()),
            expires_at=topic.expires_at.value,
        )
        for topic in topics
    ][:16]


def brief(value: CreativeBrief) -> DevBrief:
    offer = value.offer
    return DevBrief(
        brands=[_clip(name, 80) for name in value.names][:3],
        layout=_clip(value.layout, 32),
        party=value.party,
        people=value.people,
        angle=value.angle.angle.value,
        angle_reason=value.angle.reason.value,
        offer_label=None if offer is None else _clip(offer.label, 80),
        offer_price=None
        if offer is None
        else MoneyDTO(amount=format(offer.price.amount, "f"), currency=str(offer.price.currency)),
        held_brand=None if value.held_brand is None else _clip(value.held_brand, 80),
        daypart=None if value.daypart is None else value.daypart.value,
        minor_likely=value.minor_likely,
        signature=_clip(value.signature(), 200),
    )


def violations(items: Sequence[Violation]) -> list[DevViolation]:
    return [
        DevViolation(
            kind=_clip(item.kind.value, 32),
            where=_clip(item.where, 16),
            detail=_clip(item.detail, 160),
        )
        for item in items
    ][:16]


def copy(draft: CopyDraft, score: QualityScore | None = None) -> DevCopy:
    return DevCopy(
        source=CopySourceKind(draft.source.value),
        teaser=_clip(draft.teaser, 280),
        offer=_clip(draft.offer, 280),
        close=_clip(draft.close, 280),
        voice_line=_clip(draft.voice_line, 280),
        score=None if score is None else _score(score.total),
        passed=True if score is None else score.verdict.passed,
        violations=[] if score is None else violations(score.verdict.violations),
        model_version=None if draft.model_version is None else _clip(draft.model_version, 128),
    )


def last_record(records: Iterable[GenerationRecord], signature: str) -> GenerationRecord | None:
    """The studio's newest generation record for this brief signature."""
    for record in reversed(list(records)):
        if record.signature == signature:
            return record
    return None


def record_fields(record: GenerationRecord) -> dict[str, object]:
    return {
        "outcome": record.outcome.value,
        "ai_score": _score(record.ai_score),
        "stock_score": _score(record.stock_score),
        "latency_ms": max(record.latency_ms, 0.0),
        "violations": [
            DevViolation(kind=kind.value, where="all") for kind in sorted(record.violations)
        ][:16],
        "note": None if record.failure is None else _clip(record.failure.value, 200),
    }
