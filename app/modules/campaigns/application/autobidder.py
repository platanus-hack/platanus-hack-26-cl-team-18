"""Run the auto-bidding policy for opted-in campaigns (warm, on a timer; never the hot path).

Observations come in as plain facts from the composition root (auction outcomes,
billed plays, redemptions), so campaigns never imports the auction context. Each
`tick` evaluates every enabled campaign, applies changed multipliers through the
book's idempotent seq path, and keeps the last decision for the console. A manual
slider move turns auto-bidding off for that campaign: the human always wins.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Lock

from app.modules.campaigns.application.live_book import LiveCampaignBook, MultiplierOutcome
from app.modules.campaigns.domain.autobid import (
    AutoBidDecision,
    AutoBidGoal,
    AutoBidInput,
    AutoBidPolicy,
    PerformanceWindow,
)
from app.modules.campaigns.domain.book import CampaignId
from app.modules.common.domain.value_objects.money import Money

PACING_PERIOD = timedelta(minutes=30)  # the demo's "day"; a venue setting in production
WINDOW_TICKS = 4  # evidence rolls over the last few decisions, then ages out


@dataclass(slots=True)
class _Counts:
    auctions: int = 0
    wins: int = 0
    outbid: int = 0
    zero_attention_wins: int = 0
    needed: list[Decimal] = field(default_factory=list)


@dataclass(slots=True)
class _Tracker:
    goal: AutoBidGoal
    started_at: datetime
    seen: int = 0  # every auction since enabling (cold start)
    current: _Counts = field(default_factory=_Counts)
    history: deque[_Counts] = field(default_factory=lambda: deque(maxlen=WINDOW_TICKS - 1))
    spend: Money | None = None
    redemptions: int = 0
    last: AutoBidDecision | None = None

    def window(self) -> _Counts:
        buckets = (*self.history, self.current)
        return _Counts(
            auctions=sum(b.auctions for b in buckets),
            wins=sum(b.wins for b in buckets),
            outbid=sum(b.outbid for b in buckets),
            zero_attention_wins=sum(b.zero_attention_wins for b in buckets),
            needed=[n for b in buckets for n in b.needed],
        )


class AutoBidder:
    def __init__(self, book: LiveCampaignBook, *, policy: AutoBidPolicy | None = None) -> None:
        self._book = book
        self._policy = policy or AutoBidPolicy()
        self._lock = Lock()
        self._trackers: dict[CampaignId, _Tracker] = {}

    def enable(self, campaign_id: CampaignId, goal: AutoBidGoal, *, at: datetime) -> None:
        with self._lock:
            self._trackers[campaign_id] = _Tracker(goal=goal, started_at=at)

    def disable(self, campaign_id: CampaignId) -> None:
        with self._lock:
            self._trackers.pop(campaign_id, None)

    def enabled(self, campaign_id: CampaignId) -> bool:
        with self._lock:
            return campaign_id in self._trackers

    def last_decision(self, campaign_id: CampaignId) -> AutoBidDecision | None:
        with self._lock:
            tracker = self._trackers.get(campaign_id)
            return None if tracker is None else tracker.last

    # ------------------------------------------------------------ observations

    def observe_auction(
        self,
        campaign_id: CampaignId,
        *,
        won: bool,
        outbid: bool,
        multiplier_needed: Decimal | None = None,
    ) -> None:
        with self._lock:
            tracker = self._trackers.get(campaign_id)
            if tracker is None:
                return
            tracker.seen += 1
            tracker.current.auctions += 1
            tracker.current.wins += int(won)
            tracker.current.outbid += int(outbid)
            if multiplier_needed is not None:
                tracker.current.needed.append(multiplier_needed)

    def observe_play(
        self, campaign_id: CampaignId, *, charge: Money, attention_seconds: Decimal
    ) -> None:
        with self._lock:
            tracker = self._trackers.get(campaign_id)
            if tracker is None:
                return
            tracker.spend = charge if tracker.spend is None else tracker.spend + charge
            tracker.current.zero_attention_wins += int(attention_seconds == 0)

    def observe_redemption(self, campaign_id: CampaignId) -> None:
        with self._lock:
            tracker = self._trackers.get(campaign_id)
            if tracker is not None:
                tracker.redemptions += 1

    # ------------------------------------------------------------ decisions

    def tick(self, *, at: datetime) -> dict[CampaignId, AutoBidDecision]:
        """Decide for every enabled campaign; apply changed multipliers. Returns what changed."""
        entries = {entry.bidder.campaign_id: entry for entry in self._book.entries()}
        changed: dict[CampaignId, AutoBidDecision] = {}
        with self._lock:
            trackers = list(self._trackers.items())
        for campaign_id, tracker in trackers:
            entry = entries.get(campaign_id)
            if entry is None:
                continue
            account = entry.account
            currency = account.budget.currency if account else "CLP"
            elapsed = min(Decimal(1), Decimal((at - tracker.started_at) / PACING_PERIOD))
            counts = tracker.window()
            window = PerformanceWindow(
                auctions=counts.auctions,
                seen=tracker.seen,
                wins=counts.wins,
                outbid=counts.outbid,
                zero_attention_wins=counts.zero_attention_wins,
                spend=tracker.spend or Money.zero(currency=currency),
                redemptions=tracker.redemptions,
                budget=account.budget if account else None,
                remaining=account.remaining if account else None,
                elapsed_fraction=max(elapsed, Decimal("0.01")),
                win_multiplier_needed=min(counts.needed) if counts.needed else None,
            )
            current = entry.bidder.live_multiplier
            decision = self._policy.apply(
                AutoBidInput(current=current, goal=tracker.goal, window=window)
            )
            with self._lock:
                live = self._trackers.get(campaign_id)
                if live is None:
                    continue  # disabled meanwhile (a human took the slider)
                live.last = decision
                # Roll the window: this tick's counts age out after WINDOW_TICKS decisions.
                live.history.append(live.current)
                live.current = _Counts()
                # Just above the current seq: a later human move (ms clock) always wins.
                seq = entry.multiplier_seq + 1
            if decision.changed_from(current):
                outcome = self._book.set_live_multiplier(campaign_id, decision.multiplier, seq)
                if outcome is MultiplierOutcome.APPLIED:
                    changed[campaign_id] = decision
        return changed


__all__ = ["PACING_PERIOD", "AutoBidder"]
