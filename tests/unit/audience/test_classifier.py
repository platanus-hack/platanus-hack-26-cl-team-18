"""AudienceClassifier: catalog cues and prop markers on the hot path, VLM tags on the warm path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contract.messages import (
    AudienceKeyframe,
    AudienceSnapshot,
    DetectedObject,
    MinorLikely,
    SoundScene,
)
from app.modules.audience.application.classifier import AudienceClassifier, DegradedSignal
from app.modules.audience.application.ports import KeyframeTags, TaggedProduct, TaggedSegment
from app.modules.audience.domain.classification import ClassifierSettings
from app.modules.audience.domain.value_objects.segment import SegmentId, SegmentSource
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.common.domain.value_objects.percentage import Percentage
from app.realtime.warm_worker import WarmWorker
from tests.unit.ai.async_runner import run_async

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
SCREEN = "screen-1"
COFFEE, FAST_FOOD, DOGS, MUSIC = (SegmentId.create(i) for i in ("977", "1404", "544", "481"))
LOADED = load_catalog()


def snapshot(*, looking=2, objects=(), sounds=(), minor=False) -> AudienceSnapshot:
    return AudienceSnapshot(
        screen_id=SCREEN,
        seq=1,
        measured_at=T0,
        people=looking,
        looking=looking,
        objects=[DetectedObject(label=label, score=score) for label, score in objects],
        sound_scene=[SoundScene(label=label, score=score) for label, score in sounds],
        minor_likely=MinorLikely(value=minor, confidence=0.9 if minor else 0.0),
    )


class FakeTagger:
    def __init__(self, tags: KeyframeTags | Exception) -> None:
        self.tags = tags
        self.calls: list[tuple[int, int]] = []

    async def tag(self, *, jpeg: bytes, looking: int) -> KeyframeTags:
        self.calls.append((len(jpeg), looking))
        if isinstance(self.tags, Exception):
            raise self.tags
        return self.tags


class CollectingWarm:
    """Collects warm jobs; `run` drains them on a real loop through a WarmWorker."""

    def __init__(self) -> None:
        self.jobs: list = []

    def submit_threadsafe(self, provider, key, job) -> None:
        self.jobs.append((provider, key, job))

    def run(self) -> None:
        async def drain() -> None:
            worker = WarmWorker()
            for provider, key, job in self.jobs:
                worker.submit(provider, key, job)
            await worker.drain()

        run_async(drain())
        self.jobs.clear()


def classifier(**kwargs) -> tuple[AudienceClassifier, list, list]:
    findings: list[str] = []
    degraded: list[tuple[DegradedSignal, bool]] = []
    built = AudienceClassifier(
        catalog=LOADED.catalog,
        reliability=LOADED.reliability,
        on_findings=findings.append,
        on_degraded=lambda screen, signal, active: degraded.append((signal, active)),
        **kwargs,
    )
    return built, findings, degraded


def keyframe() -> AudienceKeyframe:
    return AudienceKeyframe(screen_id=SCREEN, seq=1, measured_at=T0, byte_length=4)


def test_cues_become_counted_iab_segments() -> None:
    c, _, _ = classifier()
    result = c.classify(
        snapshot(looking=3, objects=[("cup", 1.0), ("cup", 0.8), ("hot dog", 0.9)]), at=T0
    )
    segments = {s.segment_id: s for s in result.segments}
    assert set(segments) == {COFFEE, FAST_FOOD}
    assert segments[COFFEE].viewer_count == 2  # two cups, two viewers
    assert segments[COFFEE].confidence.value == 55  # strongest cup x cue weight 55 %
    assert segments[COFFEE].sources == {SegmentSource.VISION}


def test_sound_and_vision_compound_and_unknown_labels_are_dropped() -> None:
    c, _, _ = classifier()
    result = c.classify(snapshot(objects=[("traffic light", 1.0)], sounds=[("music", 1.0)]), at=T0)
    assert [(s.segment_id, s.sources) for s in result.segments] == [(MUSIC, {SegmentSource.SOUND})]


def test_weak_cues_still_reach_the_auction_which_prices_them() -> None:
    c, _, _ = classifier()
    (weak,) = c.classify(snapshot(objects=[("dog", 0.05)]), at=T0).segments
    assert weak.confidence.value == Decimal("3.75")


def test_an_optional_floor_drops_weak_segments_for_bidding() -> None:
    c, _, _ = classifier(settings=ClassifierSettings(enter_at=Percentage.create(50)))
    result = c.classify(snapshot(objects=[("dog", 0.05)]), at=T0)
    assert result.segments == () and [s.segment_id for s in result.below_threshold] == [DOGS]


def test_prop_markers_become_held_products_not_segments() -> None:
    c, _, _ = classifier()
    result = c.classify(
        snapshot(objects=[("product:cafe-patio", 0.95), ("product:unknown", 1.0)]), at=T0
    )
    assert result.segments == ()
    assert [(p.category, p.brand_ref) for p in result.held_products] == [("coffee", "cafe-patio")]
    assert (
        c.classify(snapshot(looking=0, objects=[("product:voltio", 1.0)]), at=T0).held_products
        == ()
    )


def test_vlm_findings_merge_until_they_expire() -> None:
    warm = CollectingWarm()
    tagger = FakeTagger(
        KeyframeTags(
            segments=(
                TaggedSegment(segment_id=DOGS, viewer_count=1, confidence=0.8),
                TaggedSegment(segment_id=SegmentId.create("999999"), viewer_count=1, confidence=1),
            ),
            held_products=(
                TaggedProduct(category="coffee", brand_ref="cafe-patio", confidence=0.9),
                TaggedProduct(category="vodka", brand_ref=None, confidence=0.9),
            ),
            minor_likely=True,
            minor_likely_confidence=0.7,
            reasoning="dog leash and a branded coffee cup",
            model_version="claude-haiku-5-5@low",
        )
    )
    c, findings, degraded = classifier(tagger=tagger, warm=warm)
    c.classify(snapshot(looking=2, objects=[("dog", 1.0)]), at=T0)
    c.observe_keyframe(keyframe(), jpeg=b"\xff\xd8..", received_at=T0)
    warm.run()
    assert findings == [SCREEN] and tagger.calls == [(4, 2)]
    assert (DegradedSignal.VLM, False) in degraded

    merged = c.classify(snapshot(looking=2, objects=[("dog", 1.0)]), at=T0 + timedelta(seconds=1))
    (dogs,) = merged.segments
    assert dogs.sources == {SegmentSource.VISION, SegmentSource.VLM}
    # noisy-OR: vision 0.75, VLM 0.8 x trust 0.9 = 0.72 -> 1 - 0.25 x 0.28 = 0.93
    assert dogs.confidence.value == 93
    assert [(p.category, p.brand_ref) for p in merged.held_products] == [("coffee", "cafe-patio")]
    assert merged.minor_likely and merged.minor_likely_confidence == Percentage.create(70)

    later = c.classify(snapshot(looking=2), at=T0 + timedelta(seconds=6))
    assert later.segments == () and later.held_products == () and not later.minor_likely


def test_provider_failure_degrades_and_keeps_payloads_out_of_logs(caplog) -> None:
    warm = CollectingWarm()
    c, findings, degraded = classifier(
        tagger=FakeTagger(RuntimeError("leaked model text")), warm=warm
    )
    c.observe_keyframe(keyframe(), jpeg=b"\xff\xd8..", received_at=T0)
    with caplog.at_level("ERROR"):
        warm.run()
    assert degraded == [(DegradedSignal.VLM, True)] and findings == []
    assert "leaked model text" not in caplog.text and "RuntimeError" in caplog.text
    (dogs,) = c.classify(snapshot(objects=[("dog", 1.0)]), at=T0).segments
    assert dogs.segment_id == DOGS


def test_without_a_tagger_keyframes_flag_the_vlm_as_degraded() -> None:
    c, _, degraded = classifier()
    c.observe_keyframe(keyframe(), jpeg=b"x", received_at=T0)
    assert degraded == [(DegradedSignal.VLM, True)]


def test_each_tagging_call_is_reported_with_why_items_were_dropped() -> None:
    from app.modules.audience.application.classifier import TagReport

    reports: list[TagReport] = []
    warm = CollectingWarm()
    tagger = FakeTagger(
        KeyframeTags(
            segments=(
                TaggedSegment(segment_id=DOGS, viewer_count=1, confidence=0.8),
                TaggedSegment(segment_id=SegmentId.create("999999"), viewer_count=1, confidence=1),
                TaggedSegment(segment_id=DOGS, viewer_count=0, confidence=0.5),
            ),
            held_products=(
                TaggedProduct(category="coffee", brand_ref="cafe-patio", confidence=0.9),
                TaggedProduct(category="vodka", brand_ref=None, confidence=0.9),
                TaggedProduct(category="coffee", brand_ref="no-such-brand", confidence=0.9),
            ),
            minor_likely=False,
            minor_likely_confidence=0.1,
            reasoning="a branded coffee cup",
            model_version="claude-haiku-5-5@low",
        )
    )
    c, _, _ = classifier(tagger=tagger, warm=warm, on_tags=reports.append)
    c.observe_keyframe(keyframe(), jpeg=b"\xff\xd8..", received_at=T0)
    warm.run()

    (report,) = reports
    assert report.outcome == "ok" and report.submitted_at == T0
    assert [reason for _, reason in report.segments] == [None, "unknown_segment", "no_viewers"]
    assert [reason for _, reason in report.products] == [
        None,
        "unknown_category",
        "unknown_brand",
    ]


def test_a_failed_tagging_call_is_reported_by_error_type_only() -> None:
    from app.modules.audience.application.classifier import TagReport

    reports: list[TagReport] = []
    warm = CollectingWarm()
    c, _, _ = classifier(
        tagger=FakeTagger(RuntimeError("leaked model text")), warm=warm, on_tags=reports.append
    )
    c.observe_keyframe(keyframe(), jpeg=b"\xff\xd8..", received_at=T0)
    warm.run()
    (report,) = reports
    assert (report.outcome, report.error_type, report.tags) == ("failed", "RuntimeError", None)
