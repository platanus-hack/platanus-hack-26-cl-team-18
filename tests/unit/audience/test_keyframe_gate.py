"""The VLM is called as a tie-breaker, not on every keyframe."""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.audience.domain.policies.keyframe_gate import KeyframeGate

NOW = datetime(2026, 11, 21, 15, tzinfo=UTC)
GATE = KeyframeGate()


@pytest.mark.parametrize(
    ("looking", "objects", "understood", "last", "reason"),
    [
        # Nobody looking: no tie-breaking (nobody to sell to), but the safety refresh runs.
        (0, frozenset({"bottle"}), False, NOW - timedelta(seconds=1), None),
        (0, frozenset({"bottle"}), False, None, "refresh"),
        (3, frozenset({"bottle"}), True, NOW, "ambiguous_object"),  # beer or energy?
        (2, frozenset({"cup"}), True, NOW, "ambiguous_object"),  # coffee or a beer glass?
        (4, frozenset(), False, NOW, "nothing_understood"),  # a queue
        (2, frozenset({"sports ball"}), True, NOW - timedelta(seconds=2), None),  # clear
        (2, frozenset({"sports ball"}), True, NOW - timedelta(seconds=5), "refresh"),
        (1, frozenset(), True, None, "refresh"),  # never tagged: the first one always
    ],
)
def test_keyframe_gate(looking, objects, understood, last, reason) -> None:  # type: ignore[no-untyped-def]
    assert (
        GATE.should_tag(
            looking=looking, objects=objects, understood=understood, last_tagged_at=last, now=NOW
        )
        == reason
    )


def test_the_refresh_keeps_vlm_evidence_alive() -> None:
    """Refresh below the VLM TTL (6 s): tagged evidence and minor safety never lapse."""
    from app.modules.audience.application.classifier import EvidenceTtl

    assert GATE.refresh < EvidenceTtl().vlm
