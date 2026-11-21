"""Which model answered, and how hard it was asked to think.

Claude models take no sampling parameters; the only quality/cost lever per call
is the effort level. ``ModelProfile`` pairs the two, and ``describe()`` is the
``model_version`` string stored with every AI result (CLAUDE.md: AI output never
defines truth by itself).

A second opinion (re-tagging a moment, re-checking copy) is only an independent
check when its profile differs from the primary's. With one model everywhere the
difference is the effort; ``is_independent_of`` is the one place that decides it,
so a check never silently runs twice identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORT_LEVELS: tuple[Effort, ...] = get_args(Effort)

# Warm-path calls (moment tags, short copy) are simple and latency-sensitive.
DEFAULT_EFFORT: Effort = "low"
SECOND_OPINION_EFFORT: Effort = "high"


def parse_effort(value: str) -> Effort:
    """Validate an effort level read from configuration."""
    normalized = value.strip().lower()
    for level in EFFORT_LEVELS:
        if level == normalized:
            return level
    raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """One model plus the effort it is asked to spend; the identity of a call."""

    model: str
    effort: Effort = DEFAULT_EFFORT

    def is_independent_of(self, other: ModelProfile) -> bool:
        """Whether a second call with this profile is a different read."""
        same_model = self.model.strip().lower() == other.model.strip().lower()
        return not same_model or self.effort != other.effort

    def describe(self) -> str:
        """Provenance string, e.g. ``claude-haiku-5-5@low``."""
        return f"{self.model}@{self.effort}"


def second_opinion_profile(primary: ModelProfile, fallback_model: str | None) -> ModelProfile:
    """The profile for a second read: another model, else the same one at higher effort."""
    model = (fallback_model or "").strip() or primary.model
    if model.lower() != primary.model.strip().lower():
        return ModelProfile(model, primary.effort)
    return ModelProfile(model, SECOND_OPINION_EFFORT)


__all__ = [
    "DEFAULT_EFFORT",
    "EFFORT_LEVELS",
    "SECOND_OPINION_EFFORT",
    "Effort",
    "ModelProfile",
    "parse_effort",
    "second_opinion_profile",
]
