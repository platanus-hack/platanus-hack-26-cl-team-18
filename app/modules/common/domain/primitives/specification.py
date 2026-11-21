# AI-GUARD: DO NOT MODIFY
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class Specification(abc.ABC, Generic[T]):
    """Specification pattern: a reusable, composable business predicate.

    A Specification encapsulates a **single business rule** expressed as a
    boolean predicate over a candidate object. Specifications are used to
    *ask questions* about the domain, not to change it.

    ----------------------------------------------------------------------
    When to use a Specification
    ----------------------------------------------------------------------

    Use a Specification when:
    - the rule answers a yes/no question
    - the rule may be reused in multiple places
    - the rule benefits from composition (AND / OR / NOT)
    - you want to keep aggregates small and focused

    Typical examples in the auction:
    - "Campaign is eligible for this moment"
    - "Two brands are competitors" (competitor exclusion)
    - "Category is age-restricted while a minor is likely present"
    - "Campaign has budget left"
    - "Bid clears the reserve price"

    If the logic *changes state* or *produces a value*, use an Aggregate or
    a Policy instead.

    ----------------------------------------------------------------------
    What a Specification MUST be
    ----------------------------------------------------------------------

    - Pure and deterministic:
        * no I/O
        * no repositories
        * no network calls
        * no clocks / time access
    - Side-effect free (read-only)
    - Focused on a single rule or predicate

    ----------------------------------------------------------------------
    Where to use Specifications
    ----------------------------------------------------------------------

    - Inside Aggregate Roots:
        * to enforce invariants before state changes
        * to guard operations (raise DomainError if not satisfied)

    - In the application layer:
        * to validate commands before loading aggregates
        * to express complex eligibility checks in a readable way

    - In policies or domain services:
        * as building blocks for higher-level decisions

    ----------------------------------------------------------------------
    Implementing a Specification
    ----------------------------------------------------------------------

    Implement `is_satisfied_by(candidate)` with clear, explicit logic.

    Example:
        class HasBudgetLeft(Specification[Campaign]):
            def is_satisfied_by(self, campaign: Campaign) -> bool:
                return campaign.is_active and campaign.remaining_budget.amount > 0

    ----------------------------------------------------------------------
    Composing Specifications
    ----------------------------------------------------------------------

    Specifications can be composed using boolean operators:

        spec = IsPartnerAllowed() & ~IsCompetitor()
        spec = HasBudgetLeft() & ~IsAgeRestrictedForMinors()

    Composition creates new Specifications without duplicating logic.

    ----------------------------------------------------------------------
    Enforcing Specifications
    ----------------------------------------------------------------------

    Use `assert_satisfied(...)` to enforce a rule and fail fast:

        spec.assert_satisfied(
            candidate=campaign,
            error=CampaignBudgetExhausted(...)
        )

    This keeps invariants readable and declarative.

    ----------------------------------------------------------------------
    Anti-patterns to avoid
    ----------------------------------------------------------------------

    - Fetching data from repositories inside a Specification
    - Calling external services
    - Mutating the candidate object
    - Embedding large procedural logic (split into smaller specs)

    ----------------------------------------------------------------------
    Summary
    ----------------------------------------------------------------------

    Specifications let you express *business rules as objects*.
    They improve reuse, readability, and testability of domain logic,
    especially in rule-heavy decisions like the auction.

    """

    @abc.abstractmethod
    def is_satisfied_by(self, candidate: T) -> bool:
        raise NotImplementedError

    def __and__(self, other: Specification[T]) -> Specification[T]:
        return _AndSpec(self, other)

    def __or__(self, other: Specification[T]) -> Specification[T]:
        return _OrSpec(self, other)

    def __invert__(self) -> Specification[T]:
        return _NotSpec(self)

    def assert_satisfied(self, candidate: T, *, error: Exception) -> None:
        if not self.is_satisfied_by(candidate):
            raise error


@dataclass(frozen=True, slots=True)
class _AndSpec(Specification[T]):
    left: Specification[T]
    right: Specification[T]

    def is_satisfied_by(self, candidate: T) -> bool:
        return self.left.is_satisfied_by(candidate) and self.right.is_satisfied_by(candidate)


@dataclass(frozen=True, slots=True)
class _OrSpec(Specification[T]):
    left: Specification[T]
    right: Specification[T]

    def is_satisfied_by(self, candidate: T) -> bool:
        return self.left.is_satisfied_by(candidate) or self.right.is_satisfied_by(candidate)


@dataclass(frozen=True, slots=True)
class _NotSpec(Specification[T]):
    spec: Specification[T]

    def is_satisfied_by(self, candidate: T) -> bool:
        return not self.spec.is_satisfied_by(candidate)
