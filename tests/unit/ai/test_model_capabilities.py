import pytest

from app.modules.ai.domain.model_capabilities import (
    ModelProfile,
    parse_effort,
    second_opinion_profile,
)


def test_default_profile_is_low_effort_and_describes_itself() -> None:
    assert ModelProfile("claude-haiku-5-5").describe() == "claude-haiku-5-5@low"


def test_second_opinion_on_the_same_model_raises_effort() -> None:
    primary = ModelProfile("claude-haiku-5-5")
    second = second_opinion_profile(primary, "claude-haiku-5-5")
    assert second == ModelProfile("claude-haiku-5-5", "high")
    assert second.is_independent_of(primary)
    assert second_opinion_profile(primary, None).is_independent_of(primary)


def test_second_opinion_on_another_model_keeps_effort() -> None:
    primary = ModelProfile("claude-haiku-5-5")
    second = second_opinion_profile(primary, "claude-sonnet-5-5")
    assert second == ModelProfile("claude-sonnet-5-5", "low")
    assert second.is_independent_of(primary)


def test_identical_profiles_are_not_independent() -> None:
    assert not ModelProfile("claude-haiku-5-5").is_independent_of(ModelProfile("Claude-Haiku-5-5"))


def test_parse_effort_accepts_known_levels_only() -> None:
    assert parse_effort(" Medium ") == "medium"
    with pytest.raises(ValueError):
        parse_effort("turbo")
