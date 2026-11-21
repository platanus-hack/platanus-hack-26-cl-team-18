from decimal import Decimal

import pytest

from app.modules.audience.domain.policies.fusion import FusionInput, SegmentFusionPolicy
from app.modules.audience.domain.value_objects.segment import SegmentSource
from tests.unit.audience.factories import COFFEE, FITNESS, SNACKS, at, evidence

VISION, SOUND, SPEECH, VLM = (
    SegmentSource.VISION,
    SegmentSource.SOUND,
    SegmentSource.SPEECH,
    SegmentSource.VLM,
)


def fuse(*items, looking=3, now=0.0, previously=frozenset()):
    return SegmentFusionPolicy().apply(
        FusionInput(
            evidence=tuple(items), looking=looking, now=at(now), previously_biddable=previously
        )
    )


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        # One source: its confidence as is.
        ((evidence(COFFEE, VISION, 60),), Decimal(60)),
        # Same source twice (one frame): the strongest claim, not a sum.
        ((evidence(COFFEE, VISION, 60), evidence(COFFEE, VISION, 55)), Decimal(60)),
        # Independent sources: noisy-OR, 1 - 0.4 * 0.5 = 0.8.
        ((evidence(COFFEE, VISION, 60), evidence(COFFEE, VLM, 50)), Decimal(80)),
        # Three sources compound: 1 - 0.5 * 0.5 * 0.5 = 0.875.
        (
            (evidence(COFFEE, VISION, 50), evidence(COFFEE, VLM, 50), evidence(COFFEE, SOUND, 50)),
            Decimal("87.5"),
        ),
    ],
)
def test_noisy_or_across_sources_and_max_within_a_source(items, expected) -> None:
    (estimate,) = fuse(*items).biddable
    assert estimate.confidence.value == expected


def test_sources_are_reported_per_segment() -> None:
    result = fuse(
        evidence(COFFEE, VISION, 70), evidence(COFFEE, VLM, 70), evidence(FITNESS, VLM, 70)
    )
    by_id = {estimate.segment_id: estimate for estimate in result.biddable}
    assert by_id[COFFEE].sources == {VISION, VLM}
    assert by_id[FITNESS].sources == {VLM}


def test_viewer_count_is_the_largest_claim_capped_by_looking() -> None:
    (estimate,) = fuse(evidence(COFFEE, VISION, 90, viewers=5), looking=2).biddable
    assert estimate.viewer_count == 2


def test_one_talker_counts_as_one_viewer() -> None:
    (estimate,) = fuse(evidence(SNACKS, SPEECH, 90, viewers=4, ttl=60, intent=90)).biddable
    assert estimate.viewer_count == 1
    assert estimate.speech_intent is not None and estimate.speech_intent.value == 90


def test_nobody_looking_means_no_segments() -> None:
    result = fuse(evidence(COFFEE, VISION, 90), looking=0)
    assert result.biddable == () and result.below_threshold == ()


def test_hysteresis_keeps_a_biddable_segment_until_it_drops_below_exit() -> None:
    weak = evidence(COFFEE, VISION, 45)
    assert fuse(weak).biddable == ()
    assert [e.segment_id for e in fuse(weak).below_threshold] == [COFFEE]
    assert [e.segment_id for e in fuse(weak, previously=frozenset({COFFEE})).biddable] == [COFFEE]
    assert fuse(evidence(COFFEE, VISION, 35), previously=frozenset({COFFEE})).biddable == ()


def test_speech_fades_linearly_and_expired_evidence_is_ignored() -> None:
    spoken = evidence(SNACKS, SPEECH, 80, ttl=60, intent=80)
    (fresh,) = fuse(spoken, now=0).biddable
    (half,) = fuse(spoken, now=30).below_threshold
    assert fresh.confidence.value == 80
    assert half.confidence.value == 40
    assert fuse(spoken, now=60).biddable == fuse(spoken, now=60).below_threshold == ()
    assert fuse(evidence(COFFEE, VISION, 90, ttl=3), now=3).biddable == ()
