"""Invalid-attention filtering: every filtered second has a reason; billable looks count in full."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.delivery.domain.attention import LookSample
from app.modules.delivery.domain.validity import InvalidReason, ValidityRules, validate_attention

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def look(s: float, *ids: str, far: tuple[str, ...] = ()) -> LookSample:
    return LookSample(at=at(s), looking=frozenset(ids), far=frozenset(far))


def heartbeat(ids: tuple[str, ...], until: int, *, far: tuple[str, ...] = ()) -> list[LookSample]:
    return [look(s, *ids, far=far) for s in range(0, until, 5)]


@pytest.mark.parametrize(
    ("case", "samples", "window", "billable", "filtered"),
    [
        ("a real 8 s look", [look(0, "a")], (0, 8), "8", {}),
        ("a 0.5 s glance", [look(0, "a"), look(0.5)], (0, 8), "0", {InvalidReason.GLANCE: "0.5"}),
        (
            "far away can't read the ad",
            [look(0, "a", far=("a",))],
            (0, 8),
            "0",
            {InvalidReason.FAR: "8"},
        ),
        (
            "near counts, far doesn't",
            [look(0, "a", "b", far=("b",))],
            (0, 8),
            "8",
            {InvalidReason.FAR: "8"},
        ),
        (
            "a 4-minute unbroken look is a poster or stuck track",
            heartbeat(("poster",), 245),
            (0, 240),
            "0",
            {InvalidReason.STATIC: "240"},
        ),
        ("a 2-minute look is a real customer", heartbeat(("a",), 125), (0, 120), "120", {}),
    ],
)
def test_rules(case, samples, window, billable, filtered) -> None:  # type: ignore[no-untyped-def]
    result = validate_attention(samples, start=at(window[0]), end=at(window[1]))
    assert result.attention_seconds == Decimal(billable), case
    assert {
        k: format(v.normalize(), "f") for k, v in result.invalid_seconds.items() if v
    } == filtered, case


def test_more_lookers_than_the_screen_serves_are_capped() -> None:
    crowd = [look(0, *(f"t{i}" for i in range(15)))]
    result = validate_attention(crowd, start=at(0), end=at(2), rules=ValidityRules(max_viewers=12))
    assert result.attention_seconds == Decimal(24)  # 12 viewers × 2 s
    assert result.invalid_seconds[InvalidReason.OVER_CAPACITY] == Decimal(6)  # 3 × 2 s


def test_a_replayed_reading_is_ignored() -> None:
    samples = [look(0, "a"), look(0, "a", "ghost"), look(3, "a")]
    result = validate_attention(samples, start=at(0), end=at(4))
    assert result.duplicate_samples == 1 and result.attention_seconds == Decimal(4)


# ---------------------------------------------------------------- audit regressions


def test_a_poster_is_static_across_many_short_plays() -> None:
    """V1: a 10-minute look billed 15 s at a time is one static look: once it has
    lasted past max_static_look, every later play bills nothing (we can't know the
    future, so the first 3 minutes look like a customer)."""
    samples = heartbeat(("poster",), 600)
    for play_start in range(180, 585, 15):
        result = validate_attention(samples, start=at(play_start), end=at(play_start + 15))
        assert result.attention_seconds == 0, play_start
        assert result.invalid_seconds[InvalidReason.STATIC] == 15


def test_a_static_look_with_short_dropouts_is_still_static() -> None:
    """V2: a one-sample gap every 170 s must not reset the static clock."""
    samples = []
    for s in range(0, 600, 1):
        samples.append(look(s, *(() if s % 170 == 169 else ("poster",))))
    result = validate_attention(samples, start=at(0), end=at(600))
    assert result.attention_seconds == 0
    assert result.invalid_seconds[InvalidReason.STATIC] > 0


def test_a_customer_returning_after_a_real_break_is_not_static() -> None:
    samples = heartbeat(("a",), 120) + [look(130)] + [look(s, "a") for s in range(140, 260, 5)]
    result = validate_attention(samples, start=at(140), end=at(255))
    assert result.attention_seconds == Decimal(115)


def test_over_capacity_keeps_the_longest_standing_viewers() -> None:
    """V3: newcomers are dropped first, not whoever sorts last by id."""
    early = [look(0, *(f"z{i}" for i in range(12)))]
    crowd = early + [look(1, *(f"z{i}" for i in range(12)), "a-new", "b-new")]
    result = validate_attention(crowd, start=at(0), end=at(3), rules=ValidityRules(max_viewers=12))
    assert result.attention_seconds == Decimal(36)  # the 12 z* viewers × 3 s
    assert result.invalid_seconds[InvalidReason.OVER_CAPACITY] == Decimal(4)  # 2 × 2 s
