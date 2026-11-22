"""The per-screen auction desk: clear through the engine, then decide if and when the screen swaps.

Not thread-safe by itself; the caller serializes calls per screen (the demo
wiring holds its lock). Time is passed in, so tests drive it with a fake clock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.modules.auction.application.ports.engine import AuctionEngine
from app.modules.auction.domain.opportunity import AdOpportunity
from app.modules.auction.domain.result import AuctionResult, NoFillReason
from app.modules.auction.domain.swap import (
    SAME_MOMENT_TRIGGERS,
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
        self._pending: tuple[int, AuctionResult, Trigger] | None = None
        self._tokens = 0

    @property
    def on_air(self) -> OnAir | None:
        return self._on_air

    def run(
        self,
        opportunity: AdOpportunity,
        trigger: Trigger,
        *,
        now: datetime,
        needs_creative_refresh: Callable[[AuctionResult], bool] | None = None,
    ) -> DeskOutcome:
        on_air = self._on_air
        incumbent = None if on_air is None or on_air.is_house_ad else on_air.campaign_ids
        # A bid or stock change during a new group's hold is still that new group's
        # decision: keep its 1.5 s floor and no hysteresis (D5.3, D8).
        if trigger in SAME_MOMENT_TRIGGERS and self._pending is not None:
            if self._pending[2] is Trigger.MOMENT_CHANGED:
                trigger = Trigger.MOMENT_CHANGED
        try:
            result = self._engine.decide(replace(opportunity, incumbent=incumbent))
        except Exception:  # the screen is never blank (R-D6)
            logger.exception("auction engine failed")
            # Nothing decided may reach the screen: a held swap was cleared against
            # older facts (e.g. before a minor arrived).
            self._pending = None
            if self._policy.unsafe_on_air(on_air, opportunity):
                # Safety never depends on the engine: take an unsafe ad down now.
                fallback = AuctionResult.house_ad(
                    currency=opportunity.reserve, reason=NoFillReason.ENGINE_ERROR
                )
                return DeskOutcome(result=fallback, action=SwapAction.SWAP, swap_now=True)
            return DeskOutcome(result=None, action=SwapAction.UNCHANGED)
        creative_changed = (
            needs_creative_refresh is not None
            and on_air is not None
            and result.filled
            and result.winner_ids == on_air.campaign_ids
            and result.layout is not None
            and result.layout.name == on_air.layout_name
            and needs_creative_refresh(result)
        )
        plan = self._policy.apply(
            SwapInput(
                result=result,
                opportunity=opportunity,
                trigger=trigger,
                on_air=on_air,
                now=now,
                creative_changed=creative_changed,
            )
        )
        if plan.action is not SwapAction.SWAP or plan.not_before is None:
            self._pending = None  # what is on air already answers this moment
            return DeskOutcome(result=result, action=plan.action)
        if not self._holds_enabled or plan.not_before <= now:
            self._pending = None
            return DeskOutcome(result=result, action=SwapAction.SWAP, swap_now=True)
        self._tokens += 1
        self._pending = (self._tokens, result, trigger)  # latest wins: supersedes earlier holds
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

    def displayed(self, at: datetime) -> None:
        """The player confirmed the on-air creative is visible: holds count from here."""
        if self._on_air is not None and at > self._on_air.displayed_at:
            self._on_air = replace(self._on_air, displayed_at=at)

    def clock_stepped(self, delta: timedelta) -> None:
        """The wall clock jumped by `delta` (NTP step): the on-air play keeps its age.

        Without this a backward step leaves `displayed_at` in the future, so the
        minimum ad time would hold every new moment for the size of the step.
        """
        if self._on_air is not None:
            self._on_air = replace(self._on_air, displayed_at=self._on_air.displayed_at + delta)

    @property
    def hold_pending(self) -> bool:
        return self._pending is not None

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
