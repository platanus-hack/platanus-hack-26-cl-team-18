from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.audience.domain.speech_evidence import (
    SpeechEvidence,
    SpeechTopic,
    merge_speech,
    topics_from_keyterms,
)
from app.modules.audience.domain.value_objects.segment import (
    SegmentEstimate,
    SegmentId,
    SegmentSource,
)
from app.modules.common.domain.value_objects.percentage import Percentage
from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
FITNESS = SegmentId.create("DEMO_FITNESS")
COFFEE = SegmentId.create("DEMO_COFFEE")
CATALOG = {"gym": FITNESS, "café": COFFEE, "hambre": COFFEE}


def at(seconds: float) -> UtcTimestamp:
    return UtcTimestamp.create(T0 + timedelta(seconds=seconds))


def vision(segment: SegmentId, confidence: int = 70) -> SegmentEstimate:
    return SegmentEstimate.create(
        segment_id=segment,
        viewer_count=1,
        confidence=Percentage.create(confidence),
        sources=frozenset({SegmentSource.VISION}),
    )


def heard(*terms: str, when: float = 0, ttl: float = 60) -> SpeechEvidence:
    return SpeechEvidence().heard(
        topics_from_keyterms(terms, CATALOG, heard_at=at(when), ttl=timedelta(seconds=ttl))
    )


def test_keyterms_map_to_segments_with_a_ttl_and_dedupe_per_segment() -> None:
    topics = topics_from_keyterms(["hambre", "café", "unknown"], CATALOG, heard_at=at(0))
    assert [t.segment_id for t in topics] == [COFFEE]
    assert topics[0].expires_at.value == T0 + timedelta(seconds=60)
    assert topics_from_keyterms(["gym"], None, heard_at=at(0)) == ()


def test_speech_adds_a_segment_the_camera_did_not_see() -> None:
    merged = merge_speech((vision(FITNESS),), heard("café"), at=at(1), looking=1)
    coffee = next(s for s in merged if s.segment_id == COFFEE)
    assert coffee.sources == frozenset({SegmentSource.SPEECH})
    assert coffee.viewer_count == 1
    assert coffee.speech_intent == Percentage.create(90)


def test_speech_boosts_a_visual_segment_and_keeps_its_viewers() -> None:
    merged = merge_speech((vision(FITNESS, 40),), heard("gym"), at=at(1), looking=1)
    (fitness,) = merged
    assert fitness.sources == frozenset({SegmentSource.VISION, SegmentSource.SPEECH})
    assert fitness.confidence == Percentage.create(90)


def test_expired_speech_reverts_to_visual_evidence_or_disappears() -> None:
    evidence = heard("gym", "café", ttl=10)
    merged = merge_speech((vision(FITNESS, 40),), evidence, at=at(11), looking=1)
    assert merged == (vision(FITNESS, 40),)


def test_speech_never_invents_viewers_in_an_empty_hall() -> None:
    assert merge_speech((), heard("café"), at=at(1), looking=0) == ()


def test_repeating_a_keyterm_refreshes_its_ttl() -> None:
    evidence = heard("café", when=0, ttl=10).heard(
        topics_from_keyterms(["café"], CATALOG, heard_at=at(8), ttl=timedelta(seconds=10))
    )
    assert evidence.topics[0].expires_at == at(18)
    assert evidence.next_expiry() == at(18)


def test_topic_must_expire_after_it_was_heard() -> None:
    with pytest.raises(ValueError):
        SpeechTopic(
            segment_id=COFFEE, intent=Percentage.create(50), heard_at=at(5), expires_at=at(5)
        )


def test_a_new_single_viewer_request_replaces_the_previous_spoken_interest() -> None:
    evidence = heard("gym", when=0).heard(
        topics_from_keyterms(["café"], CATALOG, heard_at=at(1)), replace_previous=True
    )
    assert [topic.segment_id for topic in evidence.topics] == [COFFEE]


def test_one_request_can_still_contain_multiple_interests() -> None:
    evidence = heard("gym", when=0).heard(
        topics_from_keyterms(["gym", "café"], CATALOG, heard_at=at(1)), replace_previous=True
    )
    assert {topic.segment_id for topic in evidence.topics} == {FITNESS, COFFEE}


def test_an_older_delayed_request_cannot_replace_the_newest_request() -> None:
    current = heard("café", when=5)
    assert (
        current.heard(topics_from_keyterms(["gym"], CATALOG, heard_at=at(1)), replace_previous=True)
        == current
    )


def test_groups_keep_concurrent_interests_and_visual_evidence_is_preserved() -> None:
    evidence = heard("gym", when=0).heard(topics_from_keyterms(["café"], CATALOG, heard_at=at(1)))
    assert {topic.segment_id for topic in evidence.topics} == {FITNESS, COFFEE}
    latest = evidence.heard(
        topics_from_keyterms(["café"], CATALOG, heard_at=at(2)), replace_previous=True
    )
    assert {
        s.segment_id for s in merge_speech((vision(FITNESS),), latest, at=at(2), looking=1)
    } == {FITNESS, COFFEE}
