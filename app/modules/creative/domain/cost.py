"""Generation cost: estimates, a spend ledger, an hourly cap and the ROI rule.

Provider costs are sub-cent (a Haiku copy call is ~$0.0002), which `Money`
would round to zero, so they are integer **micro-dollars** (`UsdCost`). They are
an operating cost, never a price or a charge; brand money stays in `Money`.

`plan_upgrades` is the cost-aware decision: spend on a warm upgrade only when
its estimate fits the hourly budget and is worth it for this moment (a fraction
of the moment's expected revenue), with a floor under which an upgrade is
always worth trying (copy, voice). Pure: time comes in as `at`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Self

from app.modules.common.domain.primitives.value_object import ValueObject

MICROS = Decimal(1_000_000)


@dataclass(frozen=True, slots=True, order=True)
class UsdCost(ValueObject):
    micros: int

    def __post_init__(self) -> None:
        self._enforce_types()

    def _enforce_types(self) -> None:
        if type(self.micros) is not int or self.micros < 0:
            raise ValueError("UsdCost.micros must be a non-negative int")

    @classmethod
    def create(cls, dollars: Decimal | int | str) -> Self:
        if isinstance(dollars, bool | float):
            raise TypeError("UsdCost does not accept bool or float")
        value = (Decimal(dollars) * MICROS).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        return cls(micros=int(value))

    @classmethod
    def zero(cls) -> Self:
        return cls(micros=0)

    @property
    def dollars(self) -> Decimal:
        return Decimal(self.micros) / MICROS

    def __add__(self, other: UsdCost) -> UsdCost:
        return UsdCost(micros=self.micros + other.micros)

    def scaled(self, factor: Decimal) -> UsdCost:
        return UsdCost.create(self.dollars * factor)

    def __str__(self) -> str:
        return f"${self.dollars.quantize(Decimal('0.000001'))}"


class GenerationKind(StrEnum):
    COPY = "copy"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"


@dataclass(frozen=True, slots=True, kw_only=True)
class CostModel:
    """Unit prices (configurable; defaults are list prices or estimates, Nov 2026)."""

    llm_input_per_mtok: UsdCost = UsdCost(micros=100_000)  # Claude Haiku 5.5: $0.10 / MTok
    llm_output_per_mtok: UsdCost = UsdCost(micros=500_000)  # $0.50 / MTok
    tts_per_1k_chars: UsdCost = UsdCost(micros=50_000)  # ElevenLabs Flash (estimate)
    image_per_call: UsdCost = UsdCost(micros=30_000)  # fal FLUX-class (estimate)
    video_per_second: UsdCost = UsdCost(micros=60_000)  # fal turbo i2v (estimate)

    def copy(self, *, input_tokens: int, output_tokens: int) -> UsdCost:
        return UsdCost.create(
            self.llm_input_per_mtok.dollars * Decimal(input_tokens) / MICROS
            + self.llm_output_per_mtok.dollars * Decimal(output_tokens) / MICROS
        )

    def voice(self, characters: int) -> UsdCost:
        return UsdCost.create(self.tts_per_1k_chars.dollars * Decimal(characters) / 1000)

    def image(self) -> UsdCost:
        return self.image_per_call

    def video(self, seconds: int) -> UsdCost:
        return UsdCost.create(self.video_per_second.dollars * seconds)


def estimate_tokens(text: str) -> int:
    """Rough token count for budgeting (≈ 4 chars per token for Spanish/JSON)."""
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True, slots=True, kw_only=True)
class Spend:
    kind: GenerationKind
    cost: UsdCost
    at: datetime


@dataclass
class GenerationLedger:
    """In-memory record of what generation cost (the console and the eval read it)."""

    entries: list[Spend] = field(default_factory=list)

    def record(self, kind: GenerationKind, cost: UsdCost, *, at: datetime) -> None:
        self.entries.append(Spend(kind=kind, cost=cost, at=at))

    def total(self) -> UsdCost:
        return sum((e.cost for e in self.entries), UsdCost.zero())

    def spent_since(self, since: datetime) -> UsdCost:
        return sum((e.cost for e in self.entries if e.at >= since), UsdCost.zero())

    def by_kind(self) -> dict[GenerationKind, UsdCost]:
        out: dict[GenerationKind, UsdCost] = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, UsdCost.zero()) + e.cost
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationBudget:
    hourly_cap: UsdCost = UsdCost(micros=2_000_000)  # $2/h per screen
    roi_fraction: Decimal = Decimal("0.25")  # spend at most 25 % of the moment's expected revenue
    always_ok_under: UsdCost = UsdCost(micros=5_000)  # copy and voice are always worth trying
    clp_per_usd: Decimal = Decimal(950)


class DenialReason(StrEnum):
    HOURLY_CAP = "hourly_cap"
    NOT_WORTH_IT = "not_worth_it"


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeDecision:
    kind: GenerationKind
    estimate: UsdCost
    approved: bool
    reason: DenialReason | None = None


def expected_revenue_usd(
    *, value_per_s_clp: Decimal, expected_dwell_s: Decimal, budget: GenerationBudget
) -> Decimal:
    return value_per_s_clp * expected_dwell_s / budget.clp_per_usd


def plan_upgrades(
    candidates: Iterable[tuple[GenerationKind, UsdCost]],
    *,
    expected_revenue: Decimal,
    ledger: GenerationLedger,
    budget: GenerationBudget,
    at: datetime,
) -> tuple[UpgradeDecision, ...]:
    """Approve candidates in order while they fit the cap and are worth it."""
    committed = ledger.spent_since(at - timedelta(hours=1))
    out: list[UpgradeDecision] = []
    for kind, estimate in candidates:
        if (committed + estimate).micros > budget.hourly_cap.micros:
            out.append(
                UpgradeDecision(
                    kind=kind, estimate=estimate, approved=False, reason=DenialReason.HOURLY_CAP
                )
            )
            continue
        worth = estimate.dollars <= budget.roi_fraction * expected_revenue
        if not worth and estimate.micros > budget.always_ok_under.micros:
            out.append(
                UpgradeDecision(
                    kind=kind, estimate=estimate, approved=False, reason=DenialReason.NOT_WORTH_IT
                )
            )
            continue
        committed = committed + estimate
        out.append(UpgradeDecision(kind=kind, estimate=estimate, approved=True))
    return tuple(out)
