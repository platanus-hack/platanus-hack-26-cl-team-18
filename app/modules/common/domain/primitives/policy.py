# AI-GUARD: DO NOT MODIFY
from __future__ import annotations

import abc
from typing import ClassVar, Generic, TypeVar

I = TypeVar("I")
O = TypeVar("O")


class Policy(abc.ABC, Generic[I, O]):
    """Policy pattern: an explicit, replaceable business decision.

    A Policy captures **business rules that may vary** while keeping the
    surrounding domain model stable. The domain objects (Campaign, Screen,
    AudienceMoment, etc.) should not be littered with venue- or brand-specific
    branching logic.

    ----------------------------------------------------------------------
    When to use a Policy
    ----------------------------------------------------------------------

    Use a Policy when:
    - the domain needs a decision (or transformation) whose *interface is stable*
    - but the rule can vary by:
        * entity configuration
        * venue / screen configuration
        * product tier / feature flags
        * time/versioned business rules

    Examples:
    - rounding policy (HALF_UP vs HALF_EVEN; currency decimals)
    - reserve price policy per screen / daypart
    - moment debounce policy (when a change is worth a re-auction)
    - attention-second measurement policy
    - layout selection policy for a winning bundle

    ----------------------------------------------------------------------
    What a Policy MUST be
    ----------------------------------------------------------------------

    - Pure and deterministic:
        * no I/O
        * no repositories
        * no network calls
        * no clocks / "now()"
    - Testable in isolation (given the same input, always returns the same output)
    - Explicit about inputs and outputs (avoid hidden global state)

    ----------------------------------------------------------------------
    Where to execute Policies
    ----------------------------------------------------------------------

    - Application layer (recommended):
        * Application services assemble the required input (including config)
        * Inject/select the appropriate Policy implementation
        * Call policy.apply(...)
        * Pass the resulting values into Aggregate methods

    - Domain layer (allowed, but keep pure):
        * Aggregates can call a Policy if the policy is already constructed
          and passed in as an argument (no fetching inside domain).
        * Prefer to pass in the policy output rather than the policy object
          if you want maximum decoupling.

    ----------------------------------------------------------------------
    Implementing a Policy
    ----------------------------------------------------------------------

    1) Define the input/output types (often small dataclasses).
    2) Implement apply(...) with clear, deterministic logic.
    3) Version your policy when behavior changes in a backward-incompatible way.

    Example: Rounding policy

        @dataclass(frozen=True, slots=True)
        class RoundMoneyInput:
            amount: Decimal
            currency: CurrencyCode

        class RoundMoneyPolicy(Policy[RoundMoneyInput, Decimal]):
            name = "money.rounding"
            version = 1

            def apply(self, input: RoundMoneyInput) -> Decimal:
                decimals = CURRENCY_DECIMALS[input.currency]
                return input.amount.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_EVEN)

    ----------------------------------------------------------------------
    Versioning & evolution
    ----------------------------------------------------------------------

    - `name` identifies the policy family ("auction.reserve", "money.rounding").
    - `version` is incremented when behavior changes in a way that matters for:
        * auditability
        * reprocessing
        * reproducible charges

    If policy changes can affect charged amounts, store the policy version
    (or a snapshot of config) along with the resulting charges or events,
    so you can explain historical outcomes.

    ----------------------------------------------------------------------
    Anti-patterns to avoid
    ----------------------------------------------------------------------

    - Calling external services inside apply(...)
    - Reading configuration from globals/environment at runtime
    - Using current time implicitly (non-deterministic decisions)
    - Returning ambiguous values (prefer explicit result objects)

    ----------------------------------------------------------------------
    Summary
    ----------------------------------------------------------------------

    Policies keep your domain model clean:
    - aggregates enforce invariants and emit events
    - policies encode variable business rules
    - the application layer orchestrates which policy to use

    """

    name: ClassVar[str] = "policy"
    version: ClassVar[int] = 1

    @abc.abstractmethod
    def apply(self, input: I) -> O:
        raise NotImplementedError
