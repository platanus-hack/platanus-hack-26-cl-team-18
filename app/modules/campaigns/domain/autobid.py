"""Auto-bidding: an explainable rule system that moves a campaign's live multiplier.

Not a black box: rules run in a fixed order, the first that applies decides, and
every decision carries the rule and a sentence the advertiser sees. Each move is at
most `max_step` (multiplicative) and stays within the bid multiplier bounds, so the
auction stays truthful-ish and a runaway can't happen. Pure: the caller passes the
performance window and the current multiplier.

Order (safety first, then money, then opportunity):
1. BUDGET_FLOOR   less than 5 % of budget left: drop to the minimum, stop spending.
2. PACE_DOWN      spending ahead of plan: lower.
3. COLD_START     too few auctions observed: hold, don't guess.
4. WASTED_WINS    half the plays won drew no attention: lower (we pay ~0, but the
                  slot could serve someone who converts).
5. OVER_TARGET    cost per redemption above target: lower.
6. UNDER_TARGET   cost per redemption well below target and pacing behind: raise.
7. CHASE_OUTBID   repeatedly outbid while behind plan: raise toward what it takes to win.
8. HOLD           otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from app.modules.campaigns.domain.book import BidMultiplier
from app.modules.common.domain.primitives.policy import Policy
from app.modules.common.domain.value_objects.money import Money

ONE = Decimal(1)
_STEP_Q = Decimal("0.01")


class AutoBidRule(StrEnum):
    BUDGET_FLOOR = "budget_floor"
    PACE_DOWN = "pace_down"
    COLD_START = "cold_start"
    WASTED_WINS = "wasted_wins"
    OVER_TARGET = "over_target"
    UNDER_TARGET = "under_target"
    CHASE_OUTBID = "chase_outbid"
    HOLD = "hold"


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBidGoal:
    """What the advertiser asked for: a target cost per redemption (optional)."""

    target_cost_per_redemption: Money | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PerformanceWindow:
    auctions: int  # auctions it took part in within the rolling window
    seen: int | None = None  # auctions since auto-bidding started (cold start); None → auctions
    wins: int
    outbid: int  # lost on value (outbid / competitor) while eligible
    zero_attention_wins: int  # billed plays (reports) with no qualifying attention
    plays: int = 0  # billed plays (reports), the denominator for wasted wins
    spend: Money  # since auto-bidding started
    redemptions: int  # since auto-bidding started
    budget: Money | None  # None: uncapped
    remaining: Money | None
    elapsed_fraction: Decimal  # 0..1 of the pacing period
    win_multiplier_needed: Decimal | None = None  # rival value ÷ own value, when outbid


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBidInput:
    current: BidMultiplier
    goal: AutoBidGoal
    window: PerformanceWindow


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBidDecision:
    multiplier: BidMultiplier
    rule: AutoBidRule
    reason: str

    def changed_from(self, current: BidMultiplier) -> bool:
        return self.multiplier.value != current.value


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBidConfig:
    max_step: Decimal = Decimal("0.20")  # ±20 % per decision
    min_auctions: int = 3
    pace_tolerance: Decimal = Decimal("1.25")  # spent fraction > elapsed × 1.25 + 5 pp
    floor_fraction: Decimal = Decimal("0.05")
    target_band: tuple[Decimal, Decimal] = (Decimal("0.8"), Decimal("1.2"))


class AutoBidPolicy(Policy[AutoBidInput, AutoBidDecision]):
    def __init__(self, config: AutoBidConfig | None = None) -> None:
        self.config = config or AutoBidConfig()

    def apply(self, input: AutoBidInput) -> AutoBidDecision:
        c, w, now = self.config, input.window, input.current.value

        def to(value: Decimal, rule: AutoBidRule, reason: str) -> AutoBidDecision:
            low, high = now * (ONE - c.max_step), now * (ONE + c.max_step)
            bounded = min(max(value, low, BidMultiplier.MIN), high, BidMultiplier.MAX)
            if rule is AutoBidRule.BUDGET_FLOOR:
                bounded = BidMultiplier.MIN  # safety overrides the step limit
            return AutoBidDecision(
                multiplier=BidMultiplier.create(
                    bounded.quantize(_STEP_Q, rounding=ROUND_HALF_EVEN)
                ),
                rule=rule,
                reason=reason,
            )

        spent = self._spent_fraction(w)
        if w.remaining is not None and w.budget is not None and w.budget.amount > 0:
            if w.remaining.amount <= w.budget.amount * c.floor_fraction:
                return to(
                    BidMultiplier.MIN, AutoBidRule.BUDGET_FLOOR, "Budget nearly spent: minimum bid."
                )
        if spent is not None and spent > w.elapsed_fraction * c.pace_tolerance + Decimal("0.05"):
            return to(
                now * (ONE - c.max_step),
                AutoBidRule.PACE_DOWN,
                f"Spending ahead of plan ({_pct(spent)} spent, {_pct(w.elapsed_fraction)} of the period gone).",
            )
        seen = w.auctions if w.seen is None else w.seen  # lifetime, not the rolling window
        if seen < c.min_auctions:
            return to(
                now,
                AutoBidRule.COLD_START,
                f"Learning: {seen}/{c.min_auctions} auctions seen.",
            )
        if w.plays >= 2 and w.zero_attention_wins * 2 >= w.plays:
            return to(
                now * (ONE - c.max_step / 2),
                AutoBidRule.WASTED_WINS,
                f"{w.zero_attention_wins} of {w.plays} plays drew no attention: easing off.",
            )
        target = input.goal.target_cost_per_redemption
        behind = spent is None or spent < w.elapsed_fraction
        if target is not None and w.redemptions > 0:
            cpr = w.spend.amount / w.redemptions
            low, high = c.target_band
            if cpr == 0 and behind:  # redeemed while paying nothing: beats any target
                return to(
                    now * (ONE + c.max_step),
                    AutoBidRule.UNDER_TARGET,
                    "Redemptions at $0 cost: bidding up.",
                )
            if cpr > target.amount * high:
                return to(
                    now * target.amount / cpr,
                    AutoBidRule.OVER_TARGET,
                    f"Cost per redemption ${cpr:.0f} is above the ${target.amount:.0f} target.",
                )
            if 0 < cpr < target.amount * low and behind:
                return to(
                    now * target.amount / cpr,
                    AutoBidRule.UNDER_TARGET,
                    f"Cost per redemption ${cpr:.0f} beats the ${target.amount:.0f} target: bidding up.",
                )
        if w.outbid >= 2 and behind:
            needed = (w.win_multiplier_needed or ONE + c.max_step) * Decimal("1.05")
            return to(
                now * needed,
                AutoBidRule.CHASE_OUTBID,
                f"Outbid {w.outbid}× while behind plan: raising toward ×{(now * needed):.2f}.",
            )
        return to(now, AutoBidRule.HOLD, "On plan: holding.")

    @staticmethod
    def _spent_fraction(w: PerformanceWindow) -> Decimal | None:
        if w.budget is None or w.budget.amount <= 0:
            return None
        return w.spend.amount / w.budget.amount


def _pct(value: Decimal) -> str:
    return f"{(value * 100):.0f}%"
