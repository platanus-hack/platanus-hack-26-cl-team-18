"""The offer follows the group, not every glance."""

from datetime import UTC, datetime, timedelta

from app.modules.creative.domain.party import LookingSample, addressed_party

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def at(seconds: float, looking: int, people: int = 5) -> LookingSample:
    return LookingSample(T0 + timedelta(seconds=seconds), looking, people)


def test_a_new_group_gets_its_offer_on_the_first_frame() -> None:
    assert addressed_party([at(0, 2)], now=T0) == 2


def test_a_third_person_walking_up_upsizes_the_offer_at_once() -> None:
    """Demo beat 2: the third friend arrives (more people present): Trío now."""
    arrival = [at(0, 2, people=2), at(0.4, 2, people=2), at(1.0, 3, people=3)]
    assert addressed_party(arrival, now=T0 + timedelta(seconds=1.0)) == 3


def test_someone_already_there_glancing_in_needs_a_second_sample() -> None:
    glance = [at(0, 2, people=3), at(0.4, 2, people=3), at(1.0, 3, people=3)]
    assert addressed_party(glance, now=T0 + timedelta(seconds=1.0)) == 2
    stays = [*glance, at(1.6, 3, people=3)]
    assert addressed_party(stays, now=T0 + timedelta(seconds=1.6)) == 3


def test_one_frame_glances_never_resize_the_offer() -> None:
    # The house-party clip: 3 people steady, a fourth glancing in for single frames.
    party = [at(3.2, 3), at(3.8, 4), at(4.4, 3), at(5.4, 3), at(6.2, 2), at(7.2, 3), at(8.8, 4)]
    for sample in party:
        assert addressed_party(party, now=sample.measured_at) in (2, 3), sample
    assert addressed_party(party, now=T0 + timedelta(seconds=6.2)) == 3  # a glance away too


def test_a_smaller_group_takes_over_after_the_whole_window() -> None:
    steady = [at(0, 3), at(0.5, 3), at(1, 2), at(2, 2), at(3, 2), at(4.1, 2)]
    assert addressed_party(steady, now=T0 + timedelta(seconds=2.9)) == 3
    assert addressed_party(steady, now=T0 + timedelta(seconds=4.1)) == 2


def test_no_recent_sample_addresses_nobody() -> None:
    assert addressed_party([at(0, 3)], now=T0 + timedelta(seconds=10)) == 0
