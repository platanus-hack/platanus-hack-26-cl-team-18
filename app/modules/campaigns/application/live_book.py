"""The in-memory campaign book the auction reads on the hot path (R-B4, R-B5, R-B7).

Thread-safe: HTTP handlers (bidding war), the router thread (auctions) and
delivery (charges) all touch it. Postgres is the record, not the hot read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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


type MultiplierRecorder = Callable[[CampaignId, BidMultiplier, int], None]


class LiveCampaignBook:
    """`budget_remaining` on a bidder is its budget; `spent` and `multiplier_seqs` restore
    what a previous process already booked (the campaign registry). `on_multiplier` hears
    every applied multiplier, under the book's lock, so it must never block."""

    def __init__(
        self,
        bidders: Iterable[Bidder],
        *,
        spent: Mapping[CampaignId, Money] | None = None,
        multiplier_seqs: Mapping[CampaignId, int] | None = None,
        on_multiplier: MultiplierRecorder | None = None,
    ) -> None:
        self._lock = Lock()
        self._entries: dict[CampaignId, BookEntry] = {}
        self._seen: dict[CampaignId, dict[int, BidMultiplier]] = {}
        self._on_multiplier = on_multiplier
        self.version = 0
        spent = spent or {}
        multiplier_seqs = multiplier_seqs or {}
        for bidder in bidders:
            account = None
            if bidder.budget_remaining is not None:
                budget = bidder.budget_remaining
                account = BudgetAccount(
                    campaign_id=bidder.campaign_id,
                    budget=budget,
                    spent=spent.get(bidder.campaign_id, Money.zero(currency=budget.currency)),
                )
            self._entries[bidder.campaign_id] = BookEntry(
                bidder=bidder,
                account=account,
                multiplier_seq=multiplier_seqs.get(bidder.campaign_id, 0),
            )

    def snapshot(self, *, excluding_hold: str | None = None) -> tuple[Bidder, ...]:
        """Immutable bidders for one auction: live multiplier and budget left applied.

        `excluding_hold` is the on-air creative: a re-auction replaces that play, so
        its own hold must not price its winners out of the very screen they hold.
        """
        with self._lock:
            return tuple(self._bidder(entry, excluding_hold) for entry in self._entries.values())

    def charge(self, hold_key: str, campaign_id: CampaignId, amount: Money) -> Money:
        """Atomically cap a charge at the budget left and book it (settling the hold)."""
        return self.charge_many(hold_key, {campaign_id: amount})[campaign_id]

    def charge_many(
        self, hold_key: str, amounts: dict[CampaignId, Money]
    ) -> dict[CampaignId, Money]:
        """Validate and book every basket member together, or leave all balances unchanged."""
        with self._lock:
            booked: dict[CampaignId, Money] = {}
            updated: dict[CampaignId, BudgetAccount] = {}
            for campaign_id, amount in amounts.items():
                entry = self._entry(campaign_id)
                if amount.amount < 0:
                    raise ValueError("Charges cannot be negative")
                if entry.account is None:
                    booked[campaign_id] = amount
                else:
                    capped = entry.account.capped_charge(hold_key, amount)
                    booked[campaign_id] = capped
                    updated[campaign_id] = entry.account.settle(hold_key, capped)
            self._apply_accounts(updated)
            return booked

    def extend_hold(
        self, key: str, holds: dict[CampaignId, Money], *, at: datetime
    ) -> frozenset[CampaignId]:
        """Grow the hold `key` to `holds` where the budget allows (the on-air exposure lease).

        Returns the campaigns that can't cover it: their play must come down.
        """
        short: set[CampaignId] = set()
        with self._lock:
            updated: dict[CampaignId, BudgetAccount] = {}
            for campaign_id, amount in holds.items():
                account = self._entry(campaign_id).account
                if account is None:
                    continue
                # Validate and stage before publishing any member of the basket.
                extended = account.reserve(key, amount, at=at)
                if account.available_for(key) < amount:
                    short.add(campaign_id)
                    continue
                updated[campaign_id] = extended
            self._apply_accounts(updated)
        return frozenset(short)

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
            if self._on_multiplier is not None:
                self._on_multiplier(campaign_id, multiplier, seq)
            return MultiplierOutcome.APPLIED

    def reserve(self, key: str, holds: dict[CampaignId, Money], *, at: datetime) -> None:
        with self._lock:
            updated = {
                campaign_id: account.reserve(key, amount, at=at)
                for campaign_id, amount in holds.items()
                if (account := self._entry(campaign_id).account) is not None
            }
            self._apply_accounts(updated)
            self._expire(at)

    def settle(self, key: str, charges: dict[CampaignId, Money]) -> None:
        with self._lock:
            updated = {
                campaign_id: account.settle(key, charge)
                for campaign_id, charge in charges.items()
                if (account := self._entry(campaign_id).account) is not None
            }
            self._apply_accounts(updated)

    def _apply_accounts(self, accounts: dict[CampaignId, BudgetAccount]) -> None:
        """Publish a fully validated basket under the caller's lock."""
        for campaign_id, account in accounts.items():
            self._update(campaign_id, lambda current, updated=account: updated)

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
    def _bidder(entry: BookEntry, excluding_hold: str | None = None) -> Bidder:
        bidder = entry.bidder
        if entry.account is None:
            return bidder
        account = entry.account
        remaining = (
            account.remaining if excluding_hold is None else account.available_for(excluding_hold)
        )
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
            budget_remaining=remaining,
            broad_match=bidder.broad_match,
        )
