"""The in-memory campaign book the auction reads on the hot path (R-B4, R-B5, R-B7).

Thread-safe: HTTP handlers (bidding war), the router thread (auctions) and
delivery (charges) all touch it. Postgres is the record, not the hot read.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Any

from app.modules.campaigns.domain.book import Bidder, BidMultiplier, CampaignId
from app.modules.campaigns.domain.budget import BudgetAccount
from app.modules.common.domain.primitives.errors import DomainError
from app.modules.common.domain.value_objects.money import Money

# A play that never reports back (player closed) frees its hold after this long.
RESERVATION_TTL = timedelta(minutes=2)


class UnknownCampaign(DomainError):
    def __init__(self, **_: Any) -> None:
        super().__init__(code="campaigns.unknown_campaign", msg="No such campaign in the book.")


class ConflictingMultiplierSeq(DomainError):
    def __init__(self, **_: Any) -> None:
        super().__init__(
            code="campaigns.conflicting_multiplier_seq",
            msg="This seq was already used with a different multiplier.",
        )


class MultiplierOutcome(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"  # same seq, same value: idempotent retry
    STALE = "stale"  # an older seq arrived late: ignored


@dataclass(frozen=True, slots=True, kw_only=True)
class BookEntry:
    bidder: Bidder
    account: BudgetAccount | None  # None: uncapped
    multiplier_seq: int


class LiveCampaignBook:
    def __init__(self, bidders: Iterable[Bidder]) -> None:
        self._lock = Lock()
        self._entries: dict[CampaignId, BookEntry] = {}
        self._seen: dict[CampaignId, dict[int, BidMultiplier]] = {}
        self.version = 0
        for bidder in bidders:
            account = (
                None
                if bidder.budget_remaining is None
                else BudgetAccount.open(bidder.campaign_id, bidder.budget_remaining)
            )
            self._entries[bidder.campaign_id] = BookEntry(
                bidder=bidder, account=account, multiplier_seq=0
            )

    def snapshot(self) -> tuple[Bidder, ...]:
        """Immutable bidders for one auction: live multiplier and budget left applied."""
        with self._lock:
            return tuple(self._bidder(entry) for entry in self._entries.values())

    def entries(self) -> tuple[BookEntry, ...]:
        with self._lock:
            return tuple(self._entries.values())

    def set_live_multiplier(
        self, campaign_id: CampaignId, multiplier: BidMultiplier, seq: int
    ) -> MultiplierOutcome:
        with self._lock:
            entry = self._entry(campaign_id)
            seen = self._seen.setdefault(campaign_id, {})
            if seq in seen:
                if seen[seq] != multiplier:
                    raise ConflictingMultiplierSeq()
                return MultiplierOutcome.DUPLICATE
            seen[seq] = multiplier
            if seq <= entry.multiplier_seq:
                return MultiplierOutcome.STALE
            self._entries[campaign_id] = BookEntry(
                bidder=entry.bidder.with_multiplier(multiplier),
                account=entry.account,
                multiplier_seq=seq,
            )
            self.version += 1
            return MultiplierOutcome.APPLIED

    def reserve(self, key: str, holds: dict[CampaignId, Money], *, at: datetime) -> None:
        with self._lock:
            for campaign_id, amount in holds.items():
                self._update(campaign_id, lambda account: account.reserve(key, amount, at=at))
            self._expire(at)

    def settle(self, key: str, charges: dict[CampaignId, Money]) -> None:
        with self._lock:
            for campaign_id, charge in charges.items():
                self._update(campaign_id, lambda account: account.settle(key, charge))

    def release(self, key: str) -> None:
        with self._lock:
            for campaign_id in list(self._entries):
                self._update(campaign_id, lambda account: account.release(key))

    def _expire(self, now: datetime) -> None:
        for campaign_id in list(self._entries):
            self._update(campaign_id, lambda account: account.expire(before=now - RESERVATION_TTL))

    def _update(self, campaign_id: CampaignId, change: Any) -> None:
        entry = self._entry(campaign_id)
        if entry.account is None:
            return
        account = change(entry.account)
        if account is not entry.account:
            self._entries[campaign_id] = BookEntry(
                bidder=entry.bidder, account=account, multiplier_seq=entry.multiplier_seq
            )
            self.version += 1

    def _entry(self, campaign_id: CampaignId) -> BookEntry:
        entry = self._entries.get(campaign_id)
        if entry is None:
            raise UnknownCampaign()
        return entry

    @staticmethod
    def _bidder(entry: BookEntry) -> Bidder:
        bidder = entry.bidder
        if entry.account is None:
            return bidder
        return Bidder(
            campaign_id=bidder.campaign_id,
            brand_id=bidder.brand_id,
            category=bidder.category,
            age_restricted=bidder.age_restricted,
            segment_bids=bidder.segment_bids,
            partners=bidder.partners,
            basket_bonuses=bidder.basket_bonuses,
            live_multiplier=bidder.live_multiplier,
            active=bidder.active,
            budget_remaining=entry.account.remaining,
        )
