"""The per-screen auction desk: clear through the engine, then decide if and when the screen swaps.

Not thread-safe by itself; the caller serializes calls per screen (the demo
wiring holds its lock). Time is passed in, so tests drive it with a fake clock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from app.modules.auction.application.ports.engine import AuctionEngine
from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import AuctionResult
from app.modules.auction.domain.swap import (
    MinAdTimePolicy,
    OnAir,
    SwapAction,
    SwapInput,
    SwapTimings,
    Trigger,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeskOutcome:
    result: AuctionResult | None  # None: the engine failed; keep the last good creative
    action: SwapAction
    swap_now: bool = False
    hold_token: int | None = None  # set when the swap is held until `not_before`
    not_before: datetime | None = None


class AuctionDesk:
    def __init__(
        self,
        engine: AuctionEngine,
        *,
        timings: SwapTimings | None = None,
        holds_enabled: bool = True,
    ) -> None:
        self._engine = engine
        self._policy = MinAdTimePolicy(timings)
        self._holds_enabled = holds_enabled
        self._on_air: OnAir | None = None
        self._pending: tuple[int, AuctionResult] | None = None
        self._tokens = 0

    @property
    def on_air(self) -> OnAir | None:
        return self._on_air

    def run(self, opportunity: AdOpportunity, trigger: Trigger, *, now: datetime) -> DeskOutcome:
        on_air = self._on_air
        incumbent = None if on_air is None or on_air.is_house_ad else on_air.campaign_ids
        try:
            result = self._engine.decide(replace(opportunity, incumbent=incumbent))
        except Exception:  # the screen is never blank (R-D6): keep what plays
            logger.exception("auction engine failed; keeping the current creative")
            return DeskOutcome(result=None, action=SwapAction.UNCHANGED)
        plan = self._policy.apply(
            SwapInput(
                result=result, opportunity=opportunity, trigger=trigger, on_air=on_air, now=now
            )
        )
        if plan.action is not SwapAction.SWAP or plan.not_before is None:
            self._pending = None  # what is on air already answers this moment
            return DeskOutcome(result=result, action=plan.action)
        if not self._holds_enabled or plan.not_before <= now:
            self._pending = None
            return DeskOutcome(result=result, action=SwapAction.SWAP, swap_now=True)
        self._tokens += 1
        self._pending = (self._tokens, result)  # latest wins: supersedes any earlier hold
        return DeskOutcome(
            result=result,
            action=SwapAction.SWAP,
            hold_token=self._tokens,
            not_before=plan.not_before,
        )

    def release(self, token: int) -> AuctionResult | None:
        """The held result, if it is still the newest decision for this screen."""
        if self._pending is None or self._pending[0] != token:
            return None
        result = self._pending[1]
        self._pending = None
        return result

    def aired(self, result: AuctionResult | None, *, displayed_at: datetime) -> None:
        """Record what the screen now shows (None or a no-fill: the house ad)."""
        if result is None or not result.filled:
            self._on_air = OnAir(
                campaign_ids=frozenset(), layout_name=None, displayed_at=displayed_at
            )
            return
        self._on_air = OnAir(
            campaign_ids=result.winner_ids,
            layout_name=result.layout.name if result.layout else None,
            displayed_at=displayed_at,
            brand_ids=frozenset(winner.bidder.brand_id for winner in result.winners),
            age_restricted=any(winner.bidder.age_restricted for winner in result.winners),
        )
